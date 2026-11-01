# 第一性原理：一条视频从「成品」到「已发布」到底需要什么

> 本文是 `video-publish` 的设计契约。代码、规则、文档冲突时以本文为准；本文与实测冲突时以实测为准，并回改本文。

## 1. 把问题拆到不可再分

「自动发布视频」不是一个动作，是 5 个**必须同时成立**的条件。任何一个不成立，发布必然是失败或假成功（平台没收到、或者收到了但重复）。

| # | 条件 | 不成立时会发生什么 | 本工具的处理 |
|---|---|---|---|
| C1 | **素材可被平台接受**：文件存在、可解码、分辨率/画幅/时长/帧率/音轨/响度/音准符合该平台 | 上传到一半被拒，或发出去播放器黑屏 | 本地 ffprobe 预检，**0 网络**，<3 秒 |
| C2 | **元数据合法**：标题/简介/标签/封面/分区/AI 声明都在平台限额内 | 提交时报 4xxx 错误码；或标题被后台截断 | 按规则表逐字段校验，错误码前置 |
| C3 | **账号可写**：cookie/登录态存在且未过期 | 走到最后一步弹登录，白等 3 分钟 | 本地解析 cookie / storage-state，**不发请求** |
| C4 | **提交动作唯一且幂等**：同一素材不投两次；结果不确定时不自动重试 | 重复投稿（B 站限流/降权）、抖音出现两条同款 | 内容指纹账本 + 三态（submitted/failed/uncertain） |
| C5 | **结果可判定**：成功必须有平台回执 ID；否则必须标 uncertain 并可人工结案 | 日志写「成功」但后台没有；或者明明成功又投一次 | 解析 BV/aid、抖音 result.success；uncertain 永不自动重发 |

**推论 1：发布环节真正的成本不是「上传」，是「发现条件不成立」的时机。**
实测旧链路：一条片子 QA/CER 不过 → 已经花 614.9 秒渲染 + 9 次重试；B 站预检才发现时长越界；
标题超长要等 Bilibili 接口报错。**把 5 个条件全部前移到「0 网络、<3 秒」的检查里**，是最直接的提速。

**推论 2：速度来自「一次算对」，不是「跳过检查」。**
所以本工具有两道门：`check`（只读，可随时跑）与 `submit`（必须 `--yes` 才动手，且先跑一次 check）。
不允许 `--skip-check` 这种开关——那等于把 C1–C5 重新变成运气。

**推论 3：多平台不是复制粘贴，是同一份事实的多份投影。**
同一支视频有 9:16 与 16:9 两个画幅、两套限额、两套登录态。
事实（视频/标题/封面/QA）只存一份 `job.json`，平台差异只写在 `rules.default.json`。

## 2. 数据模型（唯一事实来源）

```jsonc
{
  "schema": 1,
  "job_id": "daily-20261101-1200",
  "video":  { "16:9": "D:\\...\\x-16x9.mp4", "9:16": "D:\\...\\x-9x16.mp4" },
  "cover":  { "16:9": "D:\\...\\cover-16x9.jpg", "9:16": "D:\\...\\cover-3x4.png" },
  "text": {
    "title": "MCP 不是智能，是接口：你给的权限正在变成后门？",
    "desc": "……",
    "tags": ["AI", "程序员"],
    "hashtags": ["#AI", "#程序员"]
  },
  "qa": { "duration": 45.03, "lufs": -14.92, "tp": -2.51, "cer": 0.1689, "verdict": "fail" },
  "targets": ["douyin", "bilibili"],
  "schedule": { "douyin": "2026-11-01 19:00", "bilibili": "auto" },
  "ai_declaration": true
}
```

- `qa` 是**上游交付的证据**，本工具不重新测 CER/响度（那要跑 ASR 与分析，属于生产环节）；
  但会核对 `qa.cer <= 0.11`、`qa.lufs` 在区间内 —— 条件不成立就不投。
- `schedule.bilibili: "auto"` = 取未来第一个 >4h 的合法时段（B 站 dtime 硬要求）。

## 3. 状态机（每个 target 独立）

```
        check 通过
planned ─────────→ submitting ──exit 0 + 回执 ID──→ submitted（终态，永不重投）
   │                    │
   │                    └──进程已启动但超时/非 0/无法判定──→ uncertain（人工核对）
   │
   └── check 失败 / 进程未启动──→ failed（可修后重跑）
```

