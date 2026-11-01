# 视频发布踩坑档案（本机真实日志复盘 → 可执行避坑清单）

> **范围**：只收**发布环节**（含发布前预检）。渲染/写稿的坑只有在**直接导致发布失败或假成功**时才收。
> **数字**：全部照抄日志原文，保留日期，不估、不四舍五入。
> **检查项 id**：以 `publish.py` 实际输出为准（`doctor` / `check`）。`references/first-principles.md` §4 写的 `dup.fingerprint`，实现里拆成 `dup.submitted` / `dup.uncertain`；`cover.check` 拆成 `cover.exists` / `cover.ext` / `cover.dimension`。
> **取证口径**：pixelcat 日志/回执之外，本机 2026-11-01 已把两个平台的接口逐字段取证落到 `references/contracts.md`（每条带 文件:行）。两者冲突时**以 contracts.md 的实测为准**；`first-principles.md` §5 也已按它修正过一次，典型例子：旧文档写「抖音 exit 2 = 登录态问题」，实测 2 是 `no_video` / `upload_timeout`，脚本自身没有任何登录态检查。
> **验证命令**：每条的「验证方法」是一块可直接复制运行的 PowerShell（本机实测跑过），输出行即期望结果。命令里的 `$VP` 是工具目录、`$PY` 是本机 venv python。

## 0. 总览（13 条）

| # | 一句话 | 机制层 | 本工具拦截的检查项 | 真实后果（日志数字） |
|---|---|---|---|---|
| P1 | B 站预检自写第三套时长阈值 | 阈值 | `video.duration` | 44.67s 合规片 `preflight_failed`，未投稿 |
| P2 | QA 闸门与产出分布不匹配 | 阈值 | `video.duration` `qa.verdict` | 6/9 误杀；10/24、10/25 各 0/3 发布 |
| P3 | 旁白字数当常数 | 换算 | `video.duration` `qa.verdict` | 205 字 ⇒ 53.7s，越过 46–52s 目标区 |
| P4 | 短时长档预算/档位错位 | 阈值 | `video.duration` `qa.verdict` | `char_budget(35)=(120,118)`；36.2s 被判 fail |
| P5 | 跨画幅复用 script.json | 画幅 | `video.dimension` `video.format` `cover.dimension` | `aspect=false` + 字幕 10/21 |
| P6 | 抖音 AI 声明弹窗 disabled 竞态 | 竞态 | `ai.declaration` | 6s 超时，上传完成但作品未发 |
| P7 | 登录态是会话不是授权 | 登录态 | `auth.bilibili.cookies` `auth.douyin.profile` | `stage=start` 且无 `finished_at`＝启动即崩；公众号卡二次扫码 |
| P8 | 标题/简介超限、发送端静默截断 | 字段 | `text.title` `text.desc` `text.tags` | 微信 45003/45004/45110；抖音 `title[:30]` 截断 |
| P9 | 「跑完/定时成功」都不等于平台收到 | 字段/回执 | 回执四要素（见 P9）+ 三态账本 | `stage=start` 无 `finished_at`；请求 00:50 被改成 02:30 而 `scheduled=true` |
| P10 | 成品存在但 QA 未过，任务 rc=0 | 契约/阈值 | `qa.verdict` `asr.cer` `video.duration` | 11/01 产出3/发布1；10/28 三条 `qa.json 不存在` |
| P11 | 封面失败继续发 + 回执缺字段 | 字段 | `cover.exists` `cover.ext` `cover.dimension` | 封面失败率被算成 0.0%（缺字段 7/7） |
| P12 | 槽位撞车与重复投稿 | 竞态 | `dup.submitted` `dup.uncertain` | 两条同落 08:00；同题 02:40 与 08:00 各一条 |
| P13 | 定时窗口是平台硬约束 | 调度 | `schedule.valid` | B 站 <4h 整批失败；抖音 <2h 被平台静默改期 |

**证据等级**：P1/P2/P5/P6/P8/P9/P10/P11/P12/P13 的**现象**均有日志或回执原文；P3/P4 的数字来自日志里的拟合表与验证表；**推断**已在条目内单独标注（P7 的「登录失效只能从无 finished_at 倒推」、P12 的「同题两条是否同一素材」）。

---

## 1. 逐条档案

### P1 · B 站预检自写第三套时长阈值：44.67s 的合规片被判「时长异常」

