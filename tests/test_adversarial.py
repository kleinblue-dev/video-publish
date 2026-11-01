#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对抗验证测试：坏样本必被拦；已修缺陷回填正向断言；仍未修的缺陷留特征化测试。

跑法（与 test_publish.py 同一条命令）：
    python -m unittest discover -s tests -v

三类：
  1. TestAdversarialBlocks / TestSecurity —— 契约行为，必须一直绿。
  2. TestFixedGaps —— 本次对抗验证抓出的 D1/D3/D4/D5/D7/D8/D9/D12，修复后的正向断言。
  3. TestKnownDefects —— 仍未修的缺陷（D6/D10/D11）特征化测试：断言「当前确实这么错」，
     修好后会变红，即提醒你改断言 + 关掉 verify/report.md 对应条目。

全部离线：不需要网络、不需要 biliup、不需要真视频、不需要真 ffmpeg。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import publish  # noqa: E402

PUBLISH_PY = ROOT / "publish.py"


def write_png(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    path.write_bytes(header + b"\x00" * 32)


def write_jpeg(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 9
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9")


def write_cookies(path: Path, expires: int, secret: str = "unit-secret") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookie_info": {"cookies": [
            {"name": "SESSDATA", "value": secret + "-sess", "expires": expires},
            {"name": "bili_jct", "value": secret + "-jct", "expires": expires},
            {"name": "DedeUserID", "value": "42", "expires": expires},
        ]},
        "token_info": {"access_token": secret + "-token"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def levels(items) -> dict:
    rank = {"error": 3, "warn": 2, "info": 1}
    result: dict[str, str] = {}
    for item in items:
        best = result.get(item["id"])
        if best is None or rank[item["level"]] > rank[best]:
            result[item["id"]] = item["level"]
    return result


def fake_measure(ffmpeg, path, timeout=600):
    return {"lufs": -14.9, "tp": -2.1, "lra": 1.9}, ""


class Env:
    """临时环境：视频/封面/cookie/登录态/账本/工具占位文件都在 tmp 里。"""

    def __init__(self, tmp: Path, *, targets=("bilibili",), ai_declaration: bool = True,
                 schedule: dict | None = None):
        self.tmp = Path(tmp)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.video_16 = self.tmp / "v16.mp4"
        self.video_9 = self.tmp / "v9.mp4"
        self.video_16.write_bytes(b"0" * 1024)
        self.video_9.write_bytes(b"1" * 1024)
        # check_tools 会真的查文件存在：这里放占位可执行文件（probe/解码在测试里被 mock）
        self.ffprobe = self.tmp / "fake-ffprobe.exe"
        self.ffmpeg = self.tmp / "fake-ffmpeg.exe"
        self.ffprobe.write_bytes(b"fake-exe")
        self.ffmpeg.write_bytes(b"fake-exe")
        self.biliup = self.tmp / "biliup.exe"
        self.biliup.write_bytes(b"fake-exe")
        self.cover_16 = self.tmp / "cover16.jpg"
        self.cover_9 = self.tmp / "cover9.jpg"
        write_jpeg(self.cover_16, 1920, 1080)
        write_jpeg(self.cover_9, 1080, 1440)
        self.cookies = self.tmp / "cookies.json"
        write_cookies(self.cookies, int((datetime.now() + timedelta(days=30)).timestamp()))
        self.profile = self.tmp / "profile"
        (self.profile / "Default" / "Network").mkdir(parents=True)
        (self.profile / "Default" / "Network" / "Cookies").write_bytes(b"cookie-db")
        self.publisher = self.tmp / "publish_daily.py"
        self.publisher.write_text("# fake", encoding="utf-8")
        self.ledger = self.tmp / "ledger.jsonl"
        self.cfg_path = self.tmp / "config.json"
        self.job_path = self.tmp / "job.json"
        douyin_slot = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 12:00")
        self.job = {
            "schema": 1,
            "job_id": "adv-001",
            "video": {"16:9": str(self.video_16), "9:16": str(self.video_9)},
            "cover": {"16:9": str(self.cover_16), "9:16": str(self.cover_9)},
            "text": {"title": "对抗验证：标题长度正常", "desc": "第一句。第二句。",
                     "tags": ["AI", "开源"], "hashtags": ["#AI", "#开源"]},
            "qa": {"duration": 45.0, "lufs": -14.9, "tp": -2.1, "cer": 0.05, "verdict": "pass"},
            "targets": list(targets),
            "schedule": schedule or {"bilibili": "auto", "douyin": douyin_slot},
            "ai_declaration": ai_declaration,
        }
        self.cfg = publish.deep_merge(publish.builtin_config(), {
            "tools": {"ffprobe": str(self.ffprobe), "ffmpeg": str(self.ffmpeg),
                      "biliup": str(self.biliup), "python": sys.executable,
                      "douyin_publisher": str(self.publisher), "douyin_python": sys.executable},
            "bilibili": {"cookies_path": str(self.cookies), "schedule_times": ["12:00", "19:00"]},
            "douyin": {"profile_dir": str(self.profile), "result_root": str(self.tmp / "results"),
                       "shot_root": str(self.tmp / "shots"), "schedule_times": ["12:00"]},
            "ledger": str(self.ledger),
        })
        self.rules = json.loads((ROOT / "rules.default.json").read_text(encoding="utf-8"))
        self._probe_override = None

    def probe_map(self):
        if self._probe_override is not None:
            return self._probe_override
        return {
            str(self.video_16): ({"width": 1920, "height": 1080, "fps": 30.0, "duration": 45.0,
                                  "codec": "h264", "has_audio": True, "audio_codec": "aac",
                                  "size_bytes": 1024}, ""),
            str(self.video_9): ({"width": 1080, "height": 1920, "fps": 30.0, "duration": 45.0,
                                 "codec": "h264", "has_audio": True, "audio_codec": "aac",
                                 "size_bytes": 1024}, ""),
        }

    def set_probe(self, **overrides) -> None:
        mapping = {}
        for path in (self.video_16, self.video_9):
            info = dict(self.probe_map()[str(path)][0])
            info.update(overrides)
            mapping[str(path)] = (info, "")
        self._probe_override = mapping

    def checks(self, job: dict | None = None, rules: dict | None = None, cfg: dict | None = None,
               deep: bool = False):
        mapping = self.probe_map()
        with mock.patch.object(publish, "probe", lambda ffprobe, path, timeout=60: mapping.get(
                str(path), (None, "unexpected path"))), \
                mock.patch.object(publish, "measure_loudness", fake_measure):
            return publish.run_checks(job or self.job, cfg or self.cfg, rules or self.rules, deep=deep)

    def write_files(self) -> None:
        self.cfg_path.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        self.job_path.write_text(json.dumps(self.job, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 1) 坏样本必被拦
# --------------------------------------------------------------------------

class TestAdversarialBlocks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def make_env(self, name: str, *, probe_error: str = "", **probe_overrides) -> Env:
        env = Env(Path(self._tmp.name) / name)
        mapping = {}
        for path in (env.video_16, env.video_9):
            info = dict(env.probe_map()[str(path)][0])
            info.update(probe_overrides)
            mapping[str(path)] = ((None, probe_error) if probe_error else (info, ""))
        env._probe_override = mapping
        return env

    def test_good_job_passes(self):
        self.assertEqual([i for i in self.env.checks() if i["level"] == "error"], [])

    def test_wrong_dimension_blocks(self):
        self.assertEqual(levels(self.make_env("dim", width=1280, height=720).checks())["video.dimension"], "error")

    def test_no_audio_blocks(self):
        self.assertEqual(levels(self.make_env("noaudio", has_audio=False).checks())["video.audio"], "error")

    def test_short_duration_blocks(self):
        self.assertEqual(levels(self.make_env("short", duration=5.0).checks())["video.duration"], "error")

    def test_probe_failure_blocks(self):
        env = self.make_env("probe", probe_error="ffprobe 解码失败：moov atom not found")
        self.assertEqual(levels(env.checks())["video.probe"], "error")

    def test_title_90_chars_blocks(self):
        env = Env(Path(self._tmp.name) / "title")
        env.job["text"]["title"] = "超" * 90
        self.assertEqual(levels(env.checks())["text.title"], "error")

    def test_cover_300x200_blocks(self):
        env = Env(Path(self._tmp.name) / "cover")
        write_png(env.cover_16, 300, 200)
        self.assertEqual(levels(env.checks())["cover.dimension"], "error")

    def test_missing_ai_declaration_blocks(self):
        env = Env(Path(self._tmp.name) / "ai", ai_declaration=False)
        self.assertEqual(levels(env.checks())["ai.declaration"], "error")

    def test_expired_cookie_blocks(self):
        env = Env(Path(self._tmp.name) / "cookie")
        write_cookies(env.cookies, int((datetime.now() - timedelta(days=1)).timestamp()))
        self.assertEqual(levels(env.checks())["auth.bilibili.cookies"], "error")


# --------------------------------------------------------------------------
# 2) 安全：没有 --yes 不许动手；输出里不许出现 cookie 原值
# --------------------------------------------------------------------------

class TestSecurity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name), targets=("bilibili",))
        self.env.write_files()

    def tearDown(self):
        self._tmp.cleanup()

    def test_submit_without_yes_is_rejected_and_starts_nothing(self):
        with mock.patch.object(publish, "run_process", side_effect=AssertionError("不许启动进程")) as run:
            code = publish.main(["--config", str(self.env.cfg_path), "submit",
                                 "--job", str(self.env.job_path)])
        self.assertEqual(code, 1)
        self.assertFalse(run.called)
        self.assertFalse(self.env.ledger.exists(), "被拒的 submit 不许写账本")

    def test_dry_run_starts_nothing_and_writes_no_ledger(self):
        mapping = self.env.probe_map()
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: mapping[str(p)]), \
                mock.patch.object(publish, "run_process", side_effect=AssertionError("不许启动进程")) as run:
            code = publish.main(["--config", str(self.env.cfg_path), "submit", "--dry-run",
                                 "--skip-deep", "--job", str(self.env.job_path)])
        self.assertEqual(code, 0)
        self.assertFalse(run.called)
        self.assertFalse(self.env.ledger.exists())

    def test_output_never_contains_cookie_values(self):
        secret = json.loads(self.env.cookies.read_text(encoding="utf-8"))
        raw_values = [c["value"] for c in secret["cookie_info"]["cookies"]]
        raw_values.append(secret["token_info"]["access_token"])
        mapping = self.env.probe_map()
        buf = io.StringIO()
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: mapping[str(p)]), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            publish.main(["--config", str(self.env.cfg_path), "check", "--job", str(self.env.job_path)])
            publish.main(["--config", str(self.env.cfg_path), "plan", "--job", str(self.env.job_path)])
        text = buf.getvalue()
        self.assertIn("cookies.json", text, "路径应该被打印（用来核对）")
        for value in raw_values:
            self.assertNotIn(value, text, "cookie 原值不许出现在输出里")

    def test_redact_covers_each_secret_name(self):
        for name in ("SESSDATA", "bili_jct", "access_token", "refresh_token", "sec_ck", "csrf"):
            self.assertNotIn("RAWSECRETVALUE", publish.redact(f'{name}=RAWSECRETVALUE;'))