规则：`uncertain` 只能靠 `publish.py resolve` 人工改判；`submitted` 只能靠账本指纹去重；
任何自动重试都只允许 `failed`，且必须重跑 check。

## 4. 五个条件怎么落到检查项

| 条件 | 检查项（id） | 判定依据 |
|---|---|---|
| C1 | `video.exists` `video.size` `video.probe` `video.format` `video.duration` `video.fps` `video.audio` | ffprobe JSON（唯一真相）+ 规则表 |
| C1 | `audio.loudness` `audio.true_peak` | `qa.lufs` / `qa.tp`（无则 warning：未测） |
| C1 | `asr.cer` | `qa.cer` 与阈值 0.11 放行 / 0.13 警告 / >0.13 拦 |
| C2 | `text.title` `text.desc` `text.tags` `cover.check` `cover.dimension` | 规则表（按平台） |
| C2 | `ai.declaration` | job 里必须为 true；B 站走 `creation_statement.id=1`，抖音走 UI 勾选 |
| C3 | `auth.bili.cookies` `auth.douyin.profile` | 本地文件存在 + 关键字段 + 未过期；**不发网络请求** |
| C4 | `dup.fingerprint` | 账本里 `sha256(video) + title` 已 submitted → 直接拦 |
| C5 | `schedule.valid` | 显式时段必须 > 现在；bilibili 必须 >4h |

判定级别：`error`（拦住，不投）/ `warn`（放行但打印，进回执）/ `info`（记录）。
退出码：`0` 全过或只有 warn；`1` 有 error；`2` 配置/环境问题（doctor 失败）。

## 5. 两个平台的适配器契约

### bilibili（已验证可用）
- 工具：`biliup.exe`（本机 v0.2.4），cookie 文件 JSON。
- 参数固定：`--submit client --tid 231 --copyright 1 --no-reprint 1 --dtime <ts> --cover <jpg> --tag a,b`
  `--extra-fields {"creation_statement":{"id":1}}`（AI 声明，B 站要求的显式字段）。
- 成功判定：进程 exit 0 **且** stdout 能解析出 `BV[0-9A-Za-z]{10}`；解析不到 BV → 记 submitted 但要醒目提示「后台核对」。
- 失败判定：进程未启动 / 非 0 退出 → `failed`；超时或 killed → `uncertain`（**不许重试**）。

### douyin（本机已验证可用，Playwright）
- 工具：`D:\2006L\tools\douyin-auto\publish_daily.py --job <job.json>`（持久化 Chrome profile）。
- job 字段：`slug/video/cover/title/desc/schedule("YYYY-MM-DD HH:MM")/shot_dir/result`。
- 成功判定：结果 JSON 里 `success && scheduled && ai_declared` **三者同为 true**；
  缺 `ai_declared` 算失败但**不是** uncertain（页面状态可判定：作品没发出去）。
- 退出码 2 = 登录态问题（不可重试，先修登录）；4/5 = 页面步骤失败（可修后重跑）。

## 6. 为什么这样能提速（可测量）

| 环节 | 旧链路 | 本工具 |
|---|---|---|
| 判断「这条能不能发」 | 各脚本自写阈值（已知三套时长阈值漂移，实测因此误杀 6/9） | 一份 `rules.default.json`，`check` <3s，一次看全部条件 |
| 元数据错误发现时间 | 等到平台接口报 4xxx | 提交前本地报，含超限字符数 |
| 重复投稿 | 靠人记 | 指纹账本自动拦 |
| 结果不确定 | 日志写「成功」/ 或人工翻后台 | 三态 + `resolve` 结案 |
| 入口数量 | pixelcat daily.py、GTR bilibili_upload.py、douyin-auto publish_daily.py 三处 | 一个 `publish.py submit --job ... --target all` |

## 7. 边界（不做什么）

- 不做生产：不渲染、不写稿、不 TTS、不做 QA 测量（只消费上游回执）。
- 不做平台登录：cookie/profile 由人维护；本工具只**检查**它还在。
- 不绕过平台风控：不并发、不省略 AI 声明、不改定时规则。
- 不猜限额：规则表里每条限额都要写 `source`（实测 / 代码内已有约束 / 平台公开说明）。