- **现象**：`status=preflight_failed`「时长异常 44.7s（B站建议 45–55s）」；这支 16:9 成片实测 44.67s，是重渲后的合规产出。
- **根因**（阈值层：同一个判断散在三个文件）：`qa.py` 用 `abs(dur-50)<=2`（48–52s）；`daily.py` 另有一套 45–55s / CER 0.10；`publish_bili.preflight` 是第三套 `abs(dur-50)>5`（45–55s）。发布器读自己那份，QA 过了照样被拦。
- **本工具怎么防**：`video.duration` —— 硬门禁/目标区只在 `rules.default.json → duration` 写一份（20–180 硬门禁，44–56 目标区**只 warn**），`check` 用 ffprobe 实测判定，`submit` 前不再有任何第二套阈值。
- **依据**：`D:\2006L\docs\2026-10-21\pixelcat\logs\known-issues.md:40-44`（现象原文）、`logs/qa-gate-fix.md:72-74`、`references/first-principles.md:119`（§6「已知三套时长阈值漂移，实测因此误杀 6/9」）；实测文件 `out\daily-20261025-1900\16x9\daily-20261025-1900-16x9.mp4` = 44.67s。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p1','video':{'16:9':r'$B\daily-20261025-1900\16x9\daily-20261025-1900-16x9.mp4'},'cover':{'16:9':r'$B\daily-20261025-1900\9x16\covers\cover-16x9.png'},'text':{'title':'repro','desc':'x','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['bilibili'],'schedule':{'bilibili':'auto'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p1.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False));print(p)"
& $PY publish.py check --job "$env:TEMP\p1.json" --target bilibili
```

期望：`✓ video.duration 44.67s`、结论「通过（error 0 / warn 2）」——旧逻辑在此判死，新逻辑放行。

### P2 · QA 闸门阈值与产出分布不匹配：48–52s 卡死 43–47s 主力产出，6/9 被误杀

- **现象**：10/24 日志「发布成功：0/3；成片尝试次数：6；本日失败率：100%」；10/25 同样 0/3。而三条成片齐全、任务 `rc=0`（不是生产失败）。
- **根因**（阈值层）：旧门禁按「目标 50s ±2」设计（48–52s），实测产出集中在 43–47s ⇒ 9 条里 6 条被判死（时长 6 条、CER 2 条且全部与时长重叠）；`daily.py` 还另写了一套 45–55s/0.10。
- **本工具怎么防**：`video.duration` 只把 20–180 当硬门禁、44–56 当目标区（越区只 warn，不拦）；阈值分布不匹配时表现为「通过 + 警告」，不再误杀。`qa.verdict` / `asr.cer` 只消费上游回执，不在发布侧重算第二套。
- **依据**：`logs/qa-gate-fix.md:4-19`、`logs/qa-gate-attribution.md:5-20`（9 条明细）、`logs/daily-20261024.md:14-20`、`logs/daily-20261025.md:65-70`、`references/first-principles.md:119`。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p2','video':{'9:16':r'$B\daily-20261024-0800\9x16\daily-20261024-0800-9x16.mp4'},'cover':{'9:16':r'$B\daily-20261024-0800\9x16\covers\cover-3x4.png'},'text':{'title':'repro','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p2.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p2.json" --target douyin | Select-String -Pattern 'video\.duration|结论'
```

期望：`! video.duration 时长 43.1s 在硬门禁内，但不在目标区 44-56s`、`结论：通过`——放行 + 警告，不再误杀。

### P3 · 旁白字数被当成常数：205 字 ⇒ 53.7s，越过目标区逼近 56s 硬门禁

- **现象**：`205 字按此模型是 53.7s，超出目标区且逼近 56s 硬门禁`；正确值应为 170–186 字（10 场）。
- **根因**（换算层）：把「字数」直接当「时长」的上游约束，没有把实测节奏模型 `duration ≈ 字数/4.8 + 1.1×场次` 反解成字数区间，于是字数超了只能靠重写/裁剪补救，一旦漏了就一路渲到发布口才暴露。
- **本工具怎么防**：本工具**不写稿、不重算字数**（`first-principles.md:50-51,127`：只消费上游回执、不做生产）；它拦后果——`video.duration` 对实际成片复核（目标区外 warn），上游判 `qa.verdict=fail` 时 `qa.verdict` 直接 error 拦。
- **依据**：`logs/known-issues.md:20-23`、`logs/qa-gate-fix.md:43-52`（拟合表：158 字/43.1s、166 字/46.27s、170 字/47.3s）。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p3','video':{'9:16':r'$B\daily-20261023-0800-fix\9x16\daily-20261023-0800-fix-9x16.mp4'},'cover':{'9:16':r'$B\daily-20261023-0800-fix\9x16\covers\cover-3x4.png'},'text':{'title':'repro','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p3.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p3.json" --target douyin | Select-String -Pattern 'video\.duration|结论'
```

期望：`! video.duration 时长 59.47s 在硬门禁内，但不在目标区 44-56s`——成片确实越区，发布侧只放行 + 警告，真正拦住它的是上游 `qa.verdict`（见 P10）。

### P4 · 短时长档整体错位：`char_budget(35)` 返回 (120,118)，36.2s 片被 normal 44–56 档拦

- **现象**：`char_budget(35)` 返回 `(120,118)`（下界 > 上界）；35s 目标片实测 36.2s，被 normal 档 44–56 判 fail。
- **根因**（阈值层 ×2）：① 预算公式给下界设了硬地板 `max(120, center-8)`，短片档 lo>hi，字数指令本身就矛盾；② 档位按「实测时长」选，而 tail 1.2s + 场景 padding 会多出 1–2s，实测 36.2s 掉回 normal 档被拦。档位必须按**目标时长**选。
- **本工具怎么防**：`video.duration`（同一硬门禁，不为短片单独开洞）+ `qa.verdict`（上游按目标时长选档后给的回执）。短档经验值（30–38s）只作为上游 `qa.json.gates.duration_profile` 的证据，不写进发布侧规则表。
- **依据**：`logs/qa-gate-fix.md:76-105`、`logs/daily-20261025.md:96-99`（36.73s 实测 gate PASS(short)）。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p4','video':{'9:16':r'$B\daily-20261026-1200\9x16\daily-20261026-1200-9x16.mp4'},'cover':{'9:16':r'$B\daily-20261026-1200\9x16\covers\cover-3x4.png'},'text':{'title':'repro','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p4.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p4.json" --target douyin | Select-String -Pattern 'video\.duration|结论'
```

