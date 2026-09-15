# 更新日志 (Changelog)

本文件记录 DBAuto 的重要变更。按合并 / 发布倒序排列。

> 说明：底层工程治理与安全加固（P0 / P1 层级）已由仓库既有提交等价实现并合并至 `master`
> （例如 `500571b` 依赖锁定 + Dependabot + CI 漏洞扫描、`d465526` Docker 非 root + ruff 门禁、
> `9563551` 前端构建 minify + 删除重复登录页，以及更早的 P0 治理基线）。

## [当前] 修复仪表盘转存统计口径 — 改配置被算作转存成功

> 对应 [issue #8](https://cnb.cool/ciweicc/DBAuto/-/issues/8) 追加反馈
> 「修改配置时。修改配置的记录会归类到转存成功的计数里」。

- **缺陷**：执行历史（`exec_history`）是混合流水，`transfer`（转存）、`config`（改配置）、
  `expired_check`（失效检测）写在同一张表里。`compute_dashboard_stats()` 用
  「排除 `expired_check`」的**黑名单**口径统计，于是**每次保存设置产生的 `config` 记录
  都被当成一次转存**：概览页「今日转存」每改一次配置 +1，「N 次/周」同步虚高。
- **附带误报**：`config` 记录没有 `data`（`data=None`），只走 `last_status` 分支，
  把「上次成功」覆盖成「上次无有效结果」，并在「待办事项」里误报。
- **复现**（修复前）：1 次真实转存 + 2 次改配置 + 1 次失效检测 →
  `today_count=3`（应为 1）、`week_total=4`（应为 1）、`last_status=none`（应为 `success`）。
- **修复**：统计口径改为**白名单** `TRANSFER_RECORD_TYPES = ("transfer",)` +
  `is_transfer_record(record)`，`today_count` / `week_total` / `daily` / `last_status`
  四个统计点统一走该判定。黑名单→白名单的收益是：将来新增记录类型默认**不**参与
  转存统计，而不是默认被计入。
- **未受影响**：「执行历史」页面仍展示全部类型（含「配置」筛选页签），
  失效检测仍不参与转存统计（原有行为不回归）。
- **回归断言**：新增 `tests/test_dashboard_stats.py`（14 项），覆盖
  「改配置不计入今日转存 / 不计入 N 次/周 / 不改写上次状态 / 只有配置记录时统计为空 /
  未知类型不计入（白名单语义）/ 真实失败转存仍被统计」。
- **验证**：`pytest tests/` 178 项通过（164 → 178）；`ruff check .` 通过；
  `bash static/src/build.sh` 重建产物逐字节幂等（本次只改后端统计口径，前端产物无变化）。
## [当前] 侧边栏实时任务日志修复（第二轮）— 抽屉无入口 + 超宽屏日志栏被挤出

> 对应 [issue #11](https://cnb.cool/ciweicc/DBAuto/-/issues/11) 的**第二次反馈**
> 「问题依旧存在」。上一轮修复（下方那条）解决的是「默认折叠 + 不定位到最新 + 340px 上限」，
> 但**没有覆盖用户实际所处的那条路径**，因此反馈复现。

用户截图（浏览器视口约 1200px，窗口 2560px 全屏）逐像素分析后确认两个独立根因：

- **根因 1：≤1200px 的日志抽屉没有任何可用的打开入口。**
  ≤1200px 时 `.log-panel` 变成 `position:fixed; transform:translateX(100%)` 的覆盖层，
  **只有 `.open` 能把它移回可视区**，但三条入口都可能只切 `.collapsed`：
  1. 首屏 `restoreLogPanelState()` 直接照搬宽屏时期的 `logPanelCollapsed=1` 记忆；
  2. 从宽屏把窗口缩到 ≤1200px 时没有任何归一化，残留的 `.collapsed`
     被 `width:48px !important` 压回 48px 轨道；
  3. FAB 的 `toggleLogDrawer()` 只 `toggle('open')`，未清 `.collapsed` ——
     即使加上了 `.open`，`.collapsed` 的 `!important` 仍把面板压成 48px 轨道，
     看起来"点了没反应"。
  截图实测：面板左边界 = 2560（= 视口右边界，整块在屏幕外）、右下角只有一个
  48px 圆形 FAB 被挤出视口仅剩 19px 可见（y 167~209）。

- **根因 2：超宽屏下主列被拉宽到 2020px，`.content` 居中后左侧空出约 290px 空白带。**
  `.app` 第三列是 `minmax(0,1fr)`，2560px 下主列 2020px；
  而 `.content{max-width:1440px;margin:0 auto}` 只把内容居中 ——
  于是内容被挤到主列右侧、左边空一大片，日志栏却被顶到屏幕最右边。
  截图像素证据：日志区左边界 x=2513，视口宽 2560，日志栏"贴死"右边缘。
  这是"日志栏跑到屏幕外"的观感来源。

修复：

- **新增 `.log-panel.open` 抽屉态规则**：`position:fixed; right:0; left:auto;
  width:min(360px,88vw); min-width:0`。`min-width:0` 是关键 ——
  基础态 `.log-panel{min-width:260px}` 与 1024 断点的兜底规则会覆盖抽屉宽度，
  算出负宽度后内容被压没。
- **新增 `.log-panel.open.collapsed` 兜底**：`width:min(360px,88vw) !important`，
  保证叠态时不会塌回 48px 轨道。
- **新增 `normalizeLogPanelForViewport()`**：宽窄视口切换时归一化 class ——
  窄屏丢弃 `.collapsed`、宽屏丢弃 `.open`，并同步 scrim。
  `resize` 时**立即**调用（不等 150ms 防抖），避免宽→窄期间日志栏消失在屏幕外。
- **`restoreLogPanelState()` 增加窄屏分支**：折叠记忆是宽屏点的，
  窄屏首屏一律丢弃，保证抽屉有可用初始状态。
- **`toggleLogDrawer()` 打开抽屉时一并清 `.collapsed`** 并调
  `positionLogDrawerAtLatest()`；`closeDrawers()` 只在窄屏移除 `.open`，
  避免切页签把宽屏常驻日志栏也关掉。
- **新增 `positionLogDrawerAtLatest()`**：抽屉从屏幕外归位时 `scrollHeight`
  要在归位那一帧才确定，故补一次 `requestAnimationFrame` 定位。
- **移动端抽屉宽度拉满**：≤640px 时 `.log-panel.open{left:0;right:0;width:100%;
  max-width:100%;min-width:0}`（此前 390px 视口实测仍有 47px 停在屏幕外）。
- **超宽屏主列夹宽**：新增 `@media(min-width:1901px)`，把主列限制为
  `minmax(0,1440px)` 并用 `justify-content:center` 分配剩余空间，
  消除左侧空白带（实测 2560px 下空白带 290px → 0）。

验证：

- `scripts/check_log_panel_dom.py` 扩展为 **10 组视口 + 折叠记忆用例**，
  新增断言：日志栏必须落在视口内、主列左侧空白带上限、
  以及**「带 `logPanelCollapsed=1` 打开窄屏页面并点 FAB」**用例。
  **判别力验证**：把脚本跑在修复前的产物上 → 报出 8 处问题
  （4 组窄屏 × 「FAB 点不开 / 一条日志都没有」）；跑在修复后 → 0 处问题。
  超宽屏实测：`mainGutterLeft` 290px → **0px**，`panelX` 2220 → 1930（不再贴死右边缘）。
- 窄屏抽屉实测（点 FAB 后）：1200px→面板 840~1200（宽 360）、1100px→740~1100、
  1024px→664~1024、390px→0~390，均 `atBottom=true`、`visibleLines=61`；
  带折叠记忆的场景同样通过。
- `tests/test_build.py` 新增 2 项回归断言（`test_log_panel_drawer_reachable_from_every_entry`
  覆盖三条入口 + `.open` 的 `min-width:0`；`test_ultrawide_content_has_no_blank_gutter`
  覆盖超宽屏夹宽），并把走查脚本入口断言同步到新指标。
- `python -m pytest tests/ -q` → **187 项通过**；`ruff check .` 通过；对比度 18 组达标；
  `scripts/check_overview_dom.py` 9 组视口 0 处问题（无回归）；
  `bash static/src/build.sh` 重建产物逐字节幂等。

## [当前] 侧边栏实时任务日志可见性修复

> 对应 [issue #11](https://cnb.cool/ciweicc/DBAuto/-/issues/11)「侧边栏的实时任务日志。无法显示完全」。

- **修复窄屏默认折叠导致的「日志整体消失」**：`switchTab` 里有一条
  「概览页 + 窄屏（≤1200px）+ 用户未显式选择 → `collapseLogPanel()`」的分支。
  结果是 1200 / 1100 / 1024 / 390 这些宽度下**一打开页面日志栏就被压成 48px 轨道**
  （`logLines` 实测 `visibleLines=0`，一条日志都渲染不出来），移动端更彻底（宽 0px）。
  该自动折叠现已移除：默认展开，「给内容更多空间」交给用户显式点折叠按钮。
  新增 `restoreLogPanelState()` 取代原来的 DOMContentLoaded 分支 —— **只有**
  `localStorage.logPanelCollapsed === '1'`（用户自己折叠过）才恢复折叠。
- **修复窄屏「点展开没反应」**：≤1200px 日志栏是 `translateX(100%)` 的覆盖层，
  `expandLogPanel()` 只去掉 `.collapsed` 仍停在屏幕外。现在窄屏展开会同时加 `.open`，
  FAB / 折叠按钮才真正把抽屉推到可视区（实测展开后 280~390px、日志区 359~580px）。
- **修复日志不定位到最新**：`renderLog` / `applyLogSearch` 只渲染、不改 `scrollTop`，
  状态同步后停在最旧一行（实测 `scrollTop=0`、最新一条在 5544px 之外），
  用户自然读成「日志显示不完全」。现在渲染后统一回到末尾（暂停时除外），
  并新增常驻提示行 `#logHint`（「最新 N 条 · 已定位到末尾」/「已暂停滚动 · 共 N 条」），
  提示随 `scroll` 事件实时更新。
- **解除日志区 340px 硬上限**：旧 `max-height:340px` 在 900px 高的屏幕上只用了约 1/3 屏
  （实测日志区 340px、视口内仅 4 行）。现改为 `max-height:max(340px, calc(100dvh - 320px))`，
  1366x900 实测 580px（约 2 倍），1920x1080 实测 760px；移动抽屉内 `max-height:none`
  并让日志卡片纵向铺满。
- **超长日志行续行缩进**：折行后续行顶到行首，与时间戳正文错位、观感像被截断；
  现在对折行的行标记 `.wrapped`，用 `padding-left:2em` + `text-indent:-2em` 让续行与正文对齐
  （窗口 resize 后重新判定）。**不再用 `-webkit-line-clamp` 或截断文案来「解决」显示问题。**
- **验证**：
  - 新增 `scripts/check_log_panel_dom.py`（DOM 度量走查，9 组视口）。它在**修复前**的产物上
    报出 32 处问题（含「打开页面日志栏默认折叠」「日志区高度 340px」「窄屏 FAB 无效」
    「一条日志都没渲染」），在修复后报 0 处问题 —— 即修复前失败、修复后通过，具备判别力。
    > 注：该脚本依赖 playwright + chromium。本机 `pip install playwright` 后
    > 还需补齐 `libatk/libgbm/libnss3` 等系统库，否则 chromium 启动即缺共享库退出；
    > 脚本按仓库既有约定在缺少依赖时以退出码 0 跳过。
  - `tests/test_build.py` 新增 6 项回归断言（自动折叠已移除、默认展开 + 显式恢复、
    窄屏展开进抽屉、高度非硬编码、定位到最新 + 续行缩进、提示行存在），并把原
    `test_log_panel_not_auto_collapsed_on_desktop` 从「仅桌面态」收紧为「任何视口都不得自动折叠」。
  - `python -m pytest tests/ -q` 185 项通过（含 master 本次并入的 14 项统计口径断言）；`ruff check .` 通过；
    对比度 18 组全部达标；`scripts/check_overview_dom.py` 9 组视口 0 处问题（无回归）；
    构建产物重建逐字节幂等。

## [当前] 前端 UI 优化第六批 — 概览页显示密度与信息优先级重构

> 对应 [issue #8](https://cnb.cool/ciweicc/DBAuto/-/issues/8)「对概览页的显示密度和优先级提出优化方案」，
> 承接 `docs/UI_Roadmap_Next.md` 第十节。

- **首屏由「三段式竖排」改为两列**：旧布局是「4 张 KPI 卡 → 工作区（最近转存 + 状态/待办）
  → 热门推荐」纵向堆叠，900px 视口下「热门推荐」要从 y=560~904 才开始，必然落在折叠线以下。
  现在「转存概览 + 最近转存」与「热门推荐」同处首屏：1920/1600/1440/1366/1280/1200/1024
  实测首屏底边 816px，整体落在一屏内（脚本 `scripts/check_overview_dom.py` 可复跑）。
- **KPI 卡改为行式指标列表**：卡片布局在中间宽度必须折成 2×2，导致卡片高度在
  93/103/132px 之间随窗口漂移、行内出现 81/93 参差（`min-height` 还会掩盖这种高度差）。
  行式布局（标签 + 值 + 副信息 + 动作，行高固定 54px）没有这个退化路径。
- **移除重复信息**：「转存库」卡片与「最近转存」面板标题重复，删除并改由
  面板副标题（`最近 10 条 / 共 13 条`）与概览头部（`转存库 13 条 · 上次成功`）表达。
- **信息优先级**：状态/待办只展开「需要关注」的项（未配置调度 / 上次失败），
  常态项压成一行 `运行状态：全部正常 · v1.1.0`；最近转存由硬编码 8 条提升到面板容量 10 条，
  取消面板内滚动，「查看全部 → 历史记录」成为唯一出口；热门推荐降级到首屏之后。
- **修复状态语义错误**：存储里 `status` 为 `exists`（幂等跳过）的条目此前一律渲染成
  绿色「已转存」。现按 `ok/done`、`exists/skipped`、`fail/error/invalid`、`downloading`
  分别渲染「已转存 / 已存在跳过 / 失败 / 进行中」，未知状态回退看 `shareurl`。
- **修复空日期排序**：`date` 为空串的条目在降序比较下会排到列表**最前**并显示为 `-`；
  现统一显示 `—` 且排到末尾，日期格式统一为 `MM/DD HH:MM`。
- **修复热门推荐海报恒为占位图**：后端契约是 `items:[{poster,title,year,rating}]`，
  前端读的是 `results/poster_path/release_date/vote_average`，字段全部落空。
- **修复移动端分类前缀被压成竖排单字**：≤640px 卡片化时 `.ov-table-cat` 实测宽 10px，
  原因是 `table-layout:fixed` 的列宽仍作用于 block 化后的行；现改为 `display:block`
  且前缀按内容取宽（实测 30px），文案也由 `movie/tv` 改为「电影 / 剧集 / 综艺」。
- **概览两列自适应改用主列实测宽度**：`@media(max-width:1350px)` 在 1280~1440 会误判
  （`viewport:1440` 因滚动条得 `innerWidth=1425`，但主列实宽 846~920px 完全放得下），
  改为 `ResizeObserver` 观测主列宽度并设置 `.app[data-ov-narrow]`（阈值 860px）。
- **补齐缺失样式 / 清理死代码**：补 `ov-dot-warn/-error`、`ov-badge-skip/-fail/-run/-muted`、
  `ov-todo-icon-*`、`ov-rec-skel`；删除 `.ov-status-dot`、`.ov-st-*`、`.ov-todo-danger/-warning/-info`、
  `.ov-rec-btn/-rating`、`.ov-cell-title`、`.ov-kpi-bar/-card/-label/-value-row/-trend/-schedule`、
  `.ov-workspace/-main-col/-side-col`、`.ov-health-item/-name/-val` 等 10 类死 CSS。
- **补齐 `html[data-density="standard"]` 令牌分支**：此前密度令牌只在 comfortable/compact
  下覆盖，用户从紧凑切回标准档时不会复位。
- **验证**：构建产物重建逐字节幂等；`pytest tests/` 164 项通过（`test_build.py` 22 → 32 项）；
  `ruff check .` 通过；对比度 18 组全部达标；新增 `scripts/check_overview_dom.py`
  在 9 组视口下断言「首屏一屏内 / 指标行等高 / 无横向滚动 / 状态与日期语义 / 移动端前缀宽度」，
  0 处问题。走查 6 个 Tab 无横向溢出、无新增 JS 报错。
- **回归断言**（`tests/test_build.py` 新增 10 项）：首屏布局、宽度驱动而非媒体查询、
  跳过状态区分、取消三重截断、日期排序与格式、推荐字段契约、面板内滚动、骨架屏与死 CSS、
  堆叠规则集中、指标行文本度量统一。

## [当前] Agent 交付规范落库 — 长任务规范 / 读图配额 / 完成门禁

> 对应 [issue #5](https://cnb.cool/ciweicc/DBAuto/-/issues/5)（CI/CD 流水线构建失败诊断）。

- **失败定位**：`cnb-jpb-1k2illr5i` 的失败 Stage 是 `npc go`（NPC 自身运行），
  错误为 `[LLM request error model=deepseek-v4.1-flash] 500: {"message":"Internal error: request entity too large"}`，
  与代码、`.cnb.yml`、测试无关。根因是 Agent 在 UI 走查阶段连续 `Read image`
  多张 400–980KB 截图（第 48–51 轮仍在读图），把上下文推过模型请求体上限，
  重试 2 次后终止。**同一根因已连续报废两次运行（`cnb-it8-1k2iis46q`、`cnb-jpb-1k2illr5i`），
  且两次都因此未能产出 PR** —— 问题不在单次运行，而在「约束只存在于当次会话」。
- **修复（把约束落库）**：
  - 新增根目录 `AGENTS.md`：仓库速览、校验命令、**自动化长任务规范**（后台任务必须
    `nohup` + 重定向、禁止拉取 SSE 长连接、禁止无参数 `git status`/`git diff`、
    **读图硬配额 ≤ 8 张/任务且 ≤ 2 张/轮**、禁止 `cat` 约 220KB 的产物）、
    **任务完成门禁**（先本地验证再交付、验证手段必须落库、改 src 必须重建产物、必须产出 PR、不轮询 CI）。
  - 新增 skill `.cnb/skills/dbauto-local-verify/SKILL.md`：可复用的本地验证流程，
    「廉价证据（DOM 度量/断言脚本）优先，截图仅用于断言表达不了的视觉问题」。
- **防复发断言**（`tests/test_build.py` 新增 4 例）：`AGENTS.md` 必须存在、
  必须记录上下文超限教训并给出读图硬上限、必须含任务完成门禁、验证 skill 必须落库。
- **顺带澄清**：`static/index_new.html` 作为**提交入库的产物**（CI 有漂移门禁）不是本次失败原因；
  它带来的是 Agent 侧上下文成本，已通过「禁止 cat 产物、产物校验交给 `tests/test_build.py`」约束。

## [当前] 前端 UI 优化第五轮 — 设置页视觉与信息架构重构

> 对应 [issue #1](https://cnb.cool/ciweicc/DBAuto/-/issues/1)「对这个项目的 UI 动线视觉设计进行优化」
> 的第二次追加（首次为第四轮），承接 `docs/UI_Roadmap_Next.md` 第九节。

- **设置页分区快速定位**：页面约 3 屏高（内容 1806px / 视口 900px）却原本只有 1 个分组、
  无任何定位入口。新增 4 个 chip（数据源 / 转存服务 / 转存路径 / 认证与缓存），点击展开
  目标分组并平滑滚动；滚动时 IntersectionObserver 联动高亮。跳转期间挂起 spy，
  避免平滑滚动途中的中间分组抢走高亮。
- **分组头升级为「图标 + 名称 + 说明 + 条目数」**：原 2 分组重构为 4 个语义分组，
  折叠态下也能判断分组内容，`summary` 由 47px 增至 62px。
- **卡片等高与底部动作对齐**：同组卡片 `align-items:stretch` + 卡片内 `flex-direction:column`，
  动作位 `margin-top:auto` 贴底；行内卡片高度由 343/303 参差修正为完全一致。
- **结构化字段说明**：新增 `.form-hint` 组件替换 inline `style="font-size:12px..."` 写法，
  为 6 处关键字段（PanSou 用途、QAS 用途与 Token 语义、三类路径规则、认证改动后果）补充说明。
- **保存条重构**：替换原「一张空卡 + 右下按钮」结构，改为「说明 + 主按钮」，
  并在输入改动后切换为橙色「有未保存的改动」提示，保存成功后复位。
  未采用 `position:sticky` 吸底——实测悬停时会在滚动中段盖住说明文字与下一张卡片标题。
- **修复 `#icon-lock` 未定义**：设置页「管理认证」标题图标此前渲染为空白。
  同时把「刷新豆瓣缓存」从卡片标题行拆出为独立「缓存维护」卡片。
- **响应式**：断点统一到全局 1500/1350/1200/1024/640；单卡分组限宽 560px
  （避免 736px 巨型单卡）；≤1200px 主列变宽时用 `auto-fit` 而非硬性单列；
  ≤640px 单列 + chips 横向滚动。
- **回归断言**（`tests/test_build.py` 新增 5 项）：分区索引与目标分组齐全、
  分组说明覆盖、保存条替换旧空卡且禁用 sticky、卡片等高布局、字段说明覆盖，
  以及「产物引用的图标必须全部在 sprite 中定义」（可捕获本次 `icon-lock` 这类缺陷）。
- **验证**：构建产物重建幂等无漂移；`pytest tests/test_build.py` 18 项通过；
  `ruff check .` 通过；对比度审计 18 组全部达标；11 组「视口 × 主题」走查横向溢出 0、
  JS 报错 0。

## [当前] 修复 CI 产物漂移门禁恒失败 — 构建指纹改为内容哈希

> 对应 [issue #3](https://cnb.cool/ciweicc/DBAuto/-/issues/3)（CI 失败诊断）的后续跟进。
> 上一轮引入的「产物漂移检查」门禁存在自相矛盾设计，对**任何** PR 都恒失败。

- **缺陷**：`static/src/build.sh` 把 `git rev-parse --short HEAD` 写入产物指纹注释
  （`// sha:<HEAD>`），而 CI 的「Frontend build drift check」是在当前提交上重建产物后
  要求 `git diff --quiet` 为空。提交号随每次 commit（尤其 merge commit）变化，
  重建必然产生一行 diff —— 该门禁实际**对任何分支/PR 都无法通过**，
  与本轮提交目标（防止只改 src 未重建）背道而驰。
  实测：HEAD `6642745`（merge commit）下重建，产物 `sha:58fcfd4 → sha:6642745`，`git diff` 非空。
- **修复**：指纹改为由构建输入（CSS + JS + body）派生的内容哈希 `// hash:<sha256 前 12 位>`，
  与提交号解耦。同一份源文件重建逐字节一致（幂等），漂移检查恢复真实判别力：
  只有「只改 src 未重建」或「手改产物」才会触发 diff。`build.sh` 与 `build.py` 同步修改。
- **回归断言**（`tests/test_build.py` 新增 2 例）：
  `test_build_fingerprint_is_content_based` 断言指纹为 `hash:` 且构建脚本可执行语句中
  不再出现 `rev-parse`；`test_build_rebuild_is_idempotent` 断言重建后产物不变。

## [当前] 前端 UI 优化第四轮 — 撤销交互 / 对比度门禁 / 构建守护

> 对应 [issue #1](https://cnb.cool/ciweicc/DBAuto/-/issues/1)「对这个项目的 UI 动线视觉设计进行优化」，
> 承接 `docs/UI_Roadmap_Next.md` 第三批遗留项。

- **执行历史「撤销」（路线图 3.3）**：清空执行历史后 5s 内可一键撤销。
  `storage.clear_exec_history()` 清空前留存内存快照，新增 `restore_exec_history()`；
  `/api/exec_history/manage` 增加 `restore` 动作（无快照返回 409）。前端 `showToast` 支持行动按钮，
  「已清空 N 条执行历史 · 撤销」点击即恢复。快照仅保留最近一次，符合单实例约束，不引入软删除列。
- **对比度审计与修复（路线图 4.1）**：新增 `scripts/check_contrast.py`（解析双主题令牌、
  合成半透明前景、计算 WCAG 2.1 对比度），报告落盘 `docs/UI_Contrast_Report.md`。
  审计发现浅色主题语义色作小字时对比度仅 **1.97–3.70:1**，已按同色相压暗修复至 ≥4.76:1
  （`--accent/--green/--red/--orange/--purple/--cyan`），深色主题保持不变。
- **构建产物守护（路线图 5.2）**：新增 `tests/test_build.py`——括号配平、打包 JS `node --check`、
  `build.sh` 模块清单完整性、关键令牌/选择器入产物、想看功能零残留、撤销链路接线。
- **CI 门禁**：`.github/workflows/tests.yml` 新增「产物漂移检查」（重建后 `git diff` 必须为空）
  与「对比度审计」两步。
- **修复概览页桌面态日志栏被自动折叠至 48px（P0）**：`switchTab('overview')` 此前无条件
  `collapseLogPanel()`，叠加「`.log-toggle-btn` 有 CSS 无 DOM」与「FAB 仅切移动端抽屉」，
  导致 1280px 桌面下执行日志栏折叠后**无法还原**、核心功能不可见。现已加窄屏判定与
  用户偏好尊重、补上展开按钮（含 aria）、桌面态 FAB 改为展开折叠栏；
  1280px 下日志栏由 48px 恢复为 290px。回归断言见 `tests/test_build.py`。
- **历史 UI 提交补记**（此前三次提交未入 CHANGELOG，本次补齐）：
  - `3021388` 移除豆瓣想看同步 + 完成三批前端 UI 优化（tokens 2.0 / 栅格统一 / 移动端表格卡片化 /
    backdrop-filter 降级 / 设置页重构）；
  - `410498f` 修复概览页执行日志面板在 1300–1500px 视口被挤压至不可见（P0 功能缺失）；
  - `58fcfd4` 修复 1200–1500px 区间主列过窄导致的 KPI 断行与日志栏显示不全。

## P2 — 文档与仓库治理

> 本仓库的 P2 层级聚焦于**文档补充与单实例约束说明**，详见下方条目。

- **文档漂移修复（P2-10）**：`README.md` 同步至当前真实状态——Python 3.12 要求、非 root 容器
  （`appuser` uid 1000）、PBKDF2 600000、依赖锁文件、CI 质量门禁、完整环境变量表；并新增
  「横向扩展与单实例」章节。
- **新增 `docs/SCALING.md`（P2-12）**：说明单进程 / 单实例状态模型与水平扩展限制。
- **新增 `CONTRIBUTING.md`（P2-10）**：开发环境、测试、分支策略、代码风格与依赖锁工作流。
- **新增 `.env.example`（P2-10）**：枚举所有环境变量（含占位值，无真实密钥）。
- **仓库清理（P2-11）**：移除一次性产物——`UI_Audit_报告.html` 与 `static/login_preview.html`
  （原为 git 跟踪文件，已取消跟踪并删除）；`.gitignore` 补充生成物目录（`.pytest_cache/` 等）
  并忽略本地真实 `.env`（保留 `.env.example` 跟踪，避免密钥误提交）。
- **单实例启动提示（P2-12）**：`app_modules/main.py` 增加非阻断的启动日志，检测到多 worker
  （`WEB_CONCURRENCY` / `GUNICORN_WORKERS > 1`）时给出 WARNING。
- **补回 `LICENSE`（AGPL-3.0）**：治理基线文件（远程历史中缺失，此处补回）。

## 历史基线（已由既有提交等价实现）

### P1 — 安全加固与工程治理
- 依赖锁定、CI 质量门禁（ruff lint、前端构建漂移检查）、安全加固（PBKDF2 600000 + XFF 信任统一
  + 旧哈希自动迁移）、非 root 容器、Trivy / pip-audit 扫描。
- 这些变更已由仓库既有提交（如 `500571b`、`d465526`、`9563551`）等价实现。

### P0 — 治理基线
- `LICENSE`（AGPL-3.0）、固定 Python 3.12、CI 依赖与路由测试 + 覆盖率门禁、项目结构与文档基线。
- 治理基线已由既有提交与本次补回的 `LICENSE` 共同覆盖。

---

> 更早期历史请参见 git 提交记录（`git log`）。