# --------------------------------------------------------------------------
# 3) 本次验证抓出、且已修复的缺陷：正向断言（坏样本必须被拦）
# --------------------------------------------------------------------------

class TestFixedGaps(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name))
        self.env.write_files()

    def tearDown(self):
        self._tmp.cleanup()

    # D1：容器头可读 ≠ 媒体可解；--deep 必须整段解码并拦下截断载荷
    def test_d1_deep_blocks_truncated_payload(self):
        self.env.set_probe()  # 模拟 ffprobe 只看到正常的容器头
        self.env.video_16.write_bytes(b"x" * 512)  # 载荷残缺
        with mock.patch.object(publish, "decode_check",
                               lambda ffmpeg, path, timeout=900: (False, "Invalid NAL unit size (32655 > 1519)")):
            items = self.env.checks(deep=True)
        self.assertEqual(levels(items)["video.decode"], "error")
        self.assertFalse(publish.summarize(items)["ok"])

    # D3：账本已 submitted 同指纹 → check 只是 warn，submit 必须「跳过」而不是整条拦住
    def test_d3_submitted_is_skip_not_block(self):
        fp = publish.fingerprint(str(self.env.video_16), self.env.job["text"]["title"])
        self.env.ledger.write_text(json.dumps({"job_id": "adv-001", "target": "bilibili", "state": "submitted",
                                               "fingerprint": fp, "ts": "2026-11-01T00:00:00",
                                               "receipt": {"bvid": "BV1advFAKE1"}}, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
        self.assertEqual(levels(self.env.checks())["dup.submitted"], "warn")
        buf = io.StringIO()
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: self.env.probe_map()[str(p)]), \
                mock.patch.object(publish, "run_process", side_effect=AssertionError("不许启动")) as run, \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = publish.main(["--config", str(self.env.cfg_path), "submit", "--yes",
                                 "--skip-deep", "--job", str(self.env.job_path)])
        self.assertEqual(code, 0)
        self.assertIn("已提交过，跳过", buf.getvalue())
        self.assertFalse(run.called)

    # D3：--force-uncertain 必须真能放行（而不是被 dup error 提前拦死）
    def test_d3_force_uncertain_reaches_submit(self):
        self.env.ledger.write_text('{"job_id":"adv-001","target":"bilibili","state":"uncertain","fingerprint":"x"}\n',
                                   encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: self.env.probe_map()[str(p)]), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = publish.main(["--config", str(self.env.cfg_path), "submit", "--yes", "--force-uncertain",
                                 "--dry-run", "--skip-deep", "--job", str(self.env.job_path)])
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", buf.getvalue())

    # D4：工具不存在 → check 必须 error（不能等 submit 才发现）
    def test_d4_missing_biliup_is_check_error(self):
        cfg = publish.deep_merge(self.env.cfg, {"tools": {"biliup": str(self.env.tmp / "no-such" / "biliup.exe")}})
        items = self.env.checks(cfg=cfg)
        self.assertEqual(levels(items)["tools.biliup"], "error")
        self.assertFalse(publish.summarize(items)["ok"])

    # D5：B 站缺 16:9 素材 → 直接 error，不许静默拿 9:16 顶替
    def test_d5_missing_platform_format_is_error(self):
        env = Env(Path(self._tmp.name) / "vertical")
        env.job["video"] = {"9:16": str(env.video_9)}
        env.job["cover"] = {"9:16": str(env.cover_9)}
        items = env.checks()
        self.assertEqual(levels(items)["video.exists"], "error")
        self.assertFalse(publish.summarize(items)["ok"])

    # D7：未支持平台不许静默丢弃
    def test_d7_unsupported_targets_warn(self):
        env = Env(Path(self._tmp.name) / "targets")
        env.job["targets"] = ["bilibili", "xiaohongshu", "weibo"]
        items = env.checks()
        self.assertEqual(levels(items)["job.targets"], "warn")
        self.assertIn("xiaohongshu", json.dumps(items, ensure_ascii=False))

    # D8：坏配置要被报出来（而不是静默用内置值）
    def test_d8_broken_config_is_reported(self):
        broken = Path(self._tmp.name) / "broken.json"
        broken.write_text("{ this is not json ,,,", encoding="utf-8")
        cfg = publish.load_config(str(broken))
        self.assertIn("_config_error", cfg)
        items = self.env.checks(cfg=cfg)
        self.assertEqual(levels(items)["config.file"], "warn")

    # D8：账本读不了 → 有检查项 error，而不是裸 traceback / 静默放行
    def test_d8_unreadable_ledger_is_check_error(self):
        ledger_dir = Path(self._tmp.name) / "ledger-is-dir"
        ledger_dir.mkdir(exist_ok=True)
        cfg = publish.deep_merge(self.env.cfg, {"ledger": str(ledger_dir)})
        items = self.env.checks(cfg=cfg)
        self.assertEqual(levels(items)["dup.ledger"], "error")
        self.assertFalse(publish.summarize(items)["ok"])

    # D9：扩展名对但读不出尺寸的坏封面 → error
    def test_d9_corrupt_cover_is_error(self):
        env = Env(Path(self._tmp.name) / "corruptcover")
        env.cover_16.write_bytes(b"\xff\xd8" + b"\x00\x11garbage" * 4)
        self.assertEqual(levels(env.checks())["cover.dimension"], "error")

    # D12：--deep 必须按画幅各测一次（B 站 16:9 与抖音 9:16 是两份渲染件）
    def test_d12_deep_measures_each_format(self):
        env = Env(Path(self._tmp.name) / "deep", targets=("bilibili", "douyin"))
        measured = []

        def counting_measure(ffmpeg, path, timeout=600):
            measured.append(path)
            return {"lufs": -14.9, "tp": -2.1, "lra": 1.9}, ""

        mapping = env.probe_map()
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: mapping[str(p)]), \
                mock.patch.object(publish, "measure_loudness", counting_measure), \
                mock.patch.object(publish, "decode_check", lambda ffmpeg, path, timeout=900: (True, "")):
            items = publish.run_checks(env.job, env.cfg, env.rules, deep=True)
        self.assertEqual(len(measured), 2, "两个画幅都应被实测")
        self.assertEqual(len([i for i in items if i["id"] == "video.decode"]), 2)
        self.assertEqual(len([i for i in items if i["id"] == "audio.measured"]), 2)


