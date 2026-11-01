# 发布执行器真实调用契约（取证版）
> 取证：2026-11-01 20:15–20:30 CST。**未执行任何 upload/append/login/renew**。依据＝`文件:行` 或命令回执原文；无实测支撑标 **未验证** 并给验证法。cookie 只写键名/类型，值已全部剔除。

## 1. B 站：biliup.exe
| 项 | 值 | 依据 |
|---|---|---|
| 可执行 | `D:\2006L\biliup\biliupR-v0.2.4-x86_64-windows\biliup.exe` | `bilibili_upload.py:86`、`publish_bili.py:30` |
| 版本 | `biliup-cli 0.2.4` | `biliup.exe --version` → `biliup-cli 0.2.4`，exit=0 |
| 子命令 | login/renew/upload/append/show/dump-flv/download/list | `biliup.exe --help` 原文 |

全局参数（必须放在子命令**之前**）：`-p/--proxy`；`-u/--user-cookie <FILE>`（默认 `cookies.json`）；`--rust-log`。依据：`biliup.exe --help`。

### 1.1 `upload` 完整参数表（原文照录）
| 参数 | 默认 | 说明 / 格式化要求 | 生产必给 |
|---|---|---|---|
| `[VIDEO_PATH]...` | — | 位置参数，可多个（配置文件投稿时不用） | **是** |
| `--submit` | `client` | `client,app,web` | 是 |
| `--title` / `--desc` | 空 | 文本 | 是 |
| `--tag` | 空 | **逗号分隔字符串**，不能传数组 | 是 |
| `--tid` | `171` | 分区（我们用 231） | 是 |
| `--cover` | 空 | 封面路径 | 是 |
| `--dtime` | 无 | **10 位时间戳**，距提交 >4 小时（见 1.2） | 是 |
| `--copyright` | `1` | 1-自制 2-转载 | 是（1） |
| `--no-reprint` | `0` | 0-允许转载 1-禁止转载 | 是（1） |
| `--extra-fields` | 空 | 自定义提交参数 JSON（见 1.5） | 是 |
| `--dynamic` | 空 | 空间动态 | 否 |
| `-c,--config` | — | 自定义配置文件 | 否 |
| `-l,--line` | — | `bda2,ws,qn,bldsa,tx,txa,bda,alia` | 否 |
| `--limit` | `3` | 单视频最大并发数 | 否 |
| `--source` | 空 | 转载来源 | 否 |
| `--interactive` / `--mission-id` | `0` / — | — | 否 |
| `--dolby` / `--hires` / `--open-elec` | `0` | 0-关闭 1-开启 | 否 |
| `--up-selection-reply` / `--up-close-reply` / `--up-close-danmu` | 无 | **仅 `--submit app` 时可用** | 否 |

依据：`biliup.exe upload --help`（exit=0）全文；「生产必给」列＝参考实现与真实回执出现过的字段，非 biliup 强制。

### 1.2 `--dtime` 确切语义
- 语义：**绝对 Unix 时间戳、10 位（秒）**，本身即绝对时刻（无时区歧义）；平台硬要求「距提交 >4 小时」，否则要么被拒、要么只能立即发。
- 依据（帮助原文）：`--dtime <DTIME>  延时发布时间，距离提交大于4小时，格式为10位时间戳`（`biliup.exe upload --help`）。
- 依据（真实回执）：`bili-receipt.json` 中 `--dtime 1793509200` 与 `"dtime": "2026-11-01 13:00"` 同一条；实测 `1793509200` = `2026-11-01 13:00:00 +08:00`，提交时刻 `00:53:45` → 提前 12h06m。
- 老脚本「>4 小时」依据：`bilibili_upload.py:98` `earliest = current + timedelta(hours=4, minutes=5)`；`:414` 报错文案；`publish_bili.py:227` `if ts - now < 4*3600 → dtime_too_early`；`bilibili-channel-check.md:39`。
- 换算：`int(datetime.strptime(s,"%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8))).timestamp())`（`publish_bili.py:190-193`）。

### 1.3 成功回执与解析
真实 stdout（`pixelcat/logs/bilibili-upload.log:14`，原样截断）：
```
ResponseData { code: 0, data: Some(Object {"aid": Number(117536561633327), "bvid": String("BV1xmxJ6AEg9"), ...}), message: "OK", ttl: Some(1) }
APP接口投稿成功
```
| 目标 | 正则 | 依据 | 实测 |
|---|---|---|---|
| BV 号 | `BV[0-9A-Za-z]{10}` | `bilibili_upload.py:43`、`publish_bili.py:248` | 命中 `BV1xmxJ6AEg9` ✅ |
| aid | `"aid"\s*:\s*(?:Number\()?(\d+)` | 日志原样 | 旧正则 `"aid"\s*:\s*(\d+)`（`bilibili_upload.py:44`）匹配不到 `Number(…)` → aid 恒 `None`（缺陷，按本行修正） |

