---
name: 'video-publish'
description: '视频一键投稿（B 站 / 抖音）：把「成品视频 + 元数据」变成可核验的发布回执。第一性原理五道门（素材/元数据/登录态/幂等/回执）全部前移到 0 网络、3 秒内的本地预检；有指纹账本防重复投稿，uncertain 结果绝不自动重投。用于「把这条视频发出去 / 投 B 站 / 发抖音 / 定时投稿 / 发布失败了怎么办」。'
---

你是「发布调度员」。不要重新发明发布流程，也不要临场写上传命令——**用 `publish.py`，先 check 再 submit**。

【第一性原理：一次发布成立 = 5 个条件同时成立】
C1 素材可被平台接受（文件/解码/分辨率/画幅/时长/帧率/音轨）｜C2 元数据合法（标题/简介/标签/封面/AI 声明）｜C3 账号可写（cookie / 浏览器登录态在）｜C4 提交唯一且幂等（同素材不投两次）｜C5 结果可判定（有回执 ID 或明确 uncertain）。
**推论：发布环节真正的成本不是上传，是「发现条件不成立」太晚。** 所以 check 必须 0 网络、<3 秒、一次看全部条件；submit 必须复跑 check 才动手。

【三步速通】
1) 装一次：`powershell -ExecutionPolicy Bypass -File <技能目录>\install.ps1`
   它会探测本机 ffprobe / biliup / cookie / 抖音 Playwright 发布器，写 `~/.video-publish/config.json`，并把技能装进 `~/.dsh/skills/video-publish`。
2) 写 job（唯一事实来源，见 `examples/job.example.json`）：视频两个画幅、封面、标题/简介/标签、qa 回执、定时、`ai_declaration: true`。
3) 发布：
   ```
   python <技能目录>\publish.py check  --job job.json      # 0 网络预检，先看红叉
   python <技能目录>\publish.py plan   --job job.json      # 确认将要提交的参数
   python <技能目录>\publish.py submit --job job.json --yes # 真投；--dry-run 只打印
   ```

【命令表】
| 命令 | 作用 |
|---|---|
| `doctor` | 环境自检：ffprobe/ffmpeg/biliup/cookie 有效期/抖音登录态/时区/账本 |
| `check --job` | 五道门逐条判定，error 会拦、warn 放行并记录；加 `--deep` 会**整段解码**+实测响度（+2~4 秒），能抓出「容器头正常但载荷截断」的坏文件，并核对上游 qa 数字 |
| `plan --job` | 只打印参数：B 站 biliup argv（含 `--dtime`/`--extra-fields`）、抖音 job.json |
| `submit --job --yes` | 先跑 check（**默认强制整段解码 + 实测响度**，约 +4s，可用 `--skip-deep` 跳过）；同素材已投过直接跳过；单条占位锁防并发双投；三态落账本 |
| `status` | 账本（job × 平台的最新状态与回执） |
| `resolve --job --target ... --state submitted|failed --note` | **只给 uncertain 用**：人工核对后台后结案 |

`--target all`（或省略）= 按 job.targets 全投；`--target bilibili` 只投一个平台。
同一 job_id 换过素材要重投，必须显式加 `--republish`；上一轮 uncertain 要强投加 `--force-uncertain`（默认都拦住）。

【平台差异（规则表 `rules.default.json` 是唯一来源，别在别处再写阈值）】
- B 站：画幅 16:9 / 1920×1080；`tid=231`、`copyright=1`、`no_reprint=1`；AI 声明走 `--extra-fields {"creation_statement":{"id":1}}`；`--dtime` 必须比现在晚 **>4 小时**，`schedule: "auto"` 会取配置里的时段自动算。
- 抖音：画幅 9:16 / 1080×1920；标题/简介/话题进 job，`schedule` 形如 `2026-11-02 12:00`，必须落在 **[现在+2h, 现在+14 天]**（平台会静默改期）；**成功必须** 结果 JSON 有 `finished_at` ∧ `success` ∧ `scheduled` ∧ `ai_declared` ∧ body 里出现强证据「作者声明：内容由AI生成」∧ 回执 sha256 与本地一致；登录态是持久化 Chrome profile，只有真跑才知道还在不在。

【失败处置（照这个来，别自作聪明）】
| 看到 | 做什么 |
|---|---|
| `uncertain` | **绝不重投**。去平台后台看有没有该稿件，再用 `resolve` 结案。重复投稿在 B 站会限流、抖音会出现两条同款。 |
| B 站 601 / 风控 | 停当天批次，明天再传；不要缩短间隔硬刷。 |
| 抖音结果 JSON 缺 `finished_at` | 进程硬崩（启动即崩/登录失效/profile 被占）：先修环境（只读 `check_login.py` 看 `logged_in`），再决定；**不许直接重跑**。 |
| 抖音 `sched_value` ≠ 你要的时间 | 平台静默改期了。作品已发，**不要重投**；需要精确时段去创作中心改。 |
| 抖音退出码 2 | 看 stage：`no_video` 是 failed（视频路径错）、`upload_timeout` 是 uncertain（可能传了一半，先看后台）。**2 不是登录态问题**。 |
| CER > 0.13 / qa.verdict=fail | 这是上游生产的问题，**不许绕过**：回生产环节重渲，别在这里找开关。 |
| 标题超限 | 按 check 给的「删 N 字」改，改完重跑 check。 |

【纪律】
- 公开投稿必须显式 `--yes`；没有 `--skip-check`、没有 `--force`。
- 平台限额只从 `rules.default.json` 读；新增限额必须写 `source`（实测 / 代码内已有约束 / 平台公开说明），不许猜。
- 任何「成功」都要有回执：B 站 BV/aid、抖音 result JSON 的 `success/scheduled/ai_declared` 与 sha256。日志写成功但回执为空的，按 uncertain 处理。
- 不在本技能里做生产（渲染/写稿/TTS/QA 测量），只消费上游证据。
- 报给用户时给「已验证 / 未验证」两栏，未验证就写未验证。

【已知边界】
- Windows 为主；`biliup.exe` 版本 0.2.4 已实测；B 站外部调用只有 biliup 一个入口。
- 抖音依赖 `D:\2006L\tools\douyin-auto\publish_daily.py` + Chrome 持久化 profile；headless 不行，会弹浏览器。
- 平台后台的「AI 声明显示」「封面裁切」最终仍要人眼看一次：机器只能保证字段提交了，不能保证平台展示正确。
- 详细契约见 `references/contracts.md`，踩坑见 `references/pitfalls.md`，设计推理见 `references/first-principles.md`。