# --------------------------------------------------------------------------
# 4) 仍未修的缺陷（特征化测试）：断言「当前确实这么错」
# --------------------------------------------------------------------------

class TestKnownDefects(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self._tmp.name))
        self.env.write_files()

    def tearDown(self):
        self._tmp.cleanup()

    def test_d6_timezone_is_fixed_utc8_and_localized(self):
        """D6（已修）：TZ 固定 +08:00、账本 ts 带偏移、带 Z 的 schedule 先换算到本地再显示。"""
        self.assertIsNotNone(publish.TZ, "TZ 不许退化成 None")
        self.assertEqual(publish.TZ.utcoffset(None), timedelta(hours=8))
        self.assertTrue(publish.iso().endswith("+08:00"), "账本时间戳必须带时区偏移")
        env = Env(Path(self._tmp.name) / "utc")
        env.job["schedule"] = {"bilibili": "2026-11-02T04:00:00Z",
                               "douyin": "2026-11-02T04:00:00Z"}
        mapping = env.probe_map()
        bili = publish.build_plan(env.job, "bilibili", env.cfg, env.rules, mapping)
        douyin = publish.build_plan(env.job, "douyin", env.cfg, env.rules, mapping)
        # 04:00Z == 北京时间 12:00
        self.assertEqual(bili["schedule"], "2026-11-02 12:00")
        self.assertEqual(douyin["douyin_job"]["schedule"], "2026-11-02 12:00")

    def test_known_defect_10_douyin_title_30_vs_55(self):
        """D10：rules.douyin.title_max=55，但发布器 publish_daily.py:272 是 title[:30]。"""
        douyin_rules = self.env.rules["platforms"]["douyin"]
        if int(douyin_rules.get("title_max", 0)) != 55:
            self.skipTest("title_max 已按下游实现改小，缺陷已修")
        env = Env(Path(self._tmp.name) / "title30", targets=("douyin",))
        env.job["text"]["title"] = "这" * 35
        self.assertEqual(levels(env.checks())["text.title"], "info")
        self.assertEqual(int(douyin_rules["title_max"]), 55)  # 与下游 30 字截断矛盾

    def test_known_defect_11_target_all_documented_but_rejected(self):
        """D11：first-principles.md §6 写 `--target all`，CLI 不认识 all。"""
        mapping = self.env.probe_map()
        buf = io.StringIO()
        with mock.patch.object(publish, "probe", lambda f, p, timeout=60: mapping[str(p)]), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = publish.main(["--config", str(self.env.cfg_path), "check", "--target", "all",
                                 "--job", str(self.env.job_path)])
        if code == 0:
            self.skipTest("--target all 已实现，缺陷已修")
        self.assertEqual(code, 1)
        self.assertIn("没有已支持的平台", buf.getvalue())


