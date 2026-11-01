# video-publish

> 一条成品视频 → 一份可核验的发布回执。B 站 / 抖音，先检查，再发布。

[![Skill](https://img.shields.io/badge/DSH-skill-4f7cff)](#) [![License](https://img.shields.io/badge/license-MIT-blue)](#license)

## 为什么做这个

自动化发布视频，真正贵的不是「上传」那两分钟，而是**发现条件不成立太晚**：

- 一条片子渲完 10 分钟，才发现标题超了平台限额；
- 传到一半被拒，因为视频没有音轨 / 画幅不对；
- 日志写着「成功」，后台却没有稿件；或者明明成功了，第二天又投了一遍；
- 三个脚本各写一套阈值（时长 44–56 / 48–52 / 50±5），同一支片子在不同环节结论相反。

把这些问题拆到不可再分，其实就是 **5 个条件必须同时成立**：

| 条件 | 一句话 | 不成立时 |
|---|---|---|
| C1 素材可被接受 | 文件可解码、分辨率/画幅/时长/帧率/音轨 | 上传中途被拒或播放器黑屏 |
| C2 元数据合法 | 标题/简介/标签/封面/AI 声明在限额内 | 平台 4xxx 报错、标题被截断 |
| C3 账号可写 | cookie / 浏览器登录态未过期 | 走到最后一步弹登录 |
| C4 提交唯一幂等 | 同素材不投两次、不确定不自动重试 | 重复投稿、限流、两条同款 |
| C5 结果可判定 | 有回执 ID，否则标 uncertain | 假成功 / 真成功又重投 |

**推论**：把 C1–C5 全部前移到「0 网络、3 秒内」的本地检查，过了才允许提交 —— 这是最直接的提速。
所以 `publish.py` 只有一条规则：**先 check，再 submit；没有 `--skip-check`，没有 `--force`。**

## 快速开始

```powershell
# 1) 装：探测本机 ffprobe / biliup / cookie / 抖音发布器，写配置并装进 ~/.dsh/skills
powershell -ExecutionPolicy Bypass -File .\install.ps1

# 2) 写 job（唯一事实来源，格式见 examples/job.example.json）
#    视频两个画幅 + 封面 + 标题/简介/标签 + 上游 qa 回执 + 定时 + ai_declaration: true

# 3) 三步发布
python publish.py check  --job job.json        # 0 网络预检，逐条给通过/拦截
python publish.py plan   --job job.json        # 确认参数（biliup argv / 抖音 job）
python publish.py submit --job job.json --yes  # 真投；加 --dry-run 只打印
```

```powershell
python publish.py doctor                        # 环境自检
python publish.py status                        # 账本：job × 平台的最新状态与回执
python publish.py resolve --job job.json --target bilibili --state submitted --note "后台已确认 BV1xxx"
                                                # 只给 uncertain 结案用
```

退出码：`0` 通过/成功；`1` 有拦截或失败；`2` 环境或配置问题。

## 命令与检查项

| 命令 | 做什么 | 副作用 |
|---|---|---|
| `doctor` | python / ffprobe / biliup / cookie 有效期 / 抖音登录态 / 账本可写 | 无 |
| `check` | 五道门逐条判定；`error` 拦截，`warn` 放行并进回执。加 `--deep` 会**整段解码**（抓「容器完好但载荷截断」）+ ffmpeg 实测响度并核对上游 qa | 无（**不碰网络**） |
| `plan` | 打印 B 站 biliup argv、抖音 job.json、定时换算结果 | 无 |
| `submit` | 先跑一遍 check，全过才执行；同指纹已 submitted 直接跳过 | 真实投稿 |
| `status` | 读 JSONL 账本 | 无 |
| `resolve` | 人工核对后台后给 `uncertain` 结案 | 只写账本 |

检查项 id 与对应条件（完整表见 `references/first-principles.md`）：

`video.exists / video.size / video.probe / video.dimension / video.duration / video.fps / video.audio`（C1）、
`audio.loudness / audio.true_peak / asr.cer / qa.verdict`（C1 质量门）、
`text.title / text.desc / text.tags / text.hashtags / cover.* / ai.declaration`（C2）、
`auth.bilibili.cookies / auth.douyin.profile`（C3）、
`dup.submitted / dup.uncertain`（C4）、`schedule.valid`（C5）。

**阈值只有一个来源**：`rules.default.json`。里面每个数字都写了 `source`（实测 / 代码内已有约束 / 平台公开说明 / 未验证）。改阈值只改这一个文件。

## 两个平台的差异

|  | B 站 | 抖音 |
|---|---|---|
| 画幅 | 16:9 / 1920×1080 | 9:16 / 1080×1920 |
| 执行器 | `biliup.exe upload`（唯一外部入口） | `douyin-auto/publish_daily.py`（Playwright + 持久化 Chrome profile） |
| 固定字段 | `tid=231`、`copyright=1`、`no_reprint=1` | 标题/简介/`#话题` 进 job |
| AI 声明 | `--extra-fields {"creation_statement":{"id":1}}` | 页面勾选，回执必须 `ai_declared=true` |
| 定时 | `--dtime` 必须比现在晚 **>4 小时** | `schedule` 形如 `2026-11-02 12:00` |
| 成功判据 | 退出码 0 且 stdout 能解析出 BV 号（aid 在回执里是 `Number(...)`，正则兼容） | `finished_at` 存在 ∧ `success` ∧ `scheduled` ∧ `ai_declared` ∧ body 里出现「作者声明：内容由AI生成」∧ 回执 sha256 与本地一致 |

抖音 `scheduled=true` 只代表「定时框填了且按了 Enter」，**平台可能静默改期**（真实案例：请求 00:50，实际 02:30，标志仍 true）。
所以定时必须在 `[now+2h, now+14d]` 内，提交后比对 `sched_value`；不一致时仍记 `submitted` 但醒目提示真实时间（标 failed 会诱导重投，反而出现两条同款）。

## 幂等与 uncertain

- 指纹 = `sha256(视频) + 标题`；同一指纹已 `submitted` → submit **直接跳过**并打印回执。
- 预检里的 `dup.*` 只是**路由提示**（warn），不是拦截：否则「B 站已发、抖音失败」的任务一重跑就被整条拦住。
  真正的拦截在 submit：`uncertain` 不结案不投；同 job_id 换过素材不 `--republish` 不投。
- 进程**已启动**但超时 / 非 0 退出 / 无法判定 → `uncertain`，**永不自动重投**。
- 只有 `failed`（进程没启动，例如工具不存在、预检失败）可以修完重跑。
- `uncertain` 必须先到平台后台核对，再用 `resolve` 结案；账本只追加，不改历史。
- 并发安全两道锁：每 `job × 平台` 一份**占位锁**（同一条同时只允许一个 submit 进程），
  账本写入再串行化（Windows 的 `O_APPEND` 并发会互相覆盖丢行，实测踩过）。

## 目录

```
publish.py              CLI：doctor / check / plan / submit / status / resolve
rules.default.json      阈值唯一来源（每个数字带 source）
config.example.json     配置模板（真实配置放 ~/.video-publish/config.json）
install.ps1             Windows 一键安装：探测路径 → 写配置 → 装进 ~/.dsh/skills
SKILL.md                DSH 技能入口
references/
  first-principles.md   设计契约：5 个条件 → 数据模型 → 状态机 → 检查表
  contracts.md          两个执行器的真实调用契约与回执字段（取证）
  pitfalls.md           本机真实踩过的坑（现象/根因/防法/依据）
tests/                  离线单测 + 对抗测试（不联网、不投真稿）
examples/job.example.json
```

## 测试

```powershell
python -m unittest discover -s tests -v
```

全部离线：用临时目录造假视频/假 cookie/假账本，执行器调用用 mock 替换，不联网、不做真实投稿。

## 已知边界（诚实清单）

- **平台后台仍需人眼看一次**：AI 声明是否显示、封面是否被裁、稿件是否真的定时 —— 机器只能保证字段提交了。
- **不做生产**：不渲染、不写稿、不 TTS、不做 QA 测量，只消费上游回执（`qa.json`）。CER/响度不达标应回生产环节重做，这里没有绕过开关。
- **不代管登录态**：B 站 cookie 由 biliup 维护，抖音靠持久化 Chrome profile；本工具只检查它们还在不在。
- **抖音限额**：标题 55 字为公开口径（未在本机实测拒绝阈值），简介 1000 字为创作页实测；B 站标题 80 字/简介 2000 字来自已验证的预检代码。
- B 站自动化投稿存在用户协议解释风险；保留 `--yes` 硬门槛、定时发布窗口与 601 熔断（出现即停当天批次）。

## License

MIT，见 [LICENSE](LICENSE)。