期望：`! video.duration 时长 36.73s 在硬门禁内，但不在目标区 44-56s`、`结论：通过`——短档不会再被 normal 门禁误杀。

### P5 · 跨画幅复用 script.json：竖屏工程渲进横屏画布，`aspect=false` + 字幕 10/21

- **现象**：`aspect=false` + `字幕 10/21`；成片是 1920×1080，但内容是竖屏布局，字幕跑到画面中部。
- **根因**（画幅层）：`pipeline.run_one` 的「复用已有脚本」分支按脚本里的 `format=9x16` 建工程（`#root data-width=1080 data-height=1920`），render 却按调用方尺寸 1920×1080 出图 → 竖屏页面塞进横屏画布；字幕探测带（0.74–0.94 高度）自然对不上。修法是复用分支强制 `script["format"]=fmt` 并回写。
- **本工具怎么防**：`video.dimension` 按目标画幅精确校验（1920×1080 / 1080×1920，差一像素即 error）；`video.format` 校验该平台是否配了对应画幅的成片；`cover.dimension` 同步校验封面比例（偏离期望 ±容差 warn，太小 error）。
- **依据**：`logs/known-issues.md:34-38`、`logs/qa-gate-fix.md:72-73`；用 `out\daily-20261024-0800\9x16\…mp4`（1080×1920）冒充 16:9 可复现。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p5','video':{'16:9':r'$B\daily-20261024-0800\9x16\daily-20261024-0800-9x16.mp4'},'cover':{'16:9':r'$B\daily-20261024-0800\9x16\covers\cover-16x9.png'},'text':{'title':'repro','desc':'x','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['bilibili'],'schedule':{'bilibili':'auto'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p5.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p5.json" --target bilibili | Select-String -Pattern 'video\.dimension|结论'
```

期望：`✗ video.dimension 分辨率 1080x1920 ≠ 规则 1920x1080（16:9）`、`结论：拦截`。

### P6 · 抖音 AI 声明弹窗 disabled 竞态：点到不可见的「确定」，6s 超时后作品不发

- **现象**：`stage=ai_declaration_failed`；`Locator.click: Timeout 6000ms exceeded … get_by_role('button', name='确定', exact=True).last … waiting for element to be visible, enabled`。视频已上传、封面已设置、标题已填，卡在这一步直接 return。
- **根因**（竞态层，两层）：① 弹窗里的「确定」在未选中任何声明类型时是 `disabled`（probe 实测 `disabled=True`），选中后才 enabled；② 页面别处还有同名「确定」，`.last` 解析到不可见的那个，于是等到超时。修法：只在可见 `.semi-modal`（含「对作品内容添加声明」）内操作 → 轮询等 `is_enabled()` 变 true → 原生点击失败用 JS 兜底 → 校验「弹窗关闭 + 页面出现 作者声明：内容由AI生成」。
- **本工具怎么防**：`ai.declaration` —— job 必须 `ai_declaration: true` 才允许提交。抖音侧判定收紧成四件事：`success && scheduled && ai_declared` 三者同真；`ai_attempt_1.still_open=False && declared=True`；**强证据「作者声明：内容由AI生成」必须出现在 `body_tail`**（只命中弱条件 → uncertain）；`stage=="ai_declaration_failed"` → failed（作品确实没发，**不是** uncertain）。
- **依据**：`logs/known-issues.md:5-12`、`logs/qa-gate-fix.md:37-41`、`references/first-principles.md:104-106`、`references/contracts.md:120-127`（弱条件误判与三档归类）、`D:\2006L\tools\douyin-auto\publish_daily.py:141-199`。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p6','video':{'9:16':r'$B\daily-20261024-0800\9x16\daily-20261024-0800-9x16.mp4'},'cover':{'9:16':r'$B\daily-20261024-0800\9x16\covers\cover-3x4.png'},'text':{'title':'repro','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':False};p=os.path.join(os.environ['TEMP'],'p6.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p6.json" --target douyin | Select-String -Pattern 'ai\.declaration|结论'
```

期望：`✗ ai.declaration job 没有标记 ai_declaration=true（AI 生成内容必须声明）`、`结论：拦截`。

### P7 · 登录态是浏览器会话，不是一次性授权：失效时表现为「启动即崩」，没有任何登录错误码

- **现象**：`stage=="start"` 且**无 `finished_at`**＝启动即崩（profile 被占 / Chrome 缺失 / 未登录跳转后元素找不到），实测 `douyin-daily-20261029-1900-result.json` 只有 6 个键；公众号侧「点卡片「发表」→ 编辑器 → 勾选协议 → 「确定」→ 弹出【微信验证】扫码」，流程中止，事后复核草稿数仍 11。
- **根因**（登录态层）：能写的条件是「持久化 profile/cookie 还在，且平台没要求二次确认」。注销后进程落到 Session 0、没有交互桌面，headful Chrome 起不来、`douyin-auto/profile` 读不到 = 等于未登录；平台侧还会周期性要求主体本人二次验证，机器无法完成。**旧文档说「退出码 2 ＝登录态问题」，2026-11-01 逐码取证已推翻：2 是 `no_video`/`upload_timeout`，发布器自身没有任何登录态检查（推断：登录失效只能从「无 finished_at / 启动即崩」倒推）。**
- **本工具怎么防**：`auth.bilibili.cookies`（JSON 可解析 + SESSDATA/bili_jct/DedeUserID 齐全 + expires 未过期/剩余天数；biliup 对话里 `验证cookie→无需更新cookie→通过cookie登录` 三行齐全才认定有效）与 `auth.douyin.profile`（profile 目录 + Cookies 文件存在）——**只读本地文件，不发网络请求**；抖音结果缺 `finished_at` → `uncertain`（不是 failed，不重跑），必要时先跑只读的 `check_login.py` 探一次登录。
- **依据**：`references/first-principles.md:13,113`、`references/contracts.md:80,142-151`（2.7 登录失效信号 A–D）、`D:\2006L\docs\2026-10-21\pixelcat\scripts\schtasks-logon-options.md:6,30-31`、`github-tech-radar\HANDOFF.md:476-492`（§3.12 实测）、`logs/daily-20261029.md:9`（风险行）。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;c={'bilibili':{'cookies_path':r'D:\path\to\cookies.json'},'douyin':{'profile_dir':r'D:\path\to\profile'}};open(os.path.join(os.environ['TEMP'],'p7-cfg.json'),'w',encoding='utf-8').write(json.dumps(c));j={'schema':1,'job_id':'repro-p7','video':{'9:16':r'$B\daily-20261024-0800\9x16\daily-20261024-0800-9x16.mp4'},'cover':{'9:16':r'$B\daily-20261024-0800\9x16\covers\cover-3x4.png'},'text':{'title':'repro','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':True};open(os.path.join(os.environ['TEMP'],'p7.json'),'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py --config "$env:TEMP\p7-cfg.json" check --job "$env:TEMP\p7.json" --target douyin | Select-String -Pattern 'auth\.|结论'
```

期望：`✗ auth.douyin.profile Chrome 登录态目录不存在：D:\path\to\profile`、`结论：拦截`（换成 bilibili 目标则报 `✗ auth.bilibili.cookies cookie 文件不存在`）。

### P8 · 标题/简介超限：发送端静默截断，按「字数」估还会撞平台的「字节」限

- **现象**：微信真实接口依次撞 `[45003] title size out of limit`（标题 41 字 / 85 字节 > 64 字节）、`[45004] description size out of limit`（摘要 120 字 / 298 字节 > 120 字节）、`[45110] author size out of limit`。
- **根因**（字段层）：① 限额按平台、有的按**字节**而不是字数，按字数估算必然漏；② 超限有两种表现——接口 4xxx 报错，或客户端**静默截断**（本机抖音上传器直接 `title[:30]`，不报错，后台标题已经变了没人知道；发布页计数是 `x/30`，`body_tail` 里能看到 `12/30` 这种回读值）。
- **本工具怎么防**：`text.title` / `text.desc` / `text.tags` 按 `rules.default.json → platforms.*` 逐平台校验，超限直接报「删 N 字」；标签还查个数上限与逗号（biliup 用逗号分隔标签）。实操建议：**job 里抖音标题按 ≤30 字写**（发送端硬截断），不要贴着 55 字口径写。
- **依据**：`github-tech-radar\HANDOFF.md:521-522,590-595`（缺陷 26/27 + 真实错误码）、`references/contracts.md:93`（`title[:30]` 与页面计数 `x/30`）、`D:\2006L\tools\douyin-auto\publish_daily.py:272`（`title[:30]`）、`rules.default.json:59,74`（55 字口径 + 「按 30 字写」的来源说明）。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p8','video':{'9:16':r'$B\daily-20261024-0800\9x16\daily-20261024-0800-9x16.mp4'},'cover':{'9:16':r'$B\daily-20261024-0800\9x16\covers\cover-3x4.png'},'text':{'title':'这是一条故意写得很长很长的标题用来验证平台上限到底会不会在本地就被拦住而不是等到接口报错一二三四五六七八九十零一二三四五','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p8.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p8.json" --target douyin | Select-String -Pattern 'text\.title|结论'
```

期望：`✗ text.title 标题 60 字 > 上限 55 字（删 5 字）`、`结论：拦截`（错误信息直接给「删几字」）。

### P9 · 「跑完」和「定时成功」都不等于平台收到：日志说失败却没有任何错误码

- **现象**：10/29 19:00 抖音回执只有 `"stage": "start"`（6 个键、无 `finished_at`）；daily 日志记 `回执：success=None scheduled=None ai_declared=None manage=None`。另一份真实回执 `douyin-college-love-20261029-result.json`：`sched_value=02:30 ≠ job 00:50`，而 `scheduled=true`、`success=false`、仍停在 post 页。
- **根因**（字段/回执层，三条）：① 进程启动 ≠ 平台收到；缺 `finished_at` 说明 `finally` 没跑＝进程硬崩，此时回执里没有任何可信结果字段；② `scheduled` 的语义只是「填入时间后按了 Enter 没抛异常」，平台会把过近时段**静默改期**（请求 00:50 改成 02:30）而标志仍 true；③ B 站成功判定只看进程输出里有没有 BV（`--submit client` 在 0.2.4 已失效、WARN 后回退 APP 接口，实测两次投稿都成功，但「解析 BV」这一步更容易落空）。
- **本工具怎么防**：三态账本 `submitted / failed / uncertain` + 抖音回执四要素：① 有 `finished_at`（缺 = 硬崩 → uncertain，不当 failed）；② `success && scheduled && ai_declared` 三真；③ `sched_value` 与请求的 `schedule` 相等（不等 → 仍记 submitted，但备注「平台把定时从 X 改成 Y，勿重投」）；④ 回执 sha256 与本地视频一致（不一致 → uncertain）。B 站必须 exit 0 **且**解析出 `BV[0-9A-Za-z]{10}`，解析不到记 submitted 但要求后台核对；uncertain 永不自动重发，只能 `resolve` 人工结案。
- **依据**：`references/contracts.md:112,117,149,156-157`（scheduled 语义、回执抽样、硬崩信号）、`D:\2006L\tools\douyin-auto\douyin-daily-20261029-1900-result.json:2`、`logs/daily-20261029.md:54-67`、`logs/bilibili-upload.log:12-14`、`references/first-principles.md:14-15,104-109`、`publish.py:1121-1158`。
- **验证方法**：

```powershell
$PY='E:\2006L\guichao\.venv\Scripts\python.exe'
& $PY -c "import json;d=json.load(open(r'D:\2006L\tools\douyin-auto\douyin-daily-20261029-1900-result.json',encoding='utf-8'));print('stage=',d.get('stage'),'finished_at=',d.get('finished_at'),'sched_value=',d.get('sched_value'),'success=',d.get('success'),'-> publishable=',bool(d.get('finished_at')) and all(d.get(k) for k in ('success','scheduled','ai_declared')))"
```

期望：`stage= start finished_at= None sched_value= None success= None -> publishable= False`——该条**不能**算发布成功，只能记 uncertain/失败去后台核对。

### P10 · 成品存在但 QA 未过：零发布，但任务 `rc=0` 看起来「健康」

- **现象**：11/01「第 3 次：FAIL ['CER 0.1689（放行线 0.11）']」；总览「产出 3 条｜QA 放行 1｜QA 未过 2｜发布成功 1」。10/28 三条全是 `FAIL ['qa.json 不存在'] rc=1`，日更任务照样跑完。
- **根因**（契约/阈值层）：① 「没测」（qa.json 缺失）与「测了没过」被混成同一个 fail，发布决策分不清；② 重试序列里**最后一版会覆盖前面 PASS 的那版**（10/29 12:00 第 2 次 PASS → 第 3 次 FAIL 43.967，最终判 qa_fail），而人只看到「成品在」。
- **本工具怎么防**：`qa.verdict`（fail 即 error，不许发布）+ `asr.cer`（>0.13 error；0.11–0.13 warn 仍发）+ `video.duration`（20–180 硬门禁 error；44–56 目标区只 warn）。10/31 那条 62.2s 的真实拦截路径是：上游判 fail → 发布侧 `qa.verdict` error，**不是**发布侧自己又写一套时长阈值。「没测」在发布侧表现为 `asr.cer` / `audio.loudness` 的 warn（`没有 cer 回执`），不会静默变成假 PASS。
- **依据**：`logs/daily-20261101.md:16-20,29-31`、`logs/daily-20261028.md:20-21,31-33`、`logs/daily-20261029.md:47-49`、`logs/daily-20261031.md:29-30`、`out\daily-20261029-1200\9x16\qa.json`（`pass=false`, duration 43.967）。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p10','video':{'9:16':r'$B\daily-20261101-1900\9x16\daily-20261101-1900-9x16.mp4'},'cover':{'9:16':r'$B\daily-20261101-1900\9x16\covers\cover-3x4.png'},'text':{'title':'repro','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'fail','cer':0.1689},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p10.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p10.json" --target douyin | Select-String -Pattern 'qa\.verdict|asr\.cer|结论'
```

期望：`✗ qa.verdict 上游 QA 判定未通过（qa.verdict=fail），不许发布` + `✗ asr.cer CER 0.1689 > 拦截线 0.13`、`结论：拦截`。

### P11 · 封面失败继续发布 + 回执缺字段：失败率被算成 0.0%，假象健康

- **现象**：封面统计「回执数：7｜封面成功：0｜失败：0｜缺字段：7」「**失败率：0.0%**」——7 份回执里 `cover_uploaded` 全是 None。
- **根因**（字段层）：① 上传器封面失败只记 `stage="cover_failed"`，关掉弹窗继续走标题/定时（不 return），所以「封面没换成」也可能发出去；② 回执契约里没有 `cover_uploaded` 字段，统计把「缺字段」当成「没失败」，于是 0/7 成功却报 0.0% 失败率。
- **本工具怎么防**：`cover.exists` / `cover.ext` / `cover.dimension` —— 封面路径必填、格式白名单、最小尺寸与目标比例（B 站 16:9、抖音 3:4），比例偏离 warn（可能被裁）、太小 error，缺了就 error 不投。
- **依据**：`logs/cover-failure-stats.md:7-16`、`D:\2006L\tools\douyin-auto\publish_daily.py:100-126`、`rules.default.json:38-41,65-68`。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os;j={'schema':1,'job_id':'repro-p11','video':{'9:16':r'$B\daily-20261024-0800\9x16\daily-20261024-0800-9x16.mp4'},'cover':{'9:16':r'D:\path\to\cover-3x4.png'},'text':{'title':'repro','desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':'2026-11-02 19:00'},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p11.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p11.json" --target douyin | Select-String -Pattern 'cover\.|结论'
```

期望：`✗ cover.exists 封面不存在：D:\path\to\cover-3x4.png`、`结论：拦截`。

### P12 · 同一槽位撞车与重复投稿：两条都落 08:00，同题在 02:40 与 08:00 各发一条

- **现象**：10/27 数据里「2026年10月26日 08:00」出现两条不同作品；10/26 日更日志记「19:00 这条 cron 没跑到（被 bug 顶掉），01:00 补跑并投 B 站 20:00」。10/29–10/30 数据里同题「大学生不谈恋爱…」在 02:40 与 08:00 各一条。
- **根因**（竞态层）：① 槽位过滤 bug（自带 slot 的 topic 占用了别的循环位）导致 slug/目录与实际槽位错位；② 补跑/重跑与定时任务没有共享「同一素材已投」的事实，于是同槽位、甚至同素材出现两条。**推断**：「同题两条」是否为同一素材未做 sha256 比对（标题、时间、内容形态一致），但重复投稿的代价是确定的（B 站限流/降权、抖音两条同款）。
- **本工具怎么防**：`dup.submitted` / `dup.uncertain` —— 账本按 `sha256(video) + title` 指纹记录；已 submitted 直接 error（并打印上一条回执与时间）；uncertain 先 `resolve` 才能再投；`submitted` 是终态，永不重投。
- **依据**：`logs/daily-20261026.md:12-14,27-28`、`logs/douyin-20261027.md:13-14`、`logs/douyin-20261029.md:10-12`、`logs/douyin-20261030.md:11,13`、`references/first-principles.md:14`。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os,hashlib,datetime;v=r'$B\daily-20261024-0800\9x16\daily-20261024-0800-9x16.mp4';t='repro-p12';h=hashlib.sha256(open(v,'rb').read()).hexdigest();fp=hashlib.sha256((h+'|'+t).encode()).hexdigest();led=os.path.join(os.environ['TEMP'],'p12.jsonl');open(led,'w',encoding='utf-8').write(json.dumps({'job_id':'repro-p12','target':'douyin','state':'submitted','fingerprint':fp,'ts':'2026-11-01 20:00'},ensure_ascii=False)+'\n');open(os.path.join(os.environ['TEMP'],'p12-cfg.json'),'w',encoding='utf-8').write(json.dumps({'ledger':led}));j={'schema':1,'job_id':'repro-p12','video':{'9:16':v},'cover':{'9:16':r'$B\daily-20261024-0800\9x16\covers\cover-3x4.png'},'text':{'title':t,'desc':'x #AI','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['douyin'],'schedule':{'douyin':(datetime.datetime.now()+datetime.timedelta(days=1)).strftime('%Y-%m-%d 19:00')},'ai_declaration':True};open(os.path.join(os.environ['TEMP'],'p12.json'),'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py --config "$env:TEMP\p12-cfg.json" check --job "$env:TEMP\p12.json" --target douyin | Select-String -Pattern 'dup\.|结论'
```

期望：`✗ dup.submitted 这条已经投过（2026-11-01 20:00，回执 {}），不许重投`、`结论：拦截`。

### P13 · 定时窗口是平台硬约束：B 站 >4h、抖音 2h–14d，越界就整批失败或被平台静默改期

- **现象**：`✗ 没有足够的合法定时时段（必须距现在 >4 小时）。不会改成立即发布。`；抖音侧真实案例请求 `00:50` 被平台改成 `02:30` 而 `scheduled` 仍为 true（创作页原文「支持2小时后及14天内的定时发布」）。
- **根因**（调度层）：两个平台都有硬窗口——B 站 `--dtime` 必须距提交 >4 小时；抖音只接受 **2 小时后 ~ 14 天内**。本机只配了 12:00/19:00 两个时段，提交时刻一靠近窗口就取不到合法点；把越界时间「改成立即发布」等于放弃后台取消窗口与风控缓冲，直接填越界时间则会被平台静默改期、日志还以为成功。
- **本工具怎么防**：`schedule.valid` —— 显式定时必须晚于现在；bilibili >4h（`min_lead_hours: 4.05`）；douyin `min_lead_minutes: 120` / `max_lead_days: 14`；`"auto"` 由规则表时段取未来第一个合法点；取不到就 error，**不静默降级**。
- **依据**：`references/contracts.md:95`（2h~14d 原文与 `sched_value` 回读）、`D:\2006L\docs\2026-10-21\pixelcat\logs\bilibili-channel-check.md:39`、`github-tech-radar\docs\07-video.md:102`、`github-tech-radar\scripts\bilibili_upload.py:82-111,408-416`、`rules.default.json:46,69-70`、`publish.py:831-886`。
- **验证方法**：

```powershell
$VP='C:\Users\2006L\Documents\deepseek-harness\default-workspace\video-publish'; $PY='E:\2006L\guichao\.venv\Scripts\python.exe'; $B='D:\2006L\docs\2026-10-21\pixelcat\out'
cd $VP; & $PY -c "import json,os,datetime;j={'schema':1,'job_id':'repro-p13','video':{'16:9':r'$B\daily-20261025-1900\16x9\daily-20261025-1900-16x9.mp4'},'cover':{'16:9':r'$B\daily-20261025-1900\9x16\covers\cover-16x9.png'},'text':{'title':'repro','desc':'x','tags':['AI'],'hashtags':['#AI']},'qa':{'verdict':'pass'},'targets':['bilibili'],'schedule':{'bilibili':(datetime.datetime.now()+datetime.timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')},'ai_declaration':True};p=os.path.join(os.environ['TEMP'],'p13.json');open(p,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False))"
& $PY publish.py check --job "$env:TEMP\p13.json" --target bilibili | Select-String -Pattern 'schedule\.|结论'
```

期望：`✗ schedule.valid 定时 <时间> 距现在不足 4.0h`、`结论：拦截`。把 `targets`/`video` 换成抖音与 9:16、定时改成现在 +1 小时，同一条规则给出 `✗ schedule.valid 定时 <时间> 距现在不足 120 分钟（抖音创作页原文：支持 2 小时后及 14 天内的定时发布）`。

---

## 附：本机验证记录（2026-11-01 实测）

| 命令（见各条「验证方法」） | 实测输出 | 判定 |
|---|---|---|
| P1 44.67s 投 bilibili | `✓ video.duration 44.67s`，通过 | 不误杀 |
| P2 43.10s 投 douyin | `! video.duration 时长 43.1s 在硬门禁内，但不在目标区 44-56s`，通过 | 放行 + warn |
| P3 59.47s 投 douyin | `! video.duration 时长 59.47s 在硬门禁内，但不在目标区 44-56s`，通过 | 放行 + warn |
| P4 36.73s 投 douyin | `! video.duration 时长 36.73s 在硬门禁内，但不在目标区 44-56s`，通过 | 放行 + warn |
| P5 9:16 投 bilibili | `✗ video.dimension 分辨率 1080x1920 ≠ 规则 1920x1080（16:9）` | 拦截 |
| P6 `ai_declaration=false` | `✗ ai.declaration …` | 拦截 |
| P7 cookie/profile 指向不存在 | `✗ auth.douyin.profile …` / `✗ auth.bilibili.cookies …` | 拦截 |
| P8 60 字标题 | `✗ text.title 标题 60 字 > 上限 55 字（删 5 字）` | 拦截 |
| P9 真实回执断言 | `stage= start finished_at= None sched_value= None -> publishable= False` | 不能算成功 |
| P10 `verdict=fail` + `cer=0.1689` | `✗ qa.verdict …` + `✗ asr.cer CER 0.1689 > 拦截线 0.13` | 拦截 |
| P11 封面不存在 | `✗ cover.exists 封面不存在 …` | 拦截 |
| P12 账本已 submitted 同指纹 | `✗ dup.submitted 这条已经投过 …不许重投` | 拦截 |
| P13 B 站定时 = 现在 +1h | `✗ schedule.valid 定时 … 距现在不足 4.0h` | 拦截 |
| P13 抖音定时 = 现在 +1h | `✗ schedule.valid 定时 … 距现在不足 120 分钟（抖音创作页原文：支持 2 小时后及 14 天内的定时发布）` | 拦截 |
| P13 抖音定时 = 现在 +15d | `✗ schedule.valid 定时 … 超过 14 天上限，平台会拒绝或静默改期` | 拦截 |

未验证：真实平台提交（本档案只做本地预检复现，未发布任何东西）；抖音后台 AI 声明的实际展示、平台风控与审核延迟属于人眼/后台，机器无法验证。

---

## § 发布前 60 秒人工核对（机器查不出来的）

> 这一节只写**机器确实查不出来**的。`check` 全绿只代表 C1–C5 在本地成立，不代表平台展示与观感正确。
> 顺序：先跑 `check`（0 网络、<3s）→ 再做这 60 秒 → 最后才 `submit --yes`。

1. **画面崩坏 / 花屏 / 黑屏 / 字幕错行 / 关键文字被裁**
   机器为什么查不出：工具只做 ffprobe（能解码 ≠ 画面对）+ 读上游 `qa.json` 的 `black/subtitles`（那是上游自测，不是逐帧复核）。
   60 秒怎么查：2× 速扫首尾各 3 秒 + 中间随机 3 处，盯字幕有没有压住人脸/被切边。
2. **BGM 授权**
   机器为什么查不出：音频流存在、LUFS/TP 正常，不代表有权商用。本机现用 BGM 是从参考片音轨提取的第三方音频（非原创、非 CC0）。
   60 秒怎么查：确认曲目来源；不确定就把 `video.bgm_path` 置空重渲——宁可没 BGM。
3. **平台后台「AI 声明」是否真的显示**
   机器为什么查不出：工具只能保证字段提交了（B 站 `creation_statement.id=1`、抖音回执 `ai_declared=true`），平台怎么展示只有人眼看。
   60 秒怎么查：创作中心 → 稿件详情/作品页，找「内容由AI生成」标识；没有就立即停自动上传、改手动。
4. **封面在客户端的实际裁切**
   机器为什么查不出：只校验像素尺寸与比例，不掌握平台裁切策略，也不防「首帧当封面」的替换。
   60 秒怎么查：用手机看作品列表里的封面缩略图（3:4 竖封面最容易被裁）。
5. **标题在后台/客户端的实际显示**
   机器为什么查不出：本地按字数/字节校验，客户端还会二次截断；抖音发送端本身就会 `title[:30]`。
   60 秒怎么查：在后台标题框里核对**开头 30 字**是否完整、有没有被平台改写。
6. **账号里是否已存在同款**
   机器为什么查不出：账本只记本机经手的投递；手动传的、别的机器传的、上次失败的都不在账本里。
   60 秒怎么查：创作中心列表搜标题前 12 字，确认没有重复稿件。
7. **新号「低质批量」风控**
   机器为什么查不出：平台风控模型不公开；本机日志只留了风险提示（一天 3 条同系列有被判低质批量的风险）。
   60 秒怎么查：看后台有没有「仅自己可见 / 审核中」的异常状态；出现就当天停发，别硬刷。
8. **音画同步与手机外放可听性**
   机器为什么查不出：`qa.sync` 是上游自测；LUFS/TP 不反映手机扬声器的低频损失（该 BGM 约 72% 能量在 20–60Hz）。
   60 秒怎么查：手机外放听开头 5 秒，看字幕与语音是否同拍、人声是否清楚。
9. **「需要主体本人确认」的通道（公众号/视频号一类）**
   机器为什么查不出：平台把「发表」定义成需要主体本人二次扫码（本机实测卡在「微信验证」），不是代码能绕的。
   60 秒怎么查：这类通道固定成「机器到草稿 + 通知人点一下」，不要排进自动发布。
10. **定时时间是否真落在平台允许窗口**
    机器为什么查不出：`schedule.valid` 只校验时间窗口（B 站 >4h、抖音 2h–14d）与规则表时段；平台后台可能因审核/排队略微推迟，也可能被平台改成待审。
    60 秒怎么查：提交后看后台「定时发布」列表里的时间，与回执里的 `dtime` / `schedule` 对一遍。