```python
BV_RE  = re.compile(r"BV[0-9A-Za-z]{10}")
AID_RE = re.compile(r'"aid"\s*:\s*(?:Number\()?(\d+)')
def parse(out):                                  # 成功 = returncode==0 且 bv 非空
    bv, aid = BV_RE.search(out), AID_RE.search(out)
    return (bv and bv.group(0)), (int(aid.group(1)) if aid else None)
```
耗时量级（同次真实投稿，`bilibili-upload.log`）：15,055,691 B / 45.76s 视频，上传 6.37s @2.37MB/s，进程总耗时 00:53:45→00:54:08＝**23s**（`--limit 3` 并发）。

### 1.4 退出码 / 三态（`bilibili_upload.py:478-502`，与 `first-principles.md:56-63` 一致）
| 情形 | 判定 | 依据 |
|---|---|---|
| 预检失败（进程未启动） | `failed`（可修后重跑） | `:457-464` |
| exit 0 且解析到 BV | `submitted`（终态） | `:478-493` |
| exit 0 但无 BV | 仍 `submitted` + error「上传成功但未解析到 BV，请到后台核对」 | `:486` |
| 超时（`TimeoutExpired`→returncode None） | `uncertain`，**永不重试** | `:263-265,495` |
| 非 0 退出（进程已启动） | `uncertain` | `:494-502` |
| 任一条 uncertain | 停止整批后续条目 | `:502` |

⚠️ 偏差：`publish_bili.py:250` 把非 0 记 `failed`，与设计契约冲突，**以 bilibili_upload.py 为准**。

### 1.5 `--submit client|app|web` 真实差别
| 值 | 实测 | 依据 |
|---|---|---|
| `client` | **v0.2.4 已失效**：`WARN 客户端接口已失效, 将使用APP接口` → `APP接口投稿成功` | `bilibili-upload.log:12,14`；`vibe-ai-hallucination/publish/upload-v04.log:15,17`（两次真实投稿同一现象） |
| `app` | APP 接口；额外支持 `--up-selection-reply/--up-close-reply/--up-close-danmu` | `upload --help`「仅提交接口为app时可用」 |
| `web` | **未验证**（无任何历史回执用 web） | 验证法：一条测试稿显式 `--submit web`（属投稿动作，需授权） |

推论：传 `client` 实际走 APP ⇒ 当前版本 `client`≈`app`；不要用 `--submit` 去影响 AI 声明。

### 1.6 `--extra-fields`（AI 声明）
- 结构：`{"creation_statement":{"id":1}}`，`id=1`＝「内容由 AI 生成」。依据：真实回执 args 原文 `"--extra-fields" "{\"creation_statement\": {\"id\": 1}}"`；`bilibili_upload.py:237-240`（`ensure_ascii=False, separators=(",",":")`）；`publish_bili.py:135,221`；`07-video.md:101`；`bilibili-channel-check.md:26`。
- 我们侧预检：`creation_statement.id == 1`，否则 `failed`（`bilibili_upload.py:195-196`）。
- **效果未验证**：biliup 接受该参数且 exit 0，但「AI 生成」标记是否落到稿件上，无任何事后核对记录；`07-video.md:13-14` 明写「B 站后台的 AI 声明…仍需人工复核」。验证法（只读，未执行）：`biliup.exe --user-cookie <ck> show <BV>`（`show`=打印视频详情，见 `--help`）或创作中心稿件详情人工看。