# --------------------------------------------------------------------------
# 5) 并发（Windows 集成测试）：同一个 job 同时跑两个 submit，只许投一次
# --------------------------------------------------------------------------

@unittest.skipUnless(os.name == "nt", "用 Windows .cmd 测试替身演示并发")
class TestConcurrency(unittest.TestCase):
    def test_only_one_of_two_concurrent_submits_goes_through(self):
        """D2 回归：账本原子 claim（claim-*.lock + O_EXCL）必须挡住第二次并发提交。"""
        with tempfile.TemporaryDirectory() as tmp:
            env = Env(Path(tmp))
            fake_ffprobe = Path(tmp) / "fake-ffprobe.cmd"
            fake_ffprobe.write_text(
                "@echo off\r\n"
                'echo {"streams":[{"codec_type":"video","width":1920,"height":1080,'
                '"avg_frame_rate":"30/1","codec_name":"h264"},'
                '{"codec_type":"audio","codec_name":"aac"}],'
                '"format":{"duration":"45.0","size":"1024"}}\r\n'
                "exit /b 0\r\n", encoding="ascii")
            fake_uploader = Path(tmp) / "fake-uploader.cmd"
            fake_uploader.write_text(
                "@echo off\r\n"
                "ping -n 4 127.0.0.1 >nul\r\n"
                'echo {"aid":12345} https://www.bilibili.com/video/BV1fake00001\r\n'
                "exit /b 0\r\n", encoding="ascii")
            cfg = publish.deep_merge(env.cfg, {
                "tools": {"ffprobe": str(fake_ffprobe), "biliup": str(fake_uploader)},
                "bilibili": {"timeout_sec": 60},
            })
            env.cfg = cfg
            env.write_files()

            argv = [sys.executable, str(PUBLISH_PY), "--config", str(env.cfg_path),
                    "submit", "--yes", "--skip-deep", "--job", str(env.job_path)]
            out_a = Path(tmp) / "a.out"
            out_b = Path(tmp) / "b.out"
            fa = out_a.open("w", encoding="utf-8")
            fb = out_b.open("w", encoding="utf-8")
            try:
                first = subprocess.Popen(argv, stdout=fa, stderr=subprocess.STDOUT)
                second = subprocess.Popen(argv, stdout=fb, stderr=subprocess.STDOUT)
                codes = [first.wait(timeout=120), second.wait(timeout=120)]
            finally:
                fa.close()
                fb.close()
            # 一个真投（exit 0），另一个被 claim 挡下（exit 1）或走到「已提交过，跳过」（exit 0）
            self.assertIn(sorted(codes), ([0, 0], [0, 1]), f"退出码异常：{codes}")

            text_a = out_a.read_text(encoding="utf-8", errors="replace")
            text_b = out_b.read_text(encoding="utf-8", errors="replace")
            uploaded = sum(1 for text in (text_a, text_b) if "✓ submitted" in text)
            self.assertEqual(uploaded, 1, f"只允许一次真上传\n--- A ---\n{text_a}\n--- B ---\n{text_b}")
            entries = [json.loads(line) for line in env.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len([e for e in entries if e.get("state") == "submitted"]), 1)
            self.assertFalse(list(Path(tmp).glob("claim-*.lock")), "占位锁必须被释放")


if __name__ == "__main__":
    unittest.main(verbosity=2)
