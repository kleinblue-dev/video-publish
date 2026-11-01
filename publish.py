#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video-publish —— 一条成品视频 → 一份可核验的发布回执。

第一性原理（详见 references/first-principles.md）：
一次发布要成立，必须同时满足 5 个条件 ——
C1 素材可被平台接受 / C2 元数据合法 / C3 账号可写 / C4 提交唯一且幂等 / C5 结果可判定。
本工具把这 5 个条件全部前移到「0 网络、<3 秒」的本地检查，过了才允许提交。

命令：
  publish.py doctor                             环境自检（工具/凭据/登录态）
  publish.py check  --job job.json              零网络预检，逐条给通过/拦截
  publish.py plan   --job job.json              只打印将要提交的完整参数
  publish.py submit --job job.json --yes        真正提交（同一素材永不重投）
  publish.py status [--job job.json]            账本
  publish.py resolve --job job.json --target bilibili --state submitted --note "后台已确认"

退出码：0 通过/成功；1 有拦截或失败；2 环境或配置问题。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = 1
ROOT = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = ROOT / "rules.default.json"
CONFIG_ENV = "VIDEO_PUBLISH_CONFIG"
HOME = Path(os.path.expanduser("~"))
DEFAULT_CONFIG_PATH = HOME / ".video-publish" / "config.json"
DEFAULT_LEDGER_PATH = HOME / ".video-publish" / "ledger.jsonl"

try:  # Windows 上没有 tzdata 时退化为本机本地时间（本机就是 +08:00）
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    TZ = None

FORMAT_ALIASES = {
    "16:9": "16:9", "16x9": "16:9", "169": "16:9", "横屏": "16:9", "横": "16:9",
    "9:16": "9:16", "9x16": "9:16", "916": "9:16", "竖屏": "9:16", "竖": "9:16",
}
PLATFORM_FORMAT_DEFAULT = {"bilibili": "16:9", "douyin": "9:16"}
SECRET_RE = re.compile(
    r"(SESSDATA|bili_jct|DedeUserID|access_token|refresh_token|sec_ck|csrf)(\s*[:=]\s*[\"']?)([^\"',;\s}]+)",
    re.IGNORECASE,
)
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
AID_RE = re.compile(r'"aid"\s*:\s*(\d+)')


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------

def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def now() -> datetime:
    return datetime.now(TZ) if TZ else datetime.now()


def iso(ts: datetime | None = None) -> str:
    value = ts or now()
    return value.isoformat(timespec="seconds")


def redact(text: str) -> str:
    return SECRET_RE.sub(r"\1\2<redacted>", str(text or ""))


def read_json(path: Path, default=None):
    try:
        # utf-8-sig：PowerShell 5.1 的 `-Encoding UTF8` 会写 BOM，
        # 带 BOM 的 JSON 用 utf-8 读会让 json.loads 直接失败（历史上踩过）。
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path, limit_bytes: int = 0) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            if limit_bytes and digest.digest_size and handle.tell() >= limit_bytes:
                break
    return digest.hexdigest()


def image_size(path: Path):
    """不依赖 Pillow 读 png/jpeg/webp 尺寸，失败返回 None。"""
    try:
        data = Path(path).read_bytes()[:65536]
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0xD9):
                index += 2
                continue
            if index + 4 > len(data):
                break
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return int(width), int(height)
            if length < 2:
                break
            index += 2 + length
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return int(width), int(height)
        if kind == b"VP8 " and len(data) >= 30:
            width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return int(width), int(height)
        if kind == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def norm_format(value: str | None) -> str | None:
    if not value:
        return None
    key = str(value).strip()
    return FORMAT_ALIASES.get(key.lower(), FORMAT_ALIASES.get(key))