### 1.7 cookie 文件结构（只写键名/类型，值已剔除）
文件 `D:\2006L\biliup\cookies.json`（biliup 自管格式，2386 B）。依据：PowerShell `ConvertFrom-Json` 结构回执。
| 路径 | 类型 | 说明 |
|---|---|---|
| `cookie_info.cookies[]` | 对象数组，7 项 | 每项键＝`name,value,expires,http_only,secure,same_site` |
| `.cookies[].name` | str | 实测集合：`SESSDATA, bili_jct, DedeUserID, DedeUserID__ckMd5, sid, default_sites, sec_ck` |
| `.cookies[].expires` | **Int32，10 位＝Unix 秒** | 7 项同值 = `2027-01-29 00:45 CST`，相对 now(1793535473) 剩 **88.2 天** |
| `cookie_info.domains` | 数组 | 域列表 |
| `token_info` | 对象 | 键 `access_token, expires_in, mid, refresh_token`；`expires_in`＝8 位整数（**秒**） |
| `sso` / `platform` | 数组(3) / str | — |
- 必需键（C3）：`SESSDATA`＋`bili_jct`（写操作 CSRF）＋`DedeUserID`，缺任一 → `error`。依据：`bilibili_upload.py:46` 的关键键正则、`publish_bili.py:78-79` 的 `has_sessdata/has_bili_jct`。
- 本地过期判断（0 网络）：`now_epoch >= min(c.expires)` → 过期；**秒**，不是毫秒（毫秒为 13 位）。
- 登录态权威判定：biliup 对话里自动验一次，stdout 出现 `验证cookie`→`无需更新cookie`→`通过cookie登录` 三行即有效；缺任一/报刷新失败 → 登录失效（依据：本次 `list` 回执原文，见 1.8）。

### 1.8 只读登录验证（本次已执行）
`biliup.exe --user-cookie D:\2006L\biliup\cookies.json list -m 1` → **exit=0**，第 1 页 **10 条**；前 3 条：`BV1xmxJ6AEg9`「MCP 不是智能，是接口：你给的权限正在变成后门？」、`BV147xz6sEag`「五万四千星的代码助手，真能替你写需求吗？」、`BV1qSxF6FEvz`「健身AI给的方案差点练废人？二百四十五道评分题才测出真相」。输出仅日志行＋BV/标题/状态列，无 cookie 值（已核）。

## 2. 抖音：douyin-auto/publish_daily.py
| 项 | 值 | 依据 |
|---|---|---|
| 调用 | `<venv-python> publish_daily.py --job <job.json>`，`--job` **required** | `publish_daily.py:203-206` |
| 生产解释器 | `D:\2006L\tools\douyin-auto\.venv\Scripts\python.exe`（实测 `Python 3.12.10`，`import playwright` OK） | `pixelcat/tools/daily.py:43,286` |
| 浏览器 | 有头 Chrome（`channel="chrome"`, `headless=False`, `--start-maximized`），持久化 profile `D:\2006L\tools\douyin-auto\profile`（实测存在，20 项含 `Default`；Chrome 156.0.8078.76） | `publish_daily.py:222-227` |

### 2.1 job JSON 全字段
| 字段 | 必填 | 类型/格式 | 说明 | 依据 |
|---|---|---|---|---|
| `video` | ✅ | 绝对路径 | 不存在 → `stage=no_video`, return 2 | `:207,218-220` |
| `title` | ✅ | str | 代码截断 `title[:30]`；页面计数 `x/30` | `:272`；body_tail 含 `12/30` |
| `desc` | ✅ | str，可含 `\n` | `insert_text` 直填；页面计数 `/1000` | `:274-278` |
| `schedule` | ✅ | **`"YYYY-MM-DD HH:MM"`**，本地时间，无秒/无时区 | 填入 `placeholder="日期和时间"` 后按 Enter；平台只接受 **2h 后 ~ 14 天内** | `:210,297-299`；`:41` body_tail 原文「支持2小时后及14天内的定时发布」 |
| `cover` | 建议 | 绝对路径 | 缺/不存在 → `stage=cover_missing`，**继续跑** | `:256-264` |
| `slug` | 否 | str | 仅决定默认 shot_dir/result 名 | `:211-212` |
| `shot_dir` | 否 | 绝对路径 | 默认 `<ROOT>/shots-daily/<slug>` | `:211` |
| `result` | 否 | 绝对路径 | 默认 `<ROOT>/douyin-<slug>-result.json` | `:212` |
| `dry_run_cover` | 否 | bool | 只做封面上传后退出（0=成功 / 6=失败） | `:256,265-270` |

真实 job 文件键并集：`cover, desc, result, schedule, shot_dir, slug, title, video`（`job-*.json` 全量扫描）；`dry_run_cover` 从未出现。

