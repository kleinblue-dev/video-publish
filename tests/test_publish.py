#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线单元测试：不需要网络、不需要 biliup、不需要真视频。

跑法：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import publish  # noqa: E402


def write_png(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    path.write_bytes(header + b"\x00" * 32)


def write_jpeg(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 9
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9")


def write_cookies(path: Path, expires: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookie_info": {"cookies": [
            {"name": "SESSDATA", "value": "x", "expires": expires},
            {"name": "bili_jct", "value": "y", "expires": expires},
            {"name": "DedeUserID", "value": "z", "expires": expires},
        ]},
        "token_info": {"access_token": "secret-token"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class Env:
    """一个完整的临时环境：视频/封面/cookie/登录态/账本都在 tmp 里。"""

    def __init__(self, tmp: Path, ai_declaration: bool = True, qa: dict | None = None,
                 targets=("bilibili", "douyin"), title: str = "五万四千星的代码助手，真能替你写需求吗？",
                 schedule: dict | None = None):
        self.tmp = Path(tmp)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.video_16 = self.tmp / "v16.mp4"
        self.video_9 = self.tmp / "v9.mp4"
        self.video_16.write_bytes(b"0" * 1024)
        self.video_9.write_bytes(b"1" * 1024)
        self.biliup = self.tmp / "biliup.exe"
        self.biliup.write_bytes(b"fake-exe")
        self.ffprobe = self.tmp / "ffprobe.exe"
        self.ffprobe.write_bytes(b"fake-exe")
        self.cover_16 = tmp / "cover16.jpg"
        self.cover_9 = tmp / "cover9.jpg"
        write_jpeg(self.cover_16, 1920, 1080)
        write_jpeg(self.cover_9, 1080, 1440)
        self.cookies = tmp / "cookies.json"
        write_cookies(self.cookies, int((datetime.now() + timedelta(days=30)).timestamp()))
        self.profile = tmp / "profile"
        (self.profile / "Default" / "Network").mkdir(parents=True)
        (self.profile / "Default" / "Network" / "Cookies").write_bytes(b"cookie-db")
        self.publisher = tmp / "publish_daily.py"
        self.publisher.write_text("# fake", encoding="utf-8")
        self.ledger = tmp / "ledger.jsonl"
        # 抖音定时必须落在 [now+2h, now+14d]：用「今天往后 3 天的 12:00」保证稳定合法。
        douyin_slot = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 12:00")
        self.job = {
            "schema": 1,
            "job_id": "unit-001",
            "video": {"16:9": str(self.video_16), "9:16": str(self.video_9)},
            "cover": {"16:9": str(self.cover_16), "9:16": str(self.cover_9)},
            "text": {"title": title, "desc": "第一句。第二句。", "tags": ["AI", "开源"],
                     "hashtags": ["#AI", "#开源"]},
            "qa": qa if qa is not None else {"duration": 45.0, "lufs": -14.9, "tp": -2.1,
                                             "cer": 0.05, "verdict": "pass"},
            "targets": list(targets),
            "schedule": schedule or {"bilibili": "auto", "douyin": douyin_slot},
            "ai_declaration": ai_declaration,
        }
        self.cfg = publish.deep_merge(publish.builtin_config(), {
            "tools": {"ffprobe": str(self.ffprobe), "biliup": str(self.biliup),
                      "python": sys.executable, "douyin_publisher": str(self.publisher),
                      "douyin_python": sys.executable},
            "bilibili": {"cookies_path": str(self.cookies), "schedule_times": ["12:00", "19:00"]},
            "douyin": {"profile_dir": str(self.profile), "result_root": str(tmp / "results"),
                       "shot_root": str(tmp / "shots"), "schedule_times": ["12:00"]},
            "ledger": str(self.ledger),
        })
        self.rules = json.loads((ROOT / "rules.default.json").read_text(encoding="utf-8"))

    def probe_map(self):
        return {
            str(self.video_16): ({"width": 1920, "height": 1080, "fps": 30.0, "duration": 45.0,
                                  "codec": "h264", "has_audio": True, "audio_codec": "aac",
                                  "size_bytes": 1024}, ""),
            str(self.video_9): ({"width": 1080, "height": 1920, "fps": 30.0, "duration": 45.0,
                                 "codec": "h264", "has_audio": True, "audio_codec": "aac",
                                 "size_bytes": 1024}, ""),
        }

    def checks(self, job: dict | None = None, rules: dict | None = None):
        with mock.patch.object(publish, "probe", lambda ffprobe, path, timeout=60: self.probe_map().get(
                str(path), (None, "unexpected path"))):
            return publish.run_checks(job or self.job, self.cfg, rules or self.rules)


class TestBasics(unittest.TestCase):
    def test_redact_hides_cookie_values(self):
        text = 'SESSDATA=abc%2Cdef; bili_jct="1234"; access_token: zzz'
        out = publish.redact(text)
        self.assertNotIn("abc%2Cdef", out)
        self.assertNotIn("1234", out)
        self.assertNotIn("zzz", out)
        self.assertIn("<redacted>", out)

    def test_format_aliases(self):
        for raw in ("16x9", "16:9", "169", "横屏"):
            self.assertEqual(publish.norm_format(raw), "16:9")
        for raw in ("9x16", "9:16", "916", "竖屏"):
            self.assertEqual(publish.norm_format(raw), "9:16")
        self.assertIsNone(publish.norm_format("4:3"))

    def test_image_size_png_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "a.png"
            write_png(png, 1080, 1440)
            self.assertEqual(publish.image_size(png), (1080, 1440))
            jpg = Path(tmp) / "b.jpg"
            write_jpeg(jpg, 1920, 1080)
            self.assertEqual(publish.image_size(jpg), (1920, 1080))

    def test_next_slots_respects_lead_time(self):
        now = datetime(2026, 11, 1, 20, 0)  # naive，与 min_lead 语义一致
        slots = publish.next_slots(["12:00", "19:00"], 1, timedelta(hours=4.05), at=now)
        self.assertEqual(len(slots), 1)
        self.assertGreaterEqual(slots[0] - now, timedelta(hours=4))
        self.assertEqual(slots[0].hour, 12)  # 明天 12:00

    def test_parse_time_formats(self):
        self.assertIsNotNone(publish.parse_time("2026-11-02 12:00"))
        self.assertIsNotNone(publish.parse_time("2026-11-02T12:00:00"))
        self.assertIsNone(publish.parse_time("不是时间"))


class TestChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def levels(self, items):
        """同一检查项在两个平台各出现一次时，取更严重的那个（error > warn > info）。"""
        rank = {"error": 3, "warn": 2, "info": 1}
        result: dict[str, str] = {}
        for item in items:
            best = result.get(item["id"])
            if best is None or rank[item["level"]] > rank[best]:
                result[item["id"]] = item["level"]
        return result

    def test_good_job_passes(self):
        items = self.env.checks()
        self.assertEqual([i for i in items if i["level"] == "error"], [])

    def test_title_too_long_blocks(self):
        environ = Env(Path(self._tmp.name) / "t2", title="超" * 90)
        levels = self.levels(environ.checks())
        self.assertEqual(levels["text.title"], "error")

    def test_missing_video_blocks(self):
        environ = Env(Path(self._tmp.name) / "t3")
        environ.job["video"] = {"16:9": str(environ.tmp / "nope.mp4"), "9:16": str(environ.tmp / "nope.mp4")}
        levels = self.levels(environ.checks())
        self.assertEqual(levels["video.exists"], "error")

    def test_duration_out_of_range_blocks(self):
        environ = Env(Path(self._tmp.name) / "t4", qa={"duration": 12.0, "lufs": -14.0, "tp": -2.0, "cer": 0.02,
                                                       "verdict": "pass"})
        with mock.patch.object(publish, "probe", lambda ffprobe, path, timeout=60: (
                {**environ.probe_map()[str(path)][0], "duration": 12.0}, "")):
            items = publish.run_checks(environ.job, environ.cfg, environ.rules)
        self.assertEqual(self.levels(items)["video.duration"], "error")

    def test_qa_fail_blocks(self):
        environ = Env(Path(self._tmp.name) / "t5", qa={"duration": 45.0, "lufs": -14.0, "tp": -2.0,
                                                       "cer": 0.02, "verdict": "fail"})
        self.assertEqual(self.levels(environ.checks())["qa.verdict"], "error")

    def test_cer_zones(self):
        bad = Env(Path(self._tmp.name) / "t6a", qa={"duration": 45.0, "cer": 0.2, "verdict": "pass"})
        self.assertEqual(self.levels(bad.checks())["asr.cer"], "error")
        mid = Env(Path(self._tmp.name) / "t6b", qa={"duration": 45.0, "cer": 0.12, "verdict": "pass"})
        self.assertEqual(self.levels(mid.checks())["asr.cer"], "warn")

    def test_ai_declaration_required(self):
        environ = Env(Path(self._tmp.name) / "t7", ai_declaration=False)
        self.assertEqual(self.levels(environ.checks())["ai.declaration"], "error")

    def test_cover_too_small_blocks(self):
        environ = Env(Path(self._tmp.name) / "t8")
        write_png(environ.cover_16, 300, 200)
        self.assertEqual(self.levels(environ.checks())["cover.dimension"], "error")

    def test_bilibili_schedule_too_soon_blocks(self):
        environ = Env(Path(self._tmp.name) / "t9", schedule={"bilibili": "2026-01-01 12:00", "douyin": "2030-01-01 12:00"})
        self.assertEqual(self.levels(environ.checks())["schedule.valid"], "error")

    def test_duplicate_submission_blocks(self):
        """check 里只提示（warn），真正「跳过/拦截」在 submit 循环里做。"""
        environ = Env(Path(self._tmp.name) / "t10")
        fp = publish.fingerprint(str(environ.video_16), environ.job["text"]["title"])
        environ.ledger.write_text(json.dumps({"job_id": "unit-001", "target": "bilibili", "state": "submitted",
                                              "fingerprint": fp, "ts": "2026-11-01T00:00:00+08:00",
                                              "receipt": {"bvid": "BV1xx"}}) + "\n", encoding="utf-8")
        levels = self.levels(environ.checks())
        self.assertEqual(levels["dup.submitted"], "warn")

    def test_uncertain_blocks(self):
        environ = Env(Path(self._tmp.name) / "t11")
        environ.ledger.write_text(json.dumps({"job_id": "unit-001", "target": "douyin", "state": "uncertain",
                                              "fingerprint": "x", "ts": "2026-11-01T00:00:00+08:00"}) + "\n",
                                  encoding="utf-8")
        self.assertEqual(self.levels(environ.checks())["dup.uncertain"], "warn")

    def test_missing_tool_blocks_check(self):
        """预检说能发 = 工具必须真的在（历史上 check PASS 但 submit 才报 biliup 不存在）。"""
        environ = Env(Path(self._tmp.name) / "t13")
        cfg = publish.deep_merge(environ.cfg, {"tools": {"biliup": str(environ.tmp / "nope.exe")}})
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: environ.probe_map().get(str(p), (None, "x"))):
            items = publish.run_checks(environ.job, cfg, environ.rules)
        self.assertEqual(self.levels(items)["tools.biliup"], "error")

    def test_missing_expected_aspect_blocks(self):
        """只给了 9:16 却要投 B 站 16:9 → 直接拦，不静默换画幅。"""
        environ = Env(Path(self._tmp.name) / "t14")
        environ.job["video"] = {"9:16": str(environ.video_9)}
        environ.job["cover"] = {"9:16": str(environ.cover_9)}
        self.assertEqual(self.levels(environ.checks())["video.exists"], "error")

    def test_flat_video_path_expands_to_platform_defaults(self):
        environ = Env(Path(self._tmp.name) / "t15")
        raw = {"job_id": "flat", "video": str(environ.video_16), "cover": str(environ.cover_16),
               "text": {"title": "x"}, "targets": ["bilibili"]}
        path = environ.tmp / "flat.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        loaded = publish.load_job(path)
        self.assertEqual(loaded["video"].get("16:9"), str(environ.video_16))
        self.assertEqual(loaded["cover"].get("16:9"), str(environ.cover_16))

    def test_expired_cookie_blocks(self):
        environ = Env(Path(self._tmp.name) / "t12")
        write_cookies(environ.cookies, int((datetime.now() - timedelta(days=1)).timestamp()))
        self.assertEqual(self.levels(environ.checks())["auth.bilibili.cookies"], "error")


class TestPlan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_bilibili_argv_has_required_fields(self):
        plan = publish.build_plan(self.env.job, "bilibili", self.env.cfg, self.env.rules,
                                  {str(self.env.video_16): self.env.probe_map()[str(self.env.video_16)]})
        argv = plan["command"]
        self.assertIn("--tid", argv)
        self.assertEqual(argv[argv.index("--tid") + 1], "231")
        self.assertEqual(argv[argv.index("--copyright") + 1], "1")
        self.assertEqual(argv[argv.index("--no-reprint") + 1], "1")
        extra = json.loads(argv[argv.index("--extra-fields") + 1])
        self.assertEqual(extra["creation_statement"]["id"], 1)
        self.assertGreaterEqual(int(argv[argv.index("--dtime") + 1]), int((datetime.now() + timedelta(hours=4)).timestamp()))

    def test_douyin_job_has_schedule_and_hashtags(self):
        plan = publish.build_plan(self.env.job, "douyin", self.env.cfg, self.env.rules,
                                  {str(self.env.video_9): self.env.probe_map()[str(self.env.video_9)]})
        job = plan["douyin_job"]
        self.assertEqual(job["schedule"], self.env.job["schedule"]["douyin"])
        self.assertIn("#AI", job["desc"])
        self.assertEqual(job["slug"], "unit-001")


class TestSubmiters(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name))
        self.plan_bili = publish.build_plan(self.env.job, "bilibili", self.env.cfg, self.env.rules, {})
        self.plan_douyin = publish.build_plan(self.env.job, "douyin", self.env.cfg, self.env.rules, {})

    def tearDown(self):
        self._tmp.cleanup()

    def test_bilibili_success_parses_bvid(self):
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (
                0, 'submit ok {"aid":12345} https://www.bilibili.com/video/BV1abcDEFghi', True)):
            result = publish.submit_bilibili(self.env.job, self.plan_bili, self.env.cfg)
        self.assertEqual(result["state"], "submitted")
        self.assertEqual(result["receipt"]["bvid"], "BV1abcDEFghi")
        self.assertEqual(result["receipt"]["aid"], 12345)

    def test_bilibili_nonzero_is_uncertain(self):
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (1, "boom", True)):
            result = publish.submit_bilibili(self.env.job, self.plan_bili, self.env.cfg)
        self.assertEqual(result["state"], "uncertain")

    def test_bilibili_timeout_is_uncertain(self):
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (None, "", True)):
            result = publish.submit_bilibili(self.env.job, self.plan_bili, self.env.cfg)
        self.assertEqual(result["state"], "uncertain")

    def test_bilibili_missing_exe_is_failed(self):
        cfg = publish.deep_merge(self.env.cfg, {"tools": {"biliup": str(self.env.tmp / "missing.exe")}})
        result = publish.submit_bilibili(self.env.job, self.plan_bili, cfg)
        self.assertEqual(result["state"], "failed")

    def _douyin_result(self, **overrides):
        requested = self.plan_douyin["douyin_job"]["schedule"]
        payload = {"success": True, "scheduled": True, "ai_declared": True, "stage": "submitted",
                   "sha256": publish.sha256_file(self.env.video_9),
                   "finished_at": "2026-11-01T12:00:00",
                   "sched_value": requested, "in_manage": True,
                   "body_tail": "定时发布成功 作者声明：内容由AI生成 审核中"}
        payload.update(overrides)
        Path(self.plan_douyin["douyin_job"]["result"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.plan_douyin["douyin_job"]["result"]).write_text(json.dumps(payload), encoding="utf-8")

    def test_douyin_success(self):
        self._douyin_result()
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (0, "ok", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "submitted")

    def test_douyin_missing_ai_declaration_is_failed(self):
        self._douyin_result(ai_declared=False)
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (0, "ok", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "failed")
        self.assertIn("ai_declared", result["note"])

    def test_douyin_weak_ai_evidence_is_uncertain(self):
        self._douyin_result(body_tail="自主声明 请选择自主声明 定时发布成功")
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (0, "ok", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "uncertain")
        self.assertIn("强证据", result["note"])

    def test_douyin_sha_mismatch_is_uncertain(self):
        self._douyin_result(sha256="deadbeef")
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (0, "ok", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "uncertain")

    def test_douyin_missing_finished_at_is_uncertain(self):
        self._douyin_result()
        payload = json.loads(Path(self.plan_douyin["douyin_job"]["result"]).read_text(encoding="utf-8"))
        payload.pop("finished_at")
        Path(self.plan_douyin["douyin_job"]["result"]).write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (1, "crash", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "uncertain")

    def test_douyin_upload_timeout_is_uncertain(self):
        self._douyin_result(stage="upload_timeout", success=False, scheduled=False, ai_declared=False)
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (2, "timeout", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "uncertain")

    def test_douyin_no_video_is_failed(self):
        self._douyin_result(stage="no_video", success=False, scheduled=False, ai_declared=False)
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (2, "no video", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "failed")

    def test_douyin_ai_declaration_failed_exit4_is_failed(self):
        self._douyin_result(stage="ai_declaration_failed", success=False, ai_declared=False)
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (4, "ai failed", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "failed")

    def test_douyin_platform_reschedule_is_submitted_with_warning(self):
        self._douyin_result(sched_value="2030-01-01 02:30")
        with mock.patch.object(publish, "run_process", lambda argv, timeout, env_extra=None: (0, "ok", True)):
            result = publish.submit_douyin(self.env.job, self.plan_douyin, self.env.cfg)
        self.assertEqual(result["state"], "submitted")
        self.assertIn("改成了", result["note"])


class TestSubmitFlow(unittest.TestCase):
    """submit 的路由决策：跳过 / 拦 uncertain / 防双投占位 / 换素材重投。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name))
        self.job_path = self.env.tmp / "job.json"
        self.job_path.write_text(json.dumps(self.env.job, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, **overrides):
        base = dict(job=str(self.job_path), target="bilibili", deep=False, json=False, verbose=False,
                    yes=True, dry_run=True, force_uncertain=False, republish=False,
                    config="", command="submit")
        base.update(overrides)
        return argparse.Namespace(**base)

    def run_submit(self, **overrides):
        mapping = self.env.probe_map()
        buf = io.StringIO()
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: mapping.get(str(p), (None, "x"))), \
                mock.patch.object(publish, "run_process", side_effect=AssertionError("不该启动执行器")) as run, \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = publish.cmd_submit(self.args(**overrides), self.env.cfg, self.env.rules)
        return code, buf.getvalue(), run.called

    def seed(self, state: str, fingerprint_value: str = ""):
        fp = fingerprint_value or publish.fingerprint(str(self.env.video_16), self.env.job["text"]["title"])
        self.env.ledger.write_text(json.dumps({"job_id": self.env.job["job_id"], "target": "bilibili",
                                               "state": state, "fingerprint": fp,
                                               "ts": "2026-11-01T00:00:00+08:00"}) + "\n", encoding="utf-8")
        return fp

    def test_already_submitted_is_skipped_not_blocked(self):
        self.seed("submitted")
        code, out, called = self.run_submit()
        self.assertEqual(code, 0)
        self.assertIn("已提交过，跳过", out)
        self.assertFalse(called)

    def test_partial_run_can_resume(self):
        """B 站已发、抖音失败后重跑：B 站跳过，抖音照常尝试（历史坑：整条被拦死）。"""
        self.seed("submitted")
        code, out, _ = self.run_submit(target="bilibili,douyin")
        self.assertIn("已提交过，跳过", out)
        self.assertIn("[douyin] 提交", out)

    def test_uncertain_blocks_until_resolve(self):
        self.seed("uncertain")
        code, out, called = self.run_submit()
        self.assertEqual(code, 1)
        self.assertIn("先 publish.py resolve 结案", out)
        self.assertFalse(called)

    def test_force_uncertain_allows_retry(self):
        self.seed("uncertain")
        code, out, called = self.run_submit(force_uncertain=True)
        self.assertIn("[bilibili] 提交", out)
        self.assertNotIn("resolve 结案", out)

    def test_fresh_claim_blocks_concurrent_submit(self):
        claim, error = publish.acquire_claim(self.env.cfg, self.env.job["job_id"], "bilibili")
        self.assertIsNotNone(claim, error)
        try:
            code, out, called = self.run_submit(dry_run=False)
        finally:
            publish.release_claim(claim)
        self.assertEqual(code, 1)
        self.assertIn("另一个 submit 正在投", out)
        self.assertFalse(called)

    def test_different_material_requires_republish(self):
        self.seed("submitted", fingerprint_value="deadbeef")
        code, out, _ = self.run_submit()
        self.assertEqual(code, 1)
        self.assertIn("--republish", out)

    def test_dry_run_never_launches_uploader(self):
        code, out, called = self.run_submit(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)
        self.assertFalse(called)


class TestLedger(unittest.TestCase):
    def test_append_and_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Env(Path(tmp))
            publish.append_ledger(env.cfg, {"job_id": "a", "target": "bilibili", "state": "submitting"})
            publish.append_ledger(env.cfg, {"job_id": "a", "target": "bilibili", "state": "submitted"})
            self.assertEqual(publish.latest_entry(env.cfg, "a", "bilibili")["state"], "submitted")
            self.assertIsNone(publish.latest_entry(env.cfg, "a", "douyin"))

    def test_bad_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Env(Path(tmp))
            env.ledger.write_text('{"job_id":"a","target":"bilibili","state":"submitted"}\nnot-json\n',
                                  encoding="utf-8")
            self.assertEqual(len(publish.read_ledger(env.cfg)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