def parse_time(text: str) -> datetime | None:
    text = str(text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            moment = datetime.strptime(text, fmt)
            return moment.replace(tzinfo=TZ) if TZ else moment
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def next_slots(times, count: int, min_lead: timedelta, at: datetime | None = None):
    """按配置的每日时段，取未来 count 个满足最小提前量的时间点。"""
    current = at or now()
    parsed = []
    for value in times or []:
        try:
            hour, minute = str(value).split(":", 1)
            parsed.append(datetime(current.year, current.month, current.day, int(hour), int(minute),
                                   tzinfo=current.tzinfo))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return []
    earliest = current + min_lead
    found = []
    for day_offset in range(0, 8):
        day = earliest.date() + timedelta(days=day_offset)
        for moment in sorted(parsed):
            candidate = moment.replace(year=day.year, month=day.month, day=day.day)
            if candidate <= earliest:
                continue
            if found and candidate <= found[-1]:
                continue
            found.append(candidate)
            if len(found) >= count:
                return found
    return found


# --------------------------------------------------------------------------
# 配置与规则
# --------------------------------------------------------------------------

def _find_biliup() -> str:
    env = os.environ.get("BILIUP_PATH", "")
    if env and Path(env).exists():
        return env
    found = shutil.which("biliup") or shutil.which("biliup.exe")
    if found:
        return found
    for base in (Path("D:/2006L/biliup"), Path("C:/tools/biliup"), HOME / "biliup"):
        if not base.exists():
            continue
        for candidate in sorted(base.glob("**/biliup.exe"), reverse=True):
            return str(candidate)
    return ""


def _find_ffmpeg_bin(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_PATH", "")
    if env and Path(env).exists():
        return env
    found = shutil.which(name) or shutil.which(name + ".exe")
    if found:
        return found
    for base in (Path("D:/2006L/tools/ffmpeg/bin"), Path("C:/ffmpeg/bin")):
        candidate = base / (name + ".exe")
        if candidate.exists():
            return str(candidate)
    return ""


def builtin_config() -> dict:
    ffprobe = _find_ffmpeg_bin("ffprobe")
    ffmpeg = _find_ffmpeg_bin("ffmpeg") or (str(Path(ffprobe).with_name("ffmpeg.exe")) if ffprobe else "")
    biliup = _find_biliup()
    cookies = str(Path(biliup).parent / "cookies.json") if biliup else ""
    if not cookies or not Path(cookies).exists():
        for base in (Path("D:/2006L/biliup"), HOME / "biliup"):
            candidate = base / "cookies.json"
            if candidate.exists():
                cookies = str(candidate)
                break
    douyin_root = Path("D:/2006L/tools/douyin-auto")
    return {
        "schema": SCHEMA,
        "tools": {
            "ffprobe": ffprobe,
            "ffmpeg": ffmpeg,
            "biliup": biliup,
            "python": sys.executable,
            "douyin_publisher": str(douyin_root / "publish_daily.py") if (douyin_root / "publish_daily.py").exists() else "",
            "douyin_python": str(Path("E:/2006L/guichao/.venv/Scripts/python.exe"))
            if Path("E:/2006L/guichao/.venv/Scripts/python.exe").exists() else sys.executable,
        },
        "bilibili": {
            "cookies_path": cookies,
            "proxy": "",
            "schedule_times": ["12:00", "19:00"],
            "tid": 231,
            "submit": "client",
            "timeout_sec": 900,
        },
        "douyin": {
            "profile_dir": str(douyin_root / "profile"),
            "shot_root": str(douyin_root / "shots"),
            "result_root": str(douyin_root),
            "schedule_times": ["12:00", "19:00"],
            "timeout_sec": 900,
        },
        "ledger": str(DEFAULT_LEDGER_PATH),
        "rules": "",
    }


def load_config(path: str | None = None) -> dict:
    base = builtin_config()
    candidate = path or os.environ.get(CONFIG_ENV) or str(DEFAULT_CONFIG_PATH)
    override = read_json(Path(candidate), None) if Path(candidate).exists() else None
    if isinstance(override, dict):
        base = deep_merge(base, override)
        base["_config_path"] = str(candidate)
    else:
        base["_config_path"] = ""
    return base


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_rules(cfg: dict) -> dict:
    path = Path(cfg.get("rules") or DEFAULT_RULES_PATH)
    rules = read_json(path, None)
    if not isinstance(rules, dict):
        raise SystemExit(f"规则表读不到或不是 JSON：{path}")
    return rules


def cfg_get(cfg: dict, dotted: str, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# --------------------------------------------------------------------------
# job 载入（兼容 pixelcat / GTR 两种历史形态）
# --------------------------------------------------------------------------

def load_job(path: Path) -> dict:
    raw = read_json(Path(path), None)
    if not isinstance(raw, dict):
        raise SystemExit(f"job 不是合法 JSON：{path}")
    raw["_job_path"] = str(Path(path).resolve())
    raw.setdefault("job_id", Path(path).stem)
    targets = raw.get("targets") or ["bilibili"]
    if isinstance(targets, str):
        targets = [item.strip() for item in targets.split(",") if item.strip()]
    raw["targets"] = [str(item).strip().lower() for item in targets]

    video = raw.get("video")
    if isinstance(video, str):
        video = {}
    raw["video"] = {norm_format(k) or k: v for k, v in (video or {}).items()}
    cover = raw.get("cover")
    if isinstance(cover, str):
        cover = {}
    raw["cover"] = {norm_format(k) or k: v for k, v in (cover or {}).items()}
    text = raw.get("text") or {}
    raw["text"] = {
        "title": str(text.get("title") or raw.get("title") or "").strip(),
        "desc": str(text.get("desc") or raw.get("desc") or "").strip(),
        "tags": [str(tag) for tag in (text.get("tags") or raw.get("tags") or [])],
        "hashtags": [str(tag) for tag in (text.get("hashtags") or raw.get("hashtags") or [])],
    }
    schedule = raw.get("schedule")
    if isinstance(schedule, str):
        schedule = {target: schedule for target in raw["targets"]}
    raw["schedule"] = schedule or {}
    raw["ai_declaration"] = bool(raw.get("ai_declaration", False))
    raw["qa"] = raw.get("qa") or {}
    return raw


def asset_for(job: dict, kind: str, target: str) -> tuple[str, str]:
    """返回 (format, path)。缺省画幅按平台默认；显式给了别的画幅也接受。"""
    assets = job.get(kind) or {}
    default_fmt = PLATFORM_FORMAT_DEFAULT.get(target, "16:9")
    if default_fmt in assets and assets[default_fmt]:
        return default_fmt, str(assets[default_fmt])
    for fmt, value in assets.items():
        if value:
            return fmt, str(value)
    return default_fmt, ""


# --------------------------------------------------------------------------
# ffprobe
# --------------------------------------------------------------------------

def probe(ffprobe: str, path: str, timeout: int = 60) -> tuple[dict | None, str]:
    if not ffprobe or not Path(ffprobe).exists():
        return None, "ffprobe 不存在"
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "ffprobe 超时"
    except OSError as exc:
        return None, f"ffprobe 无法启动：{exc}"
    if result.returncode != 0:
        return None, f"ffprobe 解码失败：{redact(result.stderr)[-200:]}"
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError:
        return None, "ffprobe 输出不是 JSON"
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return None, "没有视频流"
    rate = str(video.get("avg_frame_rate") or "0/1")
    try:
        numerator, denominator = rate.split("/")
        fps = float(numerator) / float(denominator) if float(denominator) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    try:
        duration = float((payload.get("format") or {}).get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 2),
        "duration": round(duration, 2),
        "codec": video.get("codec_name") or "",
        "has_audio": audio is not None,
        "audio_codec": (audio or {}).get("codec_name") or "",
        "size_bytes": int((payload.get("format") or {}).get("size") or 0),
    }, ""


# --------------------------------------------------------------------------
# 检查项
# --------------------------------------------------------------------------

def check(item_id: str, level: str, detail: str, platform: str = "*", target: str = "") -> dict:
    return {"id": item_id, "level": level, "detail": detail, "platform": platform, "target": target}


def check_auth_bilibili(cfg: dict, rules: dict) -> list[dict]:
    path = Path(str(cfg_get(cfg, "bilibili.cookies_path", "") or ""))
    if not path.exists():
        return [check("auth.bilibili.cookies", "error", f"cookie 文件不存在：{path}", "bilibili")]
    data = read_json(path, None)
    if not isinstance(data, dict):
        return [check("auth.bilibili.cookies", "error", f"cookie 文件不是合法 JSON：{path}", "bilibili")]
    cookies = []
    info = data.get("cookie_info") or {}
    if isinstance(info, dict):
        cookies = info.get("cookies") or []
    if not cookies and isinstance(data.get("cookies"), list):
        cookies = data["cookies"]
    names = {str(item.get("name")) for item in cookies if isinstance(item, dict)}
    required = {"SESSDATA", "bili_jct"}
    missing = required - names
    if missing:
        return [check("auth.bilibili.cookies", "error", f"缺少关键字段：{','.join(sorted(missing))}", "bilibili")]
    expires = [int(item.get("expires") or 0) for item in cookies if isinstance(item, dict)]
    latest = max(expires) if expires else 0
    if latest and latest < 10_000_000_000:  # 秒
        deadline = datetime.fromtimestamp(latest, TZ) if TZ else datetime.fromtimestamp(latest)
        if deadline < now():
            return [check("auth.bilibili.cookies", "error",
                          f"cookie 已过期：{deadline:%Y-%m-%d %H:%M}", "bilibili")]
        remaining = (deadline - now()).days
        return [check("auth.bilibili.cookies", "warn" if remaining < 3 else "info",
                      f"cookie 有效至 {deadline:%Y-%m-%d}（剩 {remaining} 天）", "bilibili")]
    return [check("auth.bilibili.cookies", "warn", "cookie 没有 expires，无法判断过期", "bilibili")]


def check_auth_douyin(cfg: dict) -> list[dict]:
    profile = Path(str(cfg_get(cfg, "douyin.profile_dir", "") or ""))
    publisher = Path(str(cfg_get(cfg, "tools.douyin_publisher", "") or ""))
    items = []
    if not publisher.exists():
        items.append(check("auth.douyin.publisher", "error", f"抖音发布器不存在：{publisher}", "douyin"))
    if not profile.exists():
        items.append(check("auth.douyin.profile", "error", f"Chrome 登录态目录不存在：{profile}", "douyin"))
    else:
        # Playwright 持久化 profile 的 cookie 在 Default/Network/Cookies（Chrome 96+），旧版在 Default/Cookies。
        markers = [path for path in (profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies")
                   if path.exists()]
        if not markers:
            items.append(check("auth.douyin.profile", "error",
                               "登录态目录在，但没有 cookie 文件，先人工登录一次", "douyin"))
        else:
            newest = max(path.stat().st_mtime for path in markers)
            age_days = (time.time() - newest) / 86400
            deadline = datetime.fromtimestamp(newest, TZ) if TZ else datetime.fromtimestamp(newest)
            detail = f"登录 cookie 在（更新于 {deadline:%Y-%m-%d}）"
            items.append(check("auth.douyin.profile",
                               "warn" if age_days > 14 else "info",
                               detail + ("，超过 14 天没更新，跑之前先确认还能登录" if age_days > 14 else ""),
                               "douyin"))
    return items


def check_platform_assets(job: dict, target: str, rules: dict, probes: dict) -> list[dict]:
    p_rules = (rules.get("platforms") or {}).get(target) or {}
    fmt, video = asset_for(job, "video", target)
    items = []

    def add(item_id, level, detail):
        items.append(check(item_id, level, detail, target))

    expected_fmt = FORMAT_ALIASES.get(fmt, fmt)
    if expected_fmt not in (p_rules.get("formats") or [expected_fmt]):
        add("video.format", "warn", f"{target} 默认画幅 {p_rules.get('formats')}，这份是 {expected_fmt}")

    if not video:
        add("video.exists", "error", f"没有 {target} 可用的视频文件")
        return items
    path = Path(video)
    if not path.exists():
        add("video.exists", "error", f"视频不存在：{video}")
        return items
    size = path.stat().st_size
    max_bytes = int(p_rules.get("max_video_bytes") or 0)
    if max_bytes and size > max_bytes:
        add("video.size", "error", f"文件 {size / 1e9:.2f} GB 超过平台上限 {max_bytes / 1e9:.1f} GB")
    else:
        add("video.size", "info", f"{size / 1e6:.1f} MB")

    info, error = probes.get(str(path), (None, ""))
    if info is None:
        add("video.probe", "error", error or "ffprobe 无法读取")
        return items
    add("video.probe", "info",
        f"{info['width']}x{info['height']} {info['fps']}fps {info['duration']}s "
        f"{info['codec']}/{info['audio_codec'] or '无音轨'}")

    expected = (rules.get("formats") or {}).get(expected_fmt) or {}
    if expected:
        want_w, want_h = int(expected["width"]), int(expected["height"])
        if (info["width"], info["height"]) != (want_w, want_h):
            add("video.dimension", "error",
                f"分辨率 {info['width']}x{info['height']} ≠ 规则 {want_w}x{want_h}（{expected_fmt}）")
        else:
            add("video.dimension", "info", f"{expected_fmt} 分辨率正确")
    fps_rules = rules.get("fps") or {}
    if fps_rules and not (float(fps_rules.get("min", 0)) <= info["fps"] <= float(fps_rules.get("max", 999))):
        add("video.fps", "error", f"帧率 {info['fps']} 不在 {fps_rules.get('min')}-{fps_rules.get('max')}")
    dur_rules = rules.get("duration") or {}
    lo, hi = float(dur_rules.get("min", 0)), float(dur_rules.get("max", 10 ** 9))
    if not (lo <= info["duration"] <= hi):
        add("video.duration", "error", f"时长 {info['duration']}s 不在硬门禁 {lo:g}-{hi:g}s")
    else:
        preferred = dur_rules.get("preferred") or []
        if len(preferred) == 2 and not (float(preferred[0]) <= info["duration"] <= float(preferred[1])):
            add("video.duration", "warn",
                f"时长 {info['duration']}s 在硬门禁内，但不在目标区 {preferred[0]:g}-{preferred[1]:g}s")
        else:
            add("video.duration", "info", f"{info['duration']}s")
    if not info["has_audio"]:
        add("video.audio", "error", "没有音轨")
    else:
        add("video.audio", "info", f"音轨 {info['audio_codec']}")
    return items


def check_platform_text(job: dict, target: str, rules: dict) -> list[dict]:
    p_rules = (rules.get("platforms") or {}).get(target) or {}
    text = job.get("text") or {}
    items = []

    def add(item_id, level, detail, platform=target):
        items.append(check(item_id, level, detail, platform))

    title = text.get("title") or ""
    title_max = int(p_rules.get("title_max") or 0)
    if not title:
        add("text.title", "error", "标题为空")
    elif "\n" in title:
        add("text.title", "error", "标题里不能有换行")
    elif title_max and len(title) > title_max:
        add("text.title", "error", f"标题 {len(title)} 字 > 上限 {title_max} 字（删 {len(title) - title_max} 字）")
    else:
        add("text.title", "info", f"标题 {len(title)}/{title_max or '?'} 字")

    desc = text.get("desc") or ""
    desc_max = int(p_rules.get("desc_max") or 0)
    if not desc:
        add("text.desc", "warn", "简介为空")
    elif desc_max and len(desc) > desc_max:
        add("text.desc", "error", f"简介 {len(desc)} 字 > 上限 {desc_max} 字")
    else:
        add("text.desc", "info", f"简介 {len(desc)}/{desc_max or '?'} 字")

    if target == "bilibili":
        tags = text.get("tags") or []
        tag_max_chars = int(p_rules.get("tag_max_chars") or 0)
        if not (int(p_rules.get("tags_min", 1)) <= len(tags) <= int(p_rules.get("tags_max", 10))):
            add("text.tags", "error", f"标签 {len(tags)} 个，要求 {p_rules.get('tags_min')}-{p_rules.get('tags_max')} 个")
        else:
            bad = [tag for tag in tags if tag_max_chars and len(tag) > tag_max_chars]
            if bad:
                add("text.tags", "error", f"标签过长：{bad[:3]}")
            elif any("," in tag for tag in tags):
                add("text.tags", "error", "标签里不能有逗号（biliup 用逗号分隔标签）")
            else:
                add("text.tags", "info", f"{len(tags)} 个标签")
    else:
        hashtags = text.get("hashtags") or []
        if p_rules.get("require_hashtags") and not hashtags:
            add("text.hashtags", "warn", "没有 #话题（抖音曝光靠话题，建议至少 2 个）")
        elif hashtags:
            add("text.hashtags", "info", f"{len(hashtags)} 个话题")
    return items


def check_platform_cover(job: dict, target: str, rules: dict) -> list[dict]:
    p_rules = (rules.get("platforms") or {}).get(target) or {}
    fmt, cover = asset_for(job, "cover", target)
    if not cover:
        return [check("cover.exists", "warn", "没有封面（B 站/抖音都会用首帧，效果差）", target)]
    path = Path(cover)
    if not path.exists():
        return [check("cover.exists", "error", f"封面不存在：{cover}", target)]
    ext = path.suffix.lower()
    allowed = [str(item).lower() for item in (p_rules.get("cover_ext") or [".jpg", ".png"])]
    if ext not in allowed:
        return [check("cover.ext", "error", f"封面格式 {ext} 不在 {allowed}", target)]
    size = image_size(path)
    if size is None:
        return [check("cover.dimension", "warn", "读不出封面尺寸（格式兼容性未知）", target)]
    width, height = size
    minimum = p_rules.get("cover_min") or [0, 0]
    if width < int(minimum[0]) or height < int(minimum[1]):
        return [check("cover.dimension", "error",
                      f"封面 {width}x{height} < 最小 {minimum[0]}x{minimum[1]}", target)]
    want = float(p_rules.get("cover_aspect") or 0)
    tolerance = float(p_rules.get("cover_aspect_tolerance") or 0.3)
    actual = width / height if height else 0
    if want and abs(actual - want) > tolerance:
        return [check("cover.dimension", "warn",
                      f"封面比例 {actual:.2f} 偏离期望 {want:.2f}（±{tolerance}），可能被裁切", target)]
    return [check("cover.dimension", "info", f"封面 {width}x{height} 比例 {actual:.2f}", target)]


def check_qa(job: dict, rules: dict, targets: list[str]) -> list[dict]:
    qa = job.get("qa") or {}
    items = []
    verdict = qa.get("verdict")
    failed = verdict is False or str(verdict).strip().lower() in ("fail", "failed", "false")
    if failed:
        items.append(check("qa.verdict", "error", "上游 QA 判定未通过（qa.verdict=fail），不许发布"))
    elif verdict is None or str(verdict).strip().lower() in ("", "none", "null"):
        items.append(check("qa.verdict", "warn", "job 里没有 qa.verdict，无法确认上游验收"))
    else:
        items.append(check("qa.verdict", "info", f"QA verdict={verdict}"))

    asr_rules = rules.get("asr") or {}
    cer = qa.get("cer")
    if cer is None:
        items.append(check("asr.cer", "warn", "没有 cer（字准确率）回执，建议上游补测"))
    else:
        try:
            cer = float(cer)
        except (TypeError, ValueError):
            cer = None
        if cer is None:
            items.append(check("asr.cer", "warn", f"cer 不是数字：{qa.get('cer')!r}"))
        elif cer > float(asr_rules.get("cer_warn", 0.13)):
            items.append(check("asr.cer", "error",
                               f"CER {cer:.4f} > 拦截线 {asr_rules.get('cer_warn')}"))
        elif cer > float(asr_rules.get("cer_pass", 0.11)):
            items.append(check("asr.cer", "warn",
                               f"CER {cer:.4f} 在警告区（{asr_rules.get('cer_pass')}-{asr_rules.get('cer_warn')}）"))
        else:
            items.append(check("asr.cer", "info", f"CER {cer:.4f}"))

    audio_rules = rules.get("audio") or {}
    lufs = qa.get("lufs")
    if lufs is None:
        items.append(check("audio.loudness", "warn", "没有响度回执（未测）"))
    else:
        try:
            lufs = float(lufs)
            low, high = audio_rules.get("lufs_range") or [-99, 99]
            if not (float(low) <= lufs <= float(high)):
                items.append(check("audio.loudness", "warn", f"响度 {lufs:.1f} LUFS 不在 {low}~{high}"))
            else:
                items.append(check("audio.loudness", "info", f"{lufs:.1f} LUFS"))
        except (TypeError, ValueError):
            items.append(check("audio.loudness", "warn", f"lufs 不是数字：{qa.get('lufs')!r}"))
    tp = qa.get("tp")
    if tp is not None:
        try:
            tp = float(tp)
            limit = float(audio_rules.get("tp_max_db", -1.0))
            if tp > limit:
                items.append(check("audio.true_peak", "warn", f"真峰 {tp:.1f} dBTP > {limit}，有削波风险"))
            else:
                items.append(check("audio.true_peak", "info", f"{tp:.1f} dBTP"))
        except (TypeError, ValueError):
            pass
    if not job.get("ai_declaration"):
        items.append(check("ai.declaration", "error", "job 没有标记 ai_declaration=true（AI 生成内容必须声明）"))
    else:
        items.append(check("ai.declaration", "info", "AI 声明已标记"))
    return items


def build_plan(job: dict, target: str, cfg: dict, rules: dict, probes: dict) -> dict:
    p_rules = (rules.get("platforms") or {}).get(target) or {}
    fmt, video = asset_for(job, "video", target)
    _, cover = asset_for(job, "cover", target)
    text = job.get("text") or {}
    info = (probes.get(str(video)) or (None, ""))[0] or {}
    plan = {
        "target": target,
        "format": fmt,
        "video": video,
        "cover": cover,
        "title": text.get("title"),
        "desc": text.get("desc"),
        "tags": text.get("tags"),
        "hashtags": text.get("hashtags"),
        "duration": info.get("duration"),
        "size_bytes": info.get("size_bytes") or (Path(video).stat().st_size if video and Path(video).exists() else 0),
        "schedule": None,
        "command": None,
        "warnings": [],
    }
    schedule = (job.get("schedule") or {}).get(target)
    if target == "bilibili":
        tid = int(cfg_get(cfg, "bilibili.tid", p_rules.get("tid", 231)) or 231)
        dtime = 0
        if schedule in (None, "", "auto"):
            slots = next_slots(cfg_get(cfg, "bilibili.schedule_times") or p_rules.get("schedule_times"),
                               1, timedelta(hours=float(p_rules.get("min_lead_hours", 4.05))))
            if slots:
                dtime = int(slots[0].timestamp())
                plan["schedule"] = slots[0].strftime("%Y-%m-%d %H:%M")
            else:
                plan["warnings"].append("没有可用的 B 站定时时段（auto 需要配置 bilibili.schedule_times）")
        else:
            moment = parse_time(str(schedule))
            if moment:
                dtime = int(moment.timestamp())
                plan["schedule"] = moment.strftime("%Y-%m-%d %H:%M")
            else:
                plan["warnings"].append(f"无法解析 schedule：{schedule!r}")
        plan["dtime"] = dtime
        argv = [str(cfg_get(cfg, "tools.biliup", ""))]
        if cfg_get(cfg, "bilibili.proxy"):
            argv += ["--proxy", str(cfg_get(cfg, "bilibili.proxy"))]
        argv += [
            "--user-cookie", str(cfg_get(cfg, "bilibili.cookies_path", "")),
            "upload", video,
            "--submit", str(cfg_get(cfg, "bilibili.submit", p_rules.get("submit", "client"))),
            "--title", str(text.get("title") or ""),
            "--desc", str(text.get("desc") or ""),
            "--tag", ",".join(text.get("tags") or []),
            "--tid", str(tid),
            "--cover", cover,
            "--copyright", str(int(p_rules.get("copyright", 1))),
            "--no-reprint", str(int(p_rules.get("no_reprint", 1))),
            "--extra-fields", json.dumps({"creation_statement": {"id": 1}}, separators=(",", ":")),
        ]
        if dtime:
            argv += ["--dtime", str(dtime)]
        plan["command"] = argv
        if plan["schedule"]:
            plan["warnings"] = [w for w in plan["warnings"] if "定时" not in w]
    else:
        desc = text.get("desc") or ""
        hashtags = text.get("hashtags") or []
        joined = " ".join(hashtags)
        if joined and joined not in desc:
            desc = (desc + "\n" + joined).strip()
        schedule_text = schedule
        if schedule in (None, "", "auto"):
            slots = next_slots(cfg_get(cfg, "douyin.schedule_times") or p_rules.get("schedule_times"),
                               1, timedelta(minutes=int(p_rules.get("min_lead_minutes", 30))))
            schedule_text = slots[0].strftime("%Y-%m-%d %H:%M") if slots else ""
            if not schedule_text:
                plan["warnings"].append("没有可用的抖音定时时段（auto 需要配置 douyin.schedule_times）")
        else:
            moment = parse_time(str(schedule))
            schedule_text = moment.strftime("%Y-%m-%d %H:%M") if moment else str(schedule)
        plan["schedule"] = schedule_text
        result_root = Path(str(cfg_get(cfg, "douyin.result_root", "") or ""))
        shot_root = Path(str(cfg_get(cfg, "douyin.shot_root", "") or ""))
        job_id = str(job.get("job_id") or "job")
        plan["desc"] = desc
        plan["douyin_job"] = {
            "slug": job_id,
            "video": video,
            "cover": cover,
            "title": text.get("title") or "",
            "desc": desc,
            "schedule": schedule_text,
            "shot_dir": str(shot_root / job_id),
            "result": str(result_root / f"douyin-{job_id}-result.json"),
        }
        plan["command"] = [
            str(cfg_get(cfg, "tools.douyin_python", sys.executable)),
            str(cfg_get(cfg, "tools.douyin_publisher", "")),
            "--job", "<生成的 job.json>",
        ]
    return plan


def check_schedule(plan: dict, rules: dict) -> list[dict]:
    target = plan["target"]
    p_rules = (rules.get("platforms") or {}).get(target) or {}
    if target == "bilibili":
        lead = timedelta(hours=float(p_rules.get("min_lead_hours", 4.05)))
        dtime = int(plan.get("dtime") or 0)
        if not dtime:
            return [check("schedule.valid", "error", "没有合法的定时时间（B 站 dtime 必须是未来 >4h）", target)]
        moment = datetime.fromtimestamp(dtime, TZ) if TZ else datetime.fromtimestamp(dtime)
        if moment < now() + lead:
            return [check("schedule.valid", "error",
                          f"定时 {moment:%Y-%m-%d %H:%M} 距现在不足 {lead.total_seconds() / 3600:.1f}h", target)]
        return [check("schedule.valid", "info", f"定时 {moment:%Y-%m-%d %H:%M}", target)]
    text = plan.get("schedule") or ""
    moment = parse_time(text)
    if not moment:
        return [check("schedule.valid", "error", f"定时时间无法解析：{text!r}", target)]
    lead = timedelta(minutes=int(p_rules.get("min_lead_minutes", 30)))
    if moment < now() + lead:
        return [check("schedule.valid", "error",
                      f"定时 {moment:%Y-%m-%d %H:%M} 距现在不足 {lead.total_seconds() / 60:.0f} 分钟", target)]
    return [check("schedule.valid", "info", f"定时 {moment:%Y-%m-%d %H:%M}", target)]


# --------------------------------------------------------------------------
# 账本
# --------------------------------------------------------------------------

def ledger_path(cfg: dict) -> Path:
    return Path(str(cfg.get("ledger") or DEFAULT_LEDGER_PATH)).expanduser()


def read_ledger(cfg: dict) -> list[dict]:
    path = ledger_path(cfg)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def append_ledger(cfg: dict, entry: dict) -> None:
    path = ledger_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry.setdefault("ts", iso())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def fingerprint(video: str, title: str) -> str:
    digest = hashlib.sha256()
    if video and Path(video).exists():
        digest.update(sha256_file(Path(video)).encode("ascii"))
    else:
        digest.update(b"<missing>")
    digest.update(b"|")
    digest.update(str(title or "").encode("utf-8"))
    return digest.hexdigest()


def latest_entry(cfg: dict, job_id: str, target: str) -> dict | None:
    found = None
    for entry in read_ledger(cfg):
        if str(entry.get("job_id")) == str(job_id) and str(entry.get("target")) == target:
            found = entry
    return found


def check_duplicate(cfg: dict, job: dict, target: str, fp: str) -> list[dict]:
    job_id = str(job.get("job_id"))
    latest = latest_entry(cfg, job_id, target)
    if latest and str(latest.get("state")) == "submitted":
        if str(latest.get("fingerprint")) == fp:
            return [check("dup.submitted", "error",
                          f"这条已经投过（{latest.get('ts')}，回执 "
                          f"{json.dumps(latest.get('receipt') or {}, ensure_ascii=False)}），不许重投", target)]
        return [check("dup.submitted", "warn",
                      f"同 job_id 曾以不同素材投过（{latest.get('ts')}），请确认不是误投", target)]
    if latest and str(latest.get("state")) == "uncertain":
        return [check("dup.uncertain", "error",
                      f"上一次结果不确定（{latest.get('ts')}），先 resolve 再投", target)]
    if latest and str(latest.get("state")) == "submitting":
        return [check("dup.uncertain", "warn",
                      f"账本里还留着 submitting（{latest.get('ts')}），若进程已死用 resolve 结案", target)]
    return [check("dup.submitted", "info", "账本里没有投递记录", target)]


# --------------------------------------------------------------------------
# 预检总入口
# --------------------------------------------------------------------------

def run_checks(job: dict, cfg: dict, rules: dict) -> list[dict]:
    targets = [t for t in job.get("targets") or [] if t in (rules.get("platforms") or {})]
    if not targets:
        return [check("job.targets", "error", f"job.targets 里没有已支持的平台：{job.get('targets')}")]
    probes: dict = {}
    ffprobe = str(cfg_get(cfg, "tools.ffprobe", "") or "")
    for target in targets:
        _, video = asset_for(job, "video", target)
        if video and video not in probes:
            probes[video] = probe(ffprobe, video)
    items: list[dict] = []
    items.append(check("job.schema", "info" if job.get("schema") in (None, SCHEMA) else "warn",
                       f"job_id={job.get('job_id')} targets={targets}"))
    items.extend(check_qa(job, rules, targets))
    for target in targets:
        items.extend(check_auth_bilibili(cfg, rules) if target == "bilibili" else check_auth_douyin(cfg))
        items.extend(check_platform_assets(job, target, rules, probes))
        items.extend(check_platform_text(job, target, rules))
        items.extend(check_platform_cover(job, target, rules))
        plan = build_plan(job, target, cfg, rules, probes)
        items.extend(check_schedule(plan, rules))
        _, video = asset_for(job, "video", target)
        items.extend(check_duplicate(cfg, job, target, fingerprint(video, (job.get("text") or {}).get("title"))))
        command = plan.get("command") or [""]
        items.append(check("plan.command", "info",
                           f"{target} 计划已生成（定时 {plan.get('schedule') or '无'}）" if command[0]
                           else f"{target} 计划未生成（缺工具路径）", target))
    return items


def summarize(items: list[dict]) -> dict:
    errors = [item for item in items if item["level"] == "error"]
    warnings = [item for item in items if item["level"] == "warn"]
    return {"errors": errors, "warnings": warnings, "ok": not errors}


def print_checks(items: list[dict], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    icons = {"error": "✗", "warn": "!", "info": "✓"}
    current = None
    for item in items:
        if item["platform"] != current:
            current = item["platform"]
            print(f"\n[{current}]")
        print(f"  {icons.get(item['level'], '?')} {item['id']:<22} {item['detail']}")
    summary = summarize(items)
    print(f"\n结论：{'通过' if summary['ok'] else '拦截'}（error {len(summary['errors'])} / warn {len(summary['warnings'])}）")


# --------------------------------------------------------------------------
# 执行器
# --------------------------------------------------------------------------

def run_process(argv: list[str], timeout: int, env_extra: dict | None = None) -> tuple[int | None, str, bool]:
    """返回 (returncode|None=超时, 输出尾部, 是否成功启动)。"""
    env = dict(os.environ)
    env["NO_PROXY"] = (env.get("NO_PROXY") or "") + ",*.bilivideo.com,*.akamaized.net,*.bilibili.com"
    env["no_proxy"] = env["NO_PROXY"]
    env.update(env_extra or {})
    try:
        result = subprocess.run([str(item) for item in argv], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=max(30, int(timeout)), env=env)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return None, redact(output)[-2000:], True
    except OSError as exc:
        return -1, f"进程无法启动：{exc}", False
    return result.returncode, redact((result.stdout or "") + "\n" + (result.stderr or ""))[-4000:], True


def submit_bilibili(job: dict, plan: dict, cfg: dict) -> dict:
    biliup = str(cfg_get(cfg, "tools.biliup", "") or "")
    if not biliup or not Path(biliup).exists():
        return {"state": "failed", "note": f"biliup 不存在：{biliup}", "code": None, "receipt": {}}
    code, output, started = run_process(plan["command"], int(cfg_get(cfg, "bilibili.timeout_sec", 900)))
    if not started:
        return {"state": "failed", "note": output[-300:], "code": code, "receipt": {}, "output": output[-600:]}
    if code is None:
        return {"state": "uncertain", "note": "上传进程超时，结果未知，必须到 B 站后台核对",
                "code": None, "receipt": {}, "output": output[-600:]}
    if code != 0:
        hint = ""
        if "601" in output:
            hint = "（601 上传过快，明天再传，别重试）"
        elif "412" in output or "风控" in output:
            hint = "（疑似风控，先停自动投稿）"
        return {"state": "uncertain", "note": f"biliup 退出码 {code}{hint}；进程已启动，结果未知，后台核对",
                "code": code, "receipt": {}, "output": output[-600:]}
    bv = BV_RE.search(output)
    aid = AID_RE.search(output)
    receipt = {"bvid": bv.group(0) if bv else "", "aid": int(aid.group(1)) if aid else None}
    note = "" if bv else "上传成功但没解析到 BV，请到 B 站后台核对（不要重传）"
    return {"state": "submitted", "note": note, "code": code, "receipt": receipt, "output": output[-600:]}


def submit_douyin(job: dict, plan: dict, cfg: dict) -> dict:
    publisher = str(cfg_get(cfg, "tools.douyin_publisher", "") or "")
    if not publisher or not Path(publisher).exists():
        return {"state": "failed", "note": f"抖音发布器不存在：{publisher}", "code": None, "receipt": {}}
    douyin_job = dict(plan.get("douyin_job") or {})
    if not douyin_job.get("schedule"):
        return {"state": "failed", "note": "没有合法的抖音定时时间", "code": None, "receipt": {}}
    job_path = Path(douyin_job["result"]).with_name(f"job-{job.get('job_id')}.json")
    write_json(job_path, douyin_job)
    python = str(cfg_get(cfg, "tools.douyin_python", sys.executable))
    argv = [python, publisher, "--job", str(job_path)]
    code, output, started = run_process(argv, int(cfg_get(cfg, "douyin.timeout_sec", 900)))
    receipt = {"job": str(job_path), "result": douyin_job["result"], "schedule": douyin_job["schedule"]}
    if not started:
        return {"state": "failed", "note": output[-300:], "code": code, "receipt": receipt}
    if code is None:
        return {"state": "uncertain", "note": "抖音发布进程超时，先看后台再决定，别重跑",
                "code": None, "receipt": receipt, "output": output[-600:]}
    result = read_json(Path(douyin_job["result"]), None)
    if not isinstance(result, dict):
        if code == 0:
            return {"state": "uncertain", "note": "进程退出 0 但结果 JSON 读不到，后台核对",
                    "code": code, "receipt": receipt, "output": output[-600:]}
        return {"state": "failed", "note": f"退出码 {code}，无结果文件", "code": code, "receipt": receipt,
                "output": output[-600:]}
    ok = bool(result.get("success")) and bool(result.get("scheduled")) and bool(result.get("ai_declared"))
    receipt.update({"success": result.get("success"), "scheduled": result.get("scheduled"),
                    "ai_declared": result.get("ai_declared"), "stage": result.get("stage"),
                    "sha256": result.get("sha256"), "finished_at": result.get("finished_at")})
    if ok:
        expected = sha256_file(Path(plan["video"])) if plan.get("video") and Path(plan["video"]).exists() else ""
        if expected and result.get("sha256") and str(result["sha256"]) != expected:
            return {"state": "uncertain", "note": "平台回执里的 sha256 与本地视频不一致，上传的可能不是这份文件",
                    "code": code, "receipt": receipt, "output": output[-600:]}
        return {"state": "submitted", "note": "", "code": code, "receipt": receipt, "output": output[-600:]}
    reasons = []
    if not result.get("success"):
        reasons.append("success=false")
    if not result.get("scheduled"):
        reasons.append("scheduled=false")
    if not result.get("ai_declared"):
        reasons.append("ai_declared=false")
    hint = {2: "（登录态问题，先人工登录）", 4: "（页面步骤失败）", 5: "（页面步骤失败）"}.get(code, "")
    return {"state": "failed", "note": f"{','.join(reasons)}{hint}", "code": code, "receipt": receipt,
            "output": output[-600:]}


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------

def cmd_doctor(args, cfg: dict, rules: dict) -> int:
    rows = []

    def row(name, ok, detail):
        rows.append((name, "✓" if ok else "✗", detail))

    row("python", sys.version_info >= (3, 9), sys.version.split()[0])
    ffprobe = str(cfg_get(cfg, "tools.ffprobe", "") or "")
    ok = bool(ffprobe) and Path(ffprobe).exists()
    version = ""
    if ok:
        code, output, _ = run_process([ffprobe, "-version"], 20)
        version = (output.splitlines() or [""])[0][:80]
    row("ffprobe", ok, ffprobe or "找不到（装 ffmpeg 或配 tools.ffprobe）")
    biliup = str(cfg_get(cfg, "tools.biliup", "") or "")
    ok = bool(biliup) and Path(biliup).exists()
    version = ""
    if ok:
        _, output, _ = run_process([biliup, "--version"], 20)
        version = (output.splitlines() or [""])[0][:60]
    row("biliup", ok, (biliup + ("  " + version if version else "")) if biliup else "找不到 biliup")
    cookies = Path(str(cfg_get(cfg, "bilibili.cookies_path", "") or ""))
    auth_items = check_auth_bilibili(cfg, rules)
    row("bilibili.cookie", not any(i["level"] == "error" for i in auth_items),
        "; ".join(i["detail"] for i in auth_items))
    publisher = Path(str(cfg_get(cfg, "tools.douyin_publisher", "") or ""))
    row("douyin.publisher", publisher.exists(), str(publisher) or "未配置")
    profile = Path(str(cfg_get(cfg, "douyin.profile_dir", "") or ""))
    row("douyin.profile", profile.exists(), str(profile))
    ledger = ledger_path(cfg)
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        probe_file = ledger.parent / ".write-test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        row("ledger", True, str(ledger))
    except OSError as exc:
        row("ledger", False, f"{ledger}（{exc}）")
    row("rules", True, str(cfg.get("rules") or DEFAULT_RULES_PATH))
    # 没有配置文件不算故障：内置探测已能从本机已有工具推断出全部路径。
    row("config", True, cfg.get("_config_path") or f"未找到，用内置探测值（可创建 {DEFAULT_CONFIG_PATH} 固化）")

    for name, mark, detail in rows:
        print(f"  {mark} {name:<18} {detail}")
    bad = [name for name, mark, _ in rows if mark == "✗"]
    print(f"\n结论：{'环境就绪' if not bad else '缺组件 → ' + ','.join(bad)}")
    return 0 if not bad else 2


def cmd_check(args, cfg: dict, rules: dict) -> int:
    job = load_job(Path(args.job))
    if args.target:
        job["targets"] = [t.strip().lower() for t in args.target.split(",") if t.strip()]
    items = run_checks(job, cfg, rules)
    print_checks(items, as_json=args.json)
    return 0 if summarize(items)["ok"] else 1


def cmd_plan(args, cfg: dict, rules: dict) -> int:
    job = load_job(Path(args.job))
    if args.target:
        job["targets"] = [t.strip().lower() for t in args.target.split(",") if t.strip()]
    probes = {}
    ffprobe = str(cfg_get(cfg, "tools.ffprobe", "") or "")
    for target in job["targets"]:
        _, video = asset_for(job, "video", target)
        if video and video not in probes:
            probes[video] = probe(ffprobe, video)
    plans = [build_plan(job, target, cfg, rules, probes) for target in job["targets"]]
    payload = {"job_id": job.get("job_id"), "plans": plans}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for plan in plans:
        print(f"\n[{plan['target']}] {plan['video']}")
        print(f"  画幅：{plan['format']}  时长：{plan['duration']}s  定时：{plan['schedule'] or '（无）'}")
        print(f"  标题：{plan['title']}")
        print(f"  标签：{plan['tags'] or plan['hashtags']}")
        if plan.get("douyin_job"):
            print("  抖音 job：\n" + json.dumps(plan["douyin_job"], ensure_ascii=False, indent=4))
        if plan["command"]:
            print("  命令：" + subprocess.list2cmdline([str(x) for x in plan["command"]]))
        for warning in plan.get("warnings") or []:
            print(f"  ! {warning}")
    return 0


def cmd_submit(args, cfg: dict, rules: dict) -> int:
    job = load_job(Path(args.job))
    if args.target:
        job["targets"] = [t.strip().lower() for t in args.target.split(",") if t.strip()]
    targets = [t for t in job["targets"] if t in (rules.get("platforms") or {})]
    if not targets:
        print("✗ job.targets 里没有已支持的平台", file=sys.stderr)
        return 1
    items = run_checks(job, cfg, rules)
    summary = summarize(items)
    print_checks(items, as_json=args.json)
    if not summary["ok"]:
        print("\n✗ 预检有拦截项，未执行任何提交。修完后重跑。", file=sys.stderr)
        return 1
    ffprobe = str(cfg_get(cfg, "tools.ffprobe", "") or "")
    probes = {}
    for target in targets:
        _, video = asset_for(job, "video", target)
        if video and video not in probes:
            probes[video] = probe(ffprobe, video)
    exit_code = 0
    for target in targets:
        plan = build_plan(job, target, cfg, rules, probes)
        _, video = asset_for(job, "video", target)
        fp = fingerprint(video, (job.get("text") or {}).get("title"))
        previous = latest_entry(cfg, str(job.get("job_id")), target)
        if previous and str(previous.get("state")) == "submitted" and str(previous.get("fingerprint")) == fp:
            print(f"\n[{target}] 已提交过，跳过（回执 {json.dumps(previous.get('receipt') or {}, ensure_ascii=False)}）")
            continue
        if previous and str(previous.get("state")) == "uncertain" and not args.force_uncertain:
            print(f"\n✗ [{target}] 上次结果不确定，先 publish.py resolve 结案", file=sys.stderr)
            exit_code = 1
            continue
        print(f"\n[{target}] 提交 → {plan['video']}")
        if args.dry_run:
            print("  DRY-RUN：不执行。命令：" + subprocess.list2cmdline([str(x) for x in plan["command"]]))
            continue
        append_ledger(cfg, {"job_id": job.get("job_id"), "target": target, "state": "submitting",
                            "format": plan["format"], "video": plan["video"],
                            "fingerprint": fp, "schedule": plan.get("schedule")})
        result = submit_bilibili(job, plan, cfg) if target == "bilibili" else submit_douyin(job, plan, cfg)
        append_ledger(cfg, {"job_id": job.get("job_id"), "target": target, "state": result["state"],
                            "format": plan["format"], "video": plan["video"], "fingerprint": fp,
                            "schedule": plan.get("schedule"), "note": result.get("note", ""),
                            "code": result.get("code"), "receipt": result.get("receipt") or {},
                            "output_tail": result.get("output", "") if args.verbose else ""})
        mark = {"submitted": "✓", "failed": "✗", "uncertain": "!"}.get(result["state"], "?")
        print(f"  {mark} {result['state']}  {result.get('note') or ''}")
        if result.get("receipt"):
            print("  回执：" + json.dumps(result["receipt"], ensure_ascii=False))
        if args.verbose and result.get("output"):
            print("  输出尾部：\n" + "\n".join("    " + line for line in result["output"].splitlines()[-12:]))
        if result["state"] != "submitted":
            exit_code = 1
    return exit_code


def cmd_status(args, cfg: dict, rules: dict) -> int:
    entries = read_ledger(cfg)
    if args.job:
        job_id = str(load_job(Path(args.job)).get("job_id"))
        entries = [e for e in entries if str(e.get("job_id")) == job_id]
    latest: dict = {}
    for entry in entries:
        latest[(str(entry.get("job_id")), str(entry.get("target")))] = entry
    if args.json:
        print(json.dumps([latest[key] for key in latest], ensure_ascii=False, indent=2))
        return 0
    if not latest:
        print("账本里还没有记录。")
        return 0
    print(f"{'job_id':<28}{'target':<11}{'state':<12}{'time':<20}note")
    for (job_id, target), entry in sorted(latest.items()):
        print(f"{job_id:<28}{target:<11}{str(entry.get('state')):<12}{str(entry.get('ts')):<20}"
              f"{str(entry.get('note') or '')[:60]}")
    return 0


def cmd_resolve(args, cfg: dict, rules: dict) -> int:
    job = load_job(Path(args.job))
    job_id = str(job.get("job_id"))
    target = args.target
    previous = latest_entry(cfg, job_id, target)
    if not previous:
        print("✗ 账本里没有这条记录", file=sys.stderr)
        return 1
    if str(previous.get("state")) != "uncertain":
        print(f"! 当前状态是 {previous.get('state')}，只有 uncertain 需要结案", file=sys.stderr)
    if args.state not in ("submitted", "failed"):
        print("✗ --state 只能是 submitted 或 failed", file=sys.stderr)
        return 1
    append_ledger(cfg, {"job_id": job_id, "target": target, "state": args.state,
                        "format": previous.get("format"), "video": previous.get("video"),
                        "fingerprint": previous.get("fingerprint"), "schedule": previous.get("schedule"),
                        "receipt": previous.get("receipt") or {},
                        "note": f"人工结案：{args.note or ''}"})
    print(f"✓ 已把 {job_id}/{target} 结案为 {args.state}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish.py",
        description="一条成品视频 → 一份可核验的发布回执（B 站 / 抖音）。先 check，再 submit。",
    )
    parser.add_argument("--config", default="", help="配置文件路径（默认 ~/.video-publish/config.json）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--verbose", action="store_true", help="打印执行器输出尾部")
    sub = parser.add_subparsers(dest="command", required=True)

    sub_doctor = sub.add_parser("doctor", help="环境自检")
    sub_doctor.set_defaults(func=cmd_doctor)

    for name, func, help_text in (("check", cmd_check, "零网络预检"),
                                  ("plan", cmd_plan, "只打印提交参数")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--job", required=True)
        item.add_argument("--target", default="", help="只检查某个平台：bilibili,douyin")
        item.set_defaults(func=func)

    submit = sub.add_parser("submit", help="提交（先跑预检，全过才动手）")
    submit.add_argument("--job", required=True)
    submit.add_argument("--target", default="")
    submit.add_argument("--yes", action="store_true", help="确认公开投稿（必须显式给）")
    submit.add_argument("--dry-run", action="store_true", help="只打印，不执行")
    submit.add_argument("--force-uncertain", action="store_true",
                        help="上一轮不确定也继续投（默认禁止，会导致重复投稿）")
    submit.set_defaults(func=cmd_submit)

    status = sub.add_parser("status", help="账本")
    status.add_argument("--job", default="")
    status.set_defaults(func=cmd_status)

    resolve = sub.add_parser("resolve", help="人工结案 uncertain")
    resolve.add_argument("--job", required=True)
    resolve.add_argument("--target", required=True, choices=["bilibili", "douyin"])
    resolve.add_argument("--state", required=True, choices=["submitted", "failed"])
    resolve.add_argument("--note", default="")
    resolve.set_defaults(func=cmd_resolve)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config or None)
    try:
        rules = load_rules(cfg)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.command == "submit" and not args.yes and not args.dry_run:
        print("✗ 公开投稿必须显式加 --yes（防止误发）。先 --dry-run 看参数。", file=sys.stderr)
        return 1
    return int(args.func(args, cfg, rules) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