### 2.2 结果 JSON 全键（30 键，实测 `douyin-daily-20261101-1200-result.json`）
| 键 | 类型 | 含义 |
|---|---|---|
| `stage` | str | `start`→`submitted`/`no_video`/`upload_timeout`/`cover_failed`/`cover_missing`/`ai_declaration_failed`/`schedule_failed`/`cover_dry_run_done` |
| `job`,`video`,`title`,`schedule` | obj/str | 回抄 |
| `sha256` | str | 视频内容哈希（文件缺失则为空串）`:215` |
| `shot_page,shot_uploaded,shot_meta,shot_ai,shot_sched,shot_done,shot_manage` | str | 截图绝对路径 |
| `progress` | str[] | 每 15s 一条页面正文，最多留 12 条 `:242-243` |
| `uploaded` | bool | 页面出现「重新上传」或「删除视频」`:245-248` |
| `cover,cover_target_1,cover_preview_changed,cover_uploaded,cover_error` | mixed | 封面结果 |
| `ai_attempt_1..3` | obj/str | `{still_open, declared}` 或异常串 `:189,193` |
| `ai_declared` / `ai_err` | bool / str | 见 2.4；三轮失败写 `ai_err` `:285` |
| `scheduled` | bool | **只要 fill+Enter 未抛异常就 true，不代表平台接受** `:302` |
| `sched_value` | str | 输入框**回读值** → 必须与 job.schedule 比对 `:303` |
| `sched_err` | str | 设置失败原因 `:306` |
| `url_after` | str | 点发布后的 URL `:325` |
| `success` | bool | 见 2.3 |
| `body_tail` | str | 点发布后页面正文末 1500 字 `:327` |
| `in_manage` / `manage_has_scheduled` | bool | 管理页是否含 `title[:12]` / 「定时发布」字样 `:335-336`；**实测成功投稿 `manage_has_scheduled` 也是 false**（10-31、11-01 两份），不可作判据 |
| `manage_err` / `finished_at` | str | 管理页异常 / `%Y-%m-%dT%H:%M:%S`；**缺失＝进程硬崩** `:339,343` |

### 2.3 `success` 的确切条件
`r["success"] = any(k in body for k in ("定时发布成功","发布成功","已发布","审核中"))`（`:326`）——**发布页正文命中 4 个关键词之一**。必要非充分：生产判定＝`success && scheduled && ai_declared` 三真（`pixelcat/tools/daily.py:291`；`first-principles.md:96`）。`已发布` 极弱（历史作品列表满页都是），必须叠加 `url_after` 与 `sched_value` 回读。

### 2.4 `ai_declared` 判定（含已知坑）
代码（`:188-191`）：`declared = ("作者声明：内容由AI生成" in body) or ("自主声明" in body and "内容由AI生成" in body)`，且弹窗已关（`still_open=False`）；最多 3 轮。
| 风险 | 说明 | 依据 |
|---|---|---|
| 弱条件字面匹配 | 上传页模板本身含「自主声明 请选择自主声明」；未勾选也可能两串同现 → 误判 true | `douyin-daily-20261031-1200-result.json:41` 原文 |
| 历史 disabled 竞态 | 「确定」未选类型时 `disabled`，且页面有同名不可见按钮 → 旧代码点到不可见元素等到超时，`stage=ai_declaration_failed`，作品**不发布**（静默拦截） | `known-issues.md:5-12` |
| 现状 | 已改为等 `is_enabled()` 变 true（24×250ms≈6s）+ 弹窗内 JS 兜底点击 | `:156-179` |

**我们的适配器必须校验**：① `ai_declared is True`（硬门）；② `ai_attempt_1.still_open=False 且 declared=True`；③ 强信号 `作者声明：内容由AI生成` 必须出现在 `body_tail`，只命中弱条件 → `uncertain`；④ `stage=="ai_declaration_failed"` → `failed`（作品确实没发，**不是** uncertain）。

### 2.5 return 码语义（代码实际，非文档）
| 码 | 触发 | 依据 | 归类 |
|---|---|---|---|
| `0` | `success==True`；或 `dry_run_cover` 且 `cover_uploaded` | `:346,270` | submitted |
| `2` | **两种语义**：`stage=no_video`（视频不存在）/ `stage=upload_timeout`（420s 未传完） | `:218-220`、`:233,250-253` | 前者 failed；后者 uncertain |
| `3` | 走完全程但 `success` 为假 | `:346` | failed |
| `4` | 三轮 AI 声明未确认 `ai_declaration_failed` | `:288-290` | failed |
| `5` | 定时发布设置抛异常 `schedule_failed` | `:304-309` | failed |
| `6` | `dry_run_cover` 且封面上传失败 | `:265-270` | failed |
| 其他(1/异常) | 启动阶段抛未捕获异常（launch/goto/set_input_files） | `:222-232` | **uncertain**（无 `finished_at`＝硬崩） |

⚠️ `first-principles.md:98` 写「退出码 2 ＝登录态问题（不可重试）」——**与代码不符**：2 是 `no_video`/`upload_timeout`，脚本**自身没有任何登录态检查**。按本表修正。

### 2.6 重试 / 截图
- **不重试发布动作**（无外层循环，一次失败即 return）。内部重试仅 3 处：封面 2 次（`:61`）、AI 声明 3 轮（`:143`）、「确定」轮询 24×250ms（`:156-164`）。
- 时长常量：上传等待 ≤420s（`:233`）；发布后 3s+8s；管理页 12s（`:313,323,333`）。
- 截图目录＝`job.shot_dir`（默认 `shots-daily\<slug>`），固定名：`01-page / 02-uploaded / 03b-cover-dialog-N / 03c-cover-applied / 03-meta / 04-ai-declared / 05-scheduled(或 05-schedule-failed) / 06-done / 07-manage`，失败另存 `cover-fail-HHMMSS`（`:118,123`）。

### 2.7 Chrome profile / storage-state / 登录失效信号
| 项 | 事实 | 依据 |
|---|---|---|
| profile | `D:\2006L\tools\douyin-auto\profile`（持久化，实测存在） | `:224` |
| `storage-state.json` | **发布链路不读**；仅 `login.py` 扫码后写出 | `login.py:14,58`；全目录 grep 仅 login.py 命中 `storage_state` |
| 登录检查 | `check_login.py`（只读）：`logged_in = bool(cookies ∩ {sessionid, sessionid_ss, sid_guard})`，输出 `check-login.json/.png` | `check_login.py:11,25,33` |
| 失效信号 A（推荐前置） | `check-login.json` 的 `logged_in=false` / `matched=[]` | `check_login.py:25-27` |
| 失效信号 B | 结果 `stage=="start"` 且**无 `finished_at`**（启动即崩：profile 被占/Chrome 缺失/未登录跳转后元素找不到） | 实例 `douyin-daily-20261029-1900-result.json`(6 键)、`douyin-fix1-20261029-result.json`(8 键，均无 finished_at) |
| 失效信号 C | `stage=="upload_timeout"` + return 2（420s 无「重新上传」） | `:233-253` |
| 失效信号 D | `stage=="start"` 且有 `finished_at`（try 内抛异常，退出码非 0，stderr 带 traceback） | `:342-345` finally 语义 |

### 2.8 真实结果 JSON 抽样（3 份）
| 文件 | stage | success | ai_declared | scheduled | sched_value | 其它 | 结论 |
|---|---|---|---|---|---|---|---|
| `douyin-daily-20261031-1200-result.json` | submitted | true | true | true | `2026-10-31 12:00`＝job | `in_manage=true`，30 键齐全 | **真成功** |
| `douyin-college-love-20261029-result.json` | submitted | **false** | true | true | **`02:30` ≠ job `00:50`** | 仍在 post 页，`in_manage=false` | **假 scheduled**：平台把过近时段改成 02:30，标志仍 true |
| `douyin-daily-20261029-1900-result.json` | start | — | — | — | — | 6 键、无 `finished_at` | **硬崩** → uncertain |

`body_tail` 可提取的成功证据（供代码消费）：`定时发布成功|发布成功|已审核?中` 关键词 + `作者声明：内容由AI生成` + `日期和时间` 输入框回读值。

## 3. 环境探针（本机实测）
| 探针 | 回执 | 结论 |
|---|---|---|
| `D:\2006L\tools\ffmpeg\bin\ffprobe.exe -version` | `ffprobe version n7.1`，exit=0 | 可用 |
| `D:\2006L\tools\ffmpeg\bin\ffmpeg.exe -version` | `ffmpeg version 7.1-essentials_build-www.gyan.dev`，exit=0 | 可用（含 loudnorm/ebur128） |
| `E:\2006L\guichao\.venv\Scripts\python.exe -V` | `Python 3.12.10`，exit=0（`requests` 可导入） | 可用 |
| `D:\2006L\tools\douyin-auto\.venv\Scripts\python.exe -V` | `Python 3.12.10`，`import playwright` OK | **douyin 生产解释器** |

响度测量（JSON、可 grep、单次直读）：
```powershell
& 'D:\2006L\tools\ffmpeg\bin\ffmpeg.exe' -hide_banner -nostats -i <video> -af loudnorm=print_format=json -f null - 2>&1
```
实测（`daily-20261101-1200-9x16.mp4`，16,782,836 B / 45.76s）：**1743 ms**，JSON 键 `input_i/input_tp/input_lra/input_thresh/output_i/output_tp/output_lra/output_thresh/normalization_type/target_offset`，本次 `input_i=-14.65`、`input_tp=-2.14`。量级 ≈ **28.6x 实时**（50s 片约 1.7s，3min 片约 6s）。
```python
def loudness(ffmpeg, path):
    cp = subprocess.run([ffmpeg,"-hide_banner","-nostats","-i",path,"-af",
        "loudnorm=print_format=json","-f","null","-"], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=600)
    d = json.loads(re.findall(r"\{[^{}]*\}", cp.stderr)[-1])   # 取最后一个 JSON 块
    return {"lufs": float(d["input_i"]), "tp": float(d["input_tp"]), "lra": float(d["input_lra"])}
```

## 4. 适配器必须落地的硬校验
| # | 校验 | 不满足 |
|---|---|---|
| 1 | B 站 `dtime_ts - now > 4*3600` 且为 10 位整数秒 | failed，绝不改成立即发布 |
| 2 | B 站 `--tag` 逗号串 / `--extra-fields` JSON 用 `list2cmdline` 传递，避免引号炸裂 | 进程不启动 → failed |
| 3 | B 站 cookie 三键存在且 `min(expires) > now`（秒）；不确定时跑只读 `list` 看是否 `通过cookie登录` | failed（先修登录） |
| 4 | B 站 exit 0 且解析到 BV ⇒ submitted；无 BV ⇒ submitted＋强制人工；超时/非 0 ⇒ uncertain 不重试 | — |
| 5 | B 站 AI 声明只认「参数已传」，不认「已生效」；后台复核前打 `ai_declared=unverified` | warning，不阻断 |
| 6 | 抖音 `schedule` 必须在 `[now+2h, now+14d]`，本地拦（平台会静默改期） | failed，不启动浏览器 |
| 7 | 抖音 `sched_value == job.schedule` 才算 scheduled | 不等 ⇒ failed（哪怕 `scheduled=true`） |
| 8 | 抖音 `success && scheduled && ai_declared` 三真＋强文案 `作者声明：内容由AI生成` | 缺一 ⇒ failed |
| 9 | 抖音结果无 `finished_at` / 仅 `stage=start` / return ∉ {0,2,3,4,5,6} ⇒ uncertain，不重试 | — |
| 10 | 抖音 return 2 必须按 `stage` 分：`no_video`⇒failed，`upload_timeout`⇒uncertain | — |
| 11 | 抖音发布前先跑 `check_login.py`（只读），`logged_in=false` 即 failed | — |
| 12 | 两平台 `submitted` 只来自回执证据（BV / 三真），永不因 exit 0 单独判定 | — |

## 5. 已验证 / 未验证
**已验证（本次实测回执）**：biliup 0.2.4 版本、upload/list 全参数、`list` 登录态 10 条、cookie 结构与 expires 语义（秒/剩 88.2 天）、`--dtime` 语义与 10 位时间戳换算、client→APP 回退（2 次历史＋1 次真实回执）、BV/aid 在 stdout 的位置与正则命中、真实 args 与 `--extra-fields` 原文、投稿耗时量级、douyin job 全字段与结果 30 键、return 码 0/2/3/4/5/6、截图命名、profile 与 storage-state 事实、3 份真实结果 JSON、ffmpeg/ffprobe/python 可用性与 loudness 实测耗时。

**未验证（附验证法）**：
1. `--submit web` 行为——一条测试稿显式传 web（属投稿动作，需授权）。
2. B 站「AI 生成」声明是否生效——`biliup.exe --user-cookie <ck> show <BV>`（只读）或创作中心稿件详情人工核对。
3. `client` 回退 APP 后 `creation_statement` 是否仍被接收——同 2。
4. B 站标题/简介/标签平台侧上限（80/2000/10 或 12）——目前只有参考脚本本地阈值（`bilibili_upload.py:181-188` 用 ≤80/≤2000/1–10；`publish_bili.py:179-186` 用 ≤80/≤2000/≤12），平台真限未验证。
5. 抖音 `desc` 1000 字是否硬限——来自页面计数 `94 / 1000`，实测未触发。
6. 抖音「未登录」时的确切退出码——代码无登录检查，只能由 `check_login.py` 前置判定。
