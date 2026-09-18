# 更新日志 (Changelog)

本文件记录 DBAuto 的重要变更。按合并 / 发布倒序排列。

## [当前] 概览页收敛历史入口 + 折叠恒常列（issue #39）

> 对应 [issue #39](https://cnb.cool/ciweicc/DBAuto/-/issues/39) 用户诉求：
> 截图框选概览首屏右侧「最近转存」面板，指「这个模块的存在没有实际意义」。

**先做了一次判据澄清：删入口，不删面板**

上一轮已删掉该面板底部的「打开历史 →」，只留面板头的「查看全部 →」。
但左侧导航栏本来就有「历史」项，而概览页的「转存概览」面板头还挂着
「历史记录 →」——**三个入口去向完全相同**（都是 `switchTab('history')`）。
按上一轮同一条判据（同去向入口只留一个），本轮把概览页内这两个也删掉，
历史入口收敛到左侧导航唯一一个。

面板本身**保留**：`最近转存`（资源级，`/api/history` / `transfer_history`，
回答「我库里有啥」）与历史页（任务级，`/api/exec_history`，回答「上次跑成啥样」）
不是同一张表，字段与能力都不同，删任一个都丢信息。

**第二部分：恒常列折叠**

用户截图里「库内」列 4 行全是「是」，「链接」列 4 行全是「—」，
一列恒等于常量、一列恒为空，都是纯占位列。改为**按需列**：

- 判据只看**本屏渲染出的行**：有任意一行「不在库内」才渲染「库内」列，
  有任意一行带分享链接才渲染「链接」列；
- 折叠掉的列把结论并进面板副标题（`均在库内` / `暂无分享链接`），
  信息不丢，用户不必为了「都入库了没」再扫一遍列表；
- 表头与表体用同一个 `data-ov-col` 值对应（`lib` / `link`），
  列可见性只由 `setOvRecentCol()` 决定，避免两处判据漂移；
- 空态 `colspan` 跟着可见列数走（`3 + showLib + showLink`）。

**改了哪些文件**

- `static/src/body.html`：删「转存概览」面板头的「历史记录 →」与「最近转存」
  面板头的「查看全部 →」；两个可选列表头加 `data-ov-col`；首载占位行 `colspan` 5 → 3。
- `static/src/scripts/overview.js`：新增 `setOvRecentCol()`；`renderOvRecent()`
  按需拼 td、按需算 colspan、并把折叠结论并进副标题。
- `static/src/styles/main.css`：新增 `.ov-table thead th[hidden]{display:none}`
  （折叠后的列不得残留列宽）；面板注释同步改为「出口收敛到左侧导航」。
- `tests/test_build.py`：
  - `test_overview_recent_has_single_history_entry` →
    `test_overview_has_no_history_entry_at_all`（概览页内同去向入口应为 0，
    且左侧导航的「历史」必须仍在）；
  - `test_product_dist_has_no_duplicate_history_link` 产物层面同步收紧；
  - 新增 `test_overview_recent_constant_columns_are_folded`（判据 / 结论并入副标题 /
    colspan / 表头标记 / 隐藏规则）。
- `scripts/check_overview_dom.py`：新增 4 类渲染后断言 —— 常量列必须折叠且表体无该 td、
  有区分度的列不得折叠、表头可见列数 == 表体单元格数（防列错位）、
  副标题必须含折叠结论；另加「概览页内无 `switchTab('history')` 入口」。

**验证证据**

- `python3 -m pytest tests/ -q` → **306 passed**；`ruff check .` 通过；
  `python3 scripts/check_contrast.py` → 18 组受检 0 组未达标。
- `python3 scripts/check_overview_dom.py --serve` → **9 组视口 0 处问题**
  （桩数据 status 恒非空 → 「库内」列已折叠、副标题带「均在库内」；
   shareurl 每 4 条留空 → 「链接」列按预期保留）。
- **反向验证**（证明断言不是摆设）：把产物里 `setOvRecentCol("lib", f)` 改成
  恒 `true` 后重跑，9 组视口全部报出
  「「库内」列是常量列却未被折叠」+「表头可见列数 5 与表体单元格数 4 不一致」；
  还原后恢复 0 问题。

## [当前] 媒体库卡片显式显示「修改时间」（issue #27 跟进）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 用户诉求：
> 「添加显示修改时间」。

**先说清楚：修改时间早就有，但用户会觉得「没显示」**

列表视图第 4 列一直是修改时间；上一版卡片把「大小 · 修改时间」挤在**同一行**
（`8.0 GB · 2026-09-17 02:15`）。两段都是数字，扫视时区分不出哪一段是时间，
用户看到的就是「一堆数字」，自然认为没有修改时间。所以本轮不是「补数据」，
而是把时间在卡片上做成**一眼可辨**的一行。

**改成了什么**

- 卡片视图的文件卡元信息拆成**两行**：
  - 第一行：文件大小；
  - 第二行：修改时间（时钟图标 + 「修改」前缀），按卡片**实际可用宽度**取两种格式：
    容器够宽（移动端单列 / 宽卡）显示 `YYYY-MM-DD HH:MM`，
    紧凑卡显示 `MM-DD HH:MM`，完整时间始终放在 `title` 里。
- 时间行用 `--text3`（比大小行更淡）做层级，`tabular-nums` 等宽数字让上下卡的时间对齐；
- 文件夹卡仍然只显示「文件夹」——目录没有「修改时间」语义，显示时间会让人误以为是文件；
- 列表视图的时间列加 `lib-sub-time`（等宽数字 + 更淡），两种视图同一套格式（`fmtTs`）；
- 拆分没有改变卡片高度：两行都是单行省略，走查对「同一行卡片高差 ≤8px」的断言继续成立。

**改了哪些文件**

- `static/src/scripts/library.js`：
  - 新增 `_libMetaHtml(it)`：产出「大小 / 修改时间」两行（文件卡）或「文件夹」（目录卡）；
  - `_libCardShell` 改用 `_libMetaHtml`；`_libMetaText` 保留为单行拼接口径（复用）；
  - `_libTableHtml` 的时间列加 `lib-sub-time` 与 `title`。
- `static/src/styles/main.css`：新增 `.lib-card-time` / `.lib-card-time svg` /
  `.lib-time-text`（单行省略 + 等宽数字）/ `.lib-sub-time`。
- `static/src/body.html`：媒体库说明文案补上「每张卡都标出文件大小与修改时间」。
- `scripts/check_library_dom.py`：新增 7 组渲染后度量与断言 ——
  时间行数 == 文件卡数、时间必须匹配 `YYYY-MM-DD HH:MM`、时间行不折行、
  每行时间都有时钟图标、时间色必须**不同于文件卡标题色**（层级）、
  列表视图 ≥3 行带时间、TMDB 不可用时时间照常渲染。
- `tests/test_build.py`：新增 `test_library_cards_show_modified_time`
  （卡片两行结构 / 图标 / 前缀 / 走 `fmtTs` / 目录卡不显示时间 / 列表视图列 / 产物已重建）
  与 `test_library_modified_time_audit_is_committed`（防止走查断言被删）。

**两个我自己踩的坑**

1. **层级断言是假的。** 第一版拿「时间行 vs 第 0 张卡标题」比色，但第 0 张是
   **文件夹**卡，标题是链接、颜色本就是 accent —— 比较永远不相等，等于没写。
   实测把时间颜色改成正文色，脚本照样报 0 问题。已改成只取**文件卡标题**比较，
   重跑后该回退被正确抓出（`[1440x900] 修改时间与标题同色`）。

2. **只显示完整时间 = 用户仍然看不到时间。** 第一版按 `YYYY-MM-DD HH:MM` 渲染，
   实测算一下才发现：卡片只有 180~240px 宽，`修改 2026-09-17 20:04` 需要约 112px，
   而被勾选框(24) + 图标(22) + 删除按钮(24) + 间距吃掉之后只剩 75~95px ——
   1440 与 2560 视口下时间都被省略号截成 `修改 2025-0…`。
   这也是「早就渲染了时间、用户却以为没有」的同一个坑换了个位置。

   修法是三件事一起做：网格列宽下限 180 → **190px**；窄卡降级为 `MM-DD HH:MM`
   并按容器查询（`@container libname`）决定用哪种格式；时间行在窄卡下再收一档
   字号/图标。修完实测 320 / 390 / 900 / 1280 / 1440 / 1920 / 2560 七个宽度
   **全部不截断**，窄卡尾部由 `+Npx` 余量而非省略号收尾。
   走查新增 `timeTruncated` 断言专门守这条 —— 把降级规则去掉会立刻报 8 条截断。

**验证证据**

- `python3 -m pytest tests/ -q` → **305 passed**；`ruff check .` 通过。
- `python3 scripts/check_library_dom.py --serve` → **0 处问题**；
  实测「文件卡 8 张 / 时间行 8 行 / 格式全部可读」。
- **反向验证**（证明断言不是摆设）：
  1. 把卡片元信息改回单行拼接 → 脚本报 5 处「有文件卡没显示修改时间」；
  2. 把时间行颜色改成正文色 → 脚本报「修改时间与标题同色，层级没有拉开」；
  3. 把列表视图时间列去掉 → 脚本报「行内没有可读的修改时间」；
  4. 去掉窄卡降级规则 → 脚本报 8 处「修改时间被省略号截断」。
- 修改时间在 320 / 390 / 900 / 1280 / 1440 / 1920 / 2560 七个视口下实测无截断。
- 概览页 / 设置页 / 日志栏 / 对比度四套走查全 0 问题；前端产物已重建。

## 媒体库卡片去掉封面图：从「有图/没图混排」改为等高无图卡（issue #27）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 用户诉求：
> 「有一些有图，有一些没图，太丑了，干脆直接不要图了」，附 2560px 宽屏截图。

**先说结论：这次不是调排版，是把图片整块去掉**

上一版把所有卡片的缩略块统一成固定 16:9，高度是齐了，但**「有没有图」本身**
就是观感噪音：网盘文件名/目录名与 TMDB 的命中率天然不可能 100%，
于是同一行里永远同时存在「贴着海报的卡」和「一大块灰色占位块」——
目录越大越花，跟卡片怎么排无关。用户给出的解也很直接：不要图了。

**改成了什么**

- 卡片 = `勾选框 + 类型图标 + 名称/元信息 + 删除图标` 的单行紧凑卡：
  文件夹用文件夹图标、文件用影片图标，**没有任何图片位、没有占位块、没有评分角标**；
- 卡片是单行 flex，所有卡天然等高，不存在「这张有图那张没图」的差异；
- 元信息回到「文件夹」/「大小 · 修改时间」——之前为了让位给海报，修改时间被塞进
  `title` 悬停查看，现在直接显示；
- 视图切换文案由「图文视图 / 列表视图」改为「卡片视图 / 列表视图」，
  页面说明文案同步改掉「自动去 TMDB 匹配海报」的承诺。

**边界说明**

- 前端不再调用 `/api/tmdb/match`（卡片渲染不再依赖任何外部服务）；
- 后端的 `match_tmdb_by_name()` 与 `/api/tmdb/match` 路由**保留未删**，
  它是独立可测的能力，以后想恢复封面图可以直接接回来；
  `tests/test_build.py::test_library_backend_match_api_kept` 防止后人误删。

**改动**

- `static/src/scripts/library.js`：
  - 删除整条海报链路：`_libPatchPosters` / `_libMatchChunked` / `_libMatchWorker` /
    `_libFileNames` / `_libApplyPoster` / `libPosterBroken` / `_libPlaceholderHtml` /
    `_libDirPhHtml` / `libMatch` 及 `LIB_MATCH_*` 常量（净减约 200 行）；
  - `_libCardShell(it, kind, icon)` 统一产出单行卡片；`_libMetaText()` 产出元信息；
  - `_updateLibViewBtn()` 文案改为「卡片视图」。
- `static/src/styles/main.css`：删除 `.lib-card-thumb` / `.lib-card-thumb-inner` /
  `.lib-card-link` / `.lib-card-ph*` / `.lib-card-foot` / `.lib-dir-badge` /
  `.lib-card-thumb .tmdb-rating-badge`；`.lib-grid` 改为 `auto-fill minmax(180px,1fr)`；
  `.lib-card` 改为单行 `display:flex;align-items:center`；新增 `.lib-card-icon` /
  `.lib-card-dirlink`。
- `static/src/body.html`：说明文案改为「文件夹与文件以等高卡片展示（不显示封面图）」。
- `scripts/check_library_dom.py`：移除 TMDB 海报桩与相关度量；
  **新增核心断言「媒体库列表里 `img` 数量必须为 0」**、类型图标数 == 卡片数、
  元信息非空、卡片视图不得请求 `/api/tmdb/match`；
  保留同一行卡片高差 ≤8px、标题/元信息零折行、全选与面包屑同行等整齐度断言。
- `tests/test_build.py`：`test_library_poster_cards_wired` /
  `test_library_folder_cards_get_tmdb_posters` / `test_library_poster_cards_keep_fast_budget`
  合并改写为 `test_library_cards_have_no_images`（从「源码无海报逻辑 / 样式无缩略块 /
  文案无图文 / 产物已重建」四个方向钉死），新增
  `test_library_backend_match_api_kept`。

**验证证据**

- `python3 -m pytest tests/ -q` → **303 passed**；`ruff check .` 通过。
- `python3 scripts/check_library_dom.py --serve` → **0 处问题**；
  实测：`#libList img` **0 张**、卡片 12 张 / 类型图标 12 个、同一行卡片高差 **0px**、
  标题折行 **0 个**、1440 与 390 视口均无横向溢出。
- 走查脚本在改造过程中真的抓到两处问题（都已修）：
  1. 网格 `minmax(230px)` 时 1440 宽只有 3 列 → 报「桌面网格只有 3 列」（收窄到 180px）；
  2. 脚本自身把目录/任务请求误计成 TMDB 请求 → 报「仍在依赖图片服务」，
     已改为独立计数器（这是脚本的 bug，不是产品的）。
- 其余走查（概览 / 设置 / 日志栏 / 勾选框 / 对比度）全部 0 处问题；
  前端产物已重建（`bash static/src/build.sh`）。

## [当前] 媒体库图文视图排版重做：从「大海报框」改为紧凑对齐的网格（issue #27）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 用户诉求：
> 「海报依旧存在 @CodeBuddy 而且现在这个媒体库的排版太丑了，实在是很乱」，
> 附 2560px 宽屏截图。

**先说「丑」和「乱」的可量化成因**

不是配色问题，是**排版的几何结构**问题。旧版每张卡的结构是
「大海报框 + 卡片内勾选框 + 标题 + 元信息 + 删除按钮」，于是：

1. **卡片高度由海报比例决定** —— 有海报 2:3、无海报 16:9、文件夹又是另一种，
   同一行卡片高矮参差（实测最大差 49px，改了比例后到 65px），一眼就是「乱」；
2. **无海报时是一大块纯色空框** —— 1440px 主列 4 列时单卡宽 ~204px，
   2:3 海报框高 ~306px，其中只有一行文件名，其余全是空白；
3. **标题允许多行** —— 长中文文件名折三行，继续把卡片撑高，加剧参差；
4. **「全选」独占一行** —— 卡片底部「操作条 + 全选行」两条通栏，
   勾选为空时就是两片空白；
5. **删除按钮是文字按钮**，和标题抢宽度，标题被压成省略号。

**改成了什么**

紧凑网格 + 底部信息行：`缩略块（固定 16:9，顶裁）` / `勾选框 + 名称 + 元信息 + 图标删除按钮`。
缩略块比例由 CSS 钉死，**不再由图片自身比例决定卡片高度**；标题与元信息单行省略；
「全选」并入面包屑所在的目录控制条。条目多时网格给高度上限（卡片内滚动），
底部批量操作条不再被长列表顶出屏幕。

**改动**

- `static/src/scripts/library.js`：
  - 卡片结构改为 `_libCardShell()` 统一产出（`lib-card-thumb` / `lib-card-foot` /
    `lib-card-name-col` / `lib-icon-btn`），文件夹与文件只差缩略块内容；
  - 勾选框与删除按钮**移出 `<a>` 链接**：旧结构里文件夹卡的按钮在链接外、
    勾选框在链接外，文件卡则是四种元素各占一块；现在统一到信息行，且
    点「勾选 / 删除」不会被进入目录的链接吞掉事件；
  - 元信息收敛为「作品名 · 年份 · 大小」一行；完整文件名 + 修改时间 + TMDB 作品名
    移到卡片 `title`（悬停可查，不占版面）；
  - 海报加载失败的兜底改成按新的 DOM 判定卡片类型（`.lib-card-dir`），
    并清掉残留角标，避免「占位块 + 评分角标」错配。
- `static/src/body.html`：面包屑与「全选」合并为 `.lib-crumbbar` 一条控制条，
  删除原 `#libSelectAllBar` 独立占位行；说明文案改到网格下方一行（`.lib-tip`）。
- `static/src/styles/main.css`：重写媒体库图文视图段（`.lib-grid` 改为
  `auto-fill minmax(190px,1fr)`；新增 `.lib-card-thumb` / `.lib-card-foot` /
  `.lib-icon-btn` / `.lib-crumbbar` / `.lib-selectall` / `.lib-tip`；删除旧的大海报框规则）。
- `scripts/check_library_dom.py`：桩数据扩到 12 条（长中文名、超长发布组名、
  短名、匹配不到的文件/文件夹），海报桩改为**竖版 200x300 与横版 500x200 两张真图**
  加一个必然加载失败的地址；新增整齐度度量与断言：缩略块等高 ≤8px、
  同一行卡片高差 ≤8px、信息行等高 ≤8px、标题/元信息零折行、
  全选与面包屑同行、桌面列数 ≥4 且卡片宽度落在 160~320px。
- `tests/test_build.py`：新增 `test_library_grid_layout_is_aligned`
  与 `test_library_grid_layout_audit_is_committed`，锁死上述结构与走查覆盖。
- `scripts/check_checkbox_dom.py`：勾选框采样点区分「必需」与「允许隐藏」。
  「全选」本次从独占一行挪进目录控制条，无条目时随容器一起隐藏，
  旧脚本会把隐藏态量成 `0x0` 并误报「尺寸不一致 / 点不准」；
  现在必需采样点仍要求可见，允许隐藏的采样点不参与形状比对。

**验证证据**

- `python3 -m pytest tests/ -q` → **305 passed**。
- `python3 scripts/check_library_dom.py --serve` → **0 处问题**；
  实测度量：缩略块高度差 **0px**、同一行卡片高度差 **0px**、信息行高度差 **0px**、
  标题折行 **0 个**、桌面 4 列 / 单卡 **204px**，1440 与 390 两个视口均无横向溢出。
- **反例守卫实测有效**：
  - 去掉缩略块 `aspect-ratio`（改回由图片比例撑高）→ 脚本报
    「缩略块高度不一致」；
  - 再把标题改成可折行 + 比例改 4:3 → 脚本报「同一行卡片高度不一致（最大差 49px）」
    与「卡片信息行高度不一致（最大差 49~65px）」。
- 主题与视口走查截图（深色 1440 / 浅色 1440 / 移动端 390）确认卡片等高对齐、
  无空框、元信息不被截断成半句。
- 其余走查脚本（概览 / 设置 / 日志栏 / 勾选框 / 对比度）全部 0 处问题；
  `ruff check .` 通过；前端产物已重建。

## [当前] 登录页：与主页统一视觉体系，并修掉浅色主题「白字白底」（issue #27）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 用户诉求：
> 「优化当前登录页显示效果。与主页的视觉效果一致」，附截图。

**先说最要命的那个 bug：浅色主题下登录页几乎看不见字**

登录页内联 CSS 里写死了一份深色令牌（`--text:#ffffff`、`--text2:rgba(255,255,255,.72)`、
`--border:rgba(255,255,255,.08)`、`--card-2:#1c1c22`…）。它的选择器也是 `:root`，
**同优先级、后出现**，于是一把盖掉了 tokens.css 里浅色主题的 `--text:#1c1c1e`：

- 主标题「登录」：白字压在半透明白卡片上 → 实测 **1.07:1**；
- 输入框占位符：白字压在近白输入框上 → 实测 **1.11:1**；
- 页脚 / 提示文案：白字压在浅色底上 → 实测 **1.14:1**。

`scripts/check_contrast.py` 只审计 tokens.css，看不见这层覆盖，所以这问题一直没被门禁拦住。

**第二件：登录页和主页确实不是一套东西**

- 页面底色是写死的 `#0a0a0d` + 两张自绘渐变图，而主页是 tokens 的 `--bg-deep` 加
  一套 40px 网格 + 三颗 `--glow-*` 光球；
- 登录卡片 `background:rgba(22,22,26,.5)` 直接盖在主色上，磨砂被 `[data-theme=light]`
  分支换成不透明的 `rgba(255,255,255,.5)` 后就彻底失效，卡片底部一大片死白；
- 主标题吃的是自拼的 `linear-gradient(白, 青, 紫)`，和主页的品牌渐变（`--accent → --accent2`）不是一套；
- 字号大面积手写 22 / 25 / 34px，而主页正文是 `--fs-base`（13px）。

**顺带把截图里那条「浅色渐变横带」定位掉了**

品牌列的原底图里有两个 `background-attachment:fixed` 的 40px 网格层。fixed 是按
**视口**铺开的，1336px 宽屏下网格正好落在登录按钮上方，看上去就是「按钮底下压了一条
浅色渐变带」。现在网格与光球统一由 `body` 承担（与主页同一份），品牌列退化为
`--card` 玻璃面，四个角实测底色差 **0**。

**改动**

- `static/login_new.html`：
  - 删掉整套本地令牌覆盖，改为「**设计 token 单一出处：tokens.css**」，
    登录页自己只补 `--login-card-w` / `--login-panel-w` 两个尺寸令牌；
  - body 底色 / 网格 / 光球改用 `--bg-deep` + `--grid-line` + `--glow-*`，
    浅色主题那一组渐变参数与 `main.css` 的 `[data-theme=light] body` **逐字对齐**；
  - 卡片换成 `var(--card)` + `var(--glass-border)` + `var(--blur-xl)` + 一层玻璃高光，
    与主页 `.card` 同形态；加 `@supports not (backdrop-filter)` 实底降级（与 main.css 同策略）；
  - 主按钮去自拼渐变，改用 `var(--gradient-btn)`（= 主页 `.btn btn-primary`）；
  - 输入框对齐主页 `input[type=text]`：`--field-bg` 实底 + `--focus-ring` 焦点环 + 11px 内边距；
  - 字号全面改用 `--fs-*`；`已锁定 N 分 0N 秒` 那段倒计时也统一到 `--fs-sm`；
  - 主标题只让第二行吃 `--gradient-brand`，第一行保留 `--text`
    （曾经想「整段渐变里塞 `var(--text)`」，但浅色主题的 `--text` 近黑，
     压在深底上对比度只有 ~1.2:1，整行会糊 —— 实测踩到过，已回退）；
  - `.liquid-bg` 从 `.form-panel` 内挪到 `.login-page` 之外：光球上的 blur 会把
    fixed 背景的包含块改到它自己身上，留在面板里等于每个视口都铺满、观感不一致；
  - 修 `100vh` 移动端地址栏跳动：`min-height:100dvh` + 面板改 `justify-content:flex-start`；
  - 错误提示去掉 4 下抖动（违反 tokens 里「no bounce, no entrance」的动效口径），
    只保留一次淡入。
- `scripts/check_login_dom.py`（**新增**）：登录页的 DOM 度量走查。静态扫描只能看出
  「有没有用 token」，看不出「渲染出来到底看不看得见」，所以把只有渲染后才能量化的
  几条固化下来：
  - 两套主题 × 5 组视口，逐元素算**真实合成对比度**（把半透明前景/背景逐级合成后
    再算，而不是读 `color` 了事），要求全部 ≥ 4.5:1；
  - 按钮白字压在渐变上的对比度单独算（渐变底不能用纯色算法，否则会算出 1.1:1 的
    假失败 —— 已单列 `btnRatio`，按大号粗体 3:1）；
  - 登录卡片背后不得出现横向色带（四角 + 左右中点的实际底色最大差 ≤ 24）；
  - 磨砂真的生效、卡片有玻璃高光、输入框底色不透明（半透明叠磨砂会「透底」）；
  - 焦点环必须在**关掉过渡后**仍有 ≥1px（过渡期间读到的是中间值，曾误报 0.16px）；
  - 密码可见性按钮 / 主按钮命中尺寸 ≥ 30px；双主题无横向溢出。
- `tests/test_build.py`：新增 3 条回归门禁
  - `test_login_page_does_not_redefine_shared_tokens`：登录页不得重定义 tokens.css 的
    任何令牌（就是这条直接锁死「白字白底」的根因）；
  - `test_login_page_uses_shared_shell_visuals`：必须用同一组背景参数 / 玻璃面 / 渐变 /
    字号令牌，且 `#0a0a0d`、`rgba(22,22,26,.5)` 这类写死的深色值不得回归；
  - `test_login_page_dom_audit_is_committed`：度量脚本必须落库且覆盖两套主题与对比度。

**验证证据**

- `python3 -m pytest tests/ -q` → **297 passed**；`ruff check .` 通过。
- `python3 scripts/check_login_dom.py --serve` → **0 处问题**（10 组主题×视口）：
  - 最低对比度：深色 **6.22:1**、浅色 **5:1**；卡片底色差 **0**；磨砂「有」；
  - 反例守卫（实测有效）：把原来的 `:root{--text:#ffffff}` 加回去 →
    脚本立刻报 `.login-card h1 对比度 1.10:1 < 4.5:1`、`#user 1.00:1`；
    把原版 `HEAD:static/login_new.html` 整份还原 → 报 15 处（含「缺少玻璃高光」
    「输入框底色半透明 alpha=0.02」）。
- `python3 -m pytest tests/test_build.py::test_login_page_does_not_redefine_shared_tokens` →
  注入 `--text:#ffffff` 后**必定失败**，还原后通过（防复发已实测）。
- 真实服务端到端：`python3 main.py` → `GET /login.html` **200**（27135 bytes）、
  `GET /static/src/styles/tokens.css` **200**；`/version` 正常。
- 回归走查全绿：概览页 / 设置页 / 媒体库页 / 日志栏 / 发现区多选条 / TMDB 筛选栏
  六套脚本各 0 处问题；`scripts/check_contrast.py` 18 组受检 0 组未达标。
- 视觉复核（1440 / 1336 / 390 三档 × 两主题）：浅色主题由「白字白底」变为可读，
  按钮无横带，移动端单栏正常。

## [当前] 复选 / 单选控件统一自绘（issue #27 反馈：复选框有点丑）

> 用户反馈：「麻烦优化一下复选框。当前复选框有点丑」（同时撤回了文件夹海报的诉求）。

排查下来的根因不是「配色不好看」，而是**同一页存在三种长相的勾选框**：

- 媒体库卡片角标：15px 的系统蓝方块，压在自己画的深色圆底上；
- 媒体库表格行：16px 原生框 + `accent-color`，跟随浏览器/系统配色，Windows 与 macOS 完全不同；
- 全选、其它页面：裸 `input`，尺寸还被行内 `font-size` 带着跑（13/15/16px 混用）。

原生 `input` 的渲染由浏览器决定，只调 `width/height/accent-color` 永远无法一致，
所以这轮改成**全局自绘一份**，各处只保留热区。

**改动**

- `static/src/styles/main.css`
  - 新增全局控件段：`input[type="checkbox"], input[type="radio"]` 统一 `appearance:none`
    + 18px 固定尺寸 + 6px 圆角方块（单选取 50% 圆）+ `--field-bg` 实底 + `--border3` 描边；
  - 勾选态用 `--gradient-btn`（与主页主按钮同一渐变出处），勾形由 `::after` 描边绘制
    （`rotate(43deg)`，内缩 3/4px 避免勾尾被卡片边缘裁掉）；
  - 补上**半选态** `:indeterminate` 的横杠 —— 全选框之前根本没有可见的半选反馈；
  - 深浅主题下描边对比度 2.44:1 / 1.54:1，两类主题都能从背景里显形（历史浅色主题下
    白底白框 = 看不出有控件）；
  - 键盘焦点环、禁用态、`:active` 缩放；`@media (hover:none)` 下热区补到 26px（视觉尺寸不变）；
  - `.lib-cb` / `.lib-card-cb` 只保留「24~30px 热区 + 光标」，**不再自定控件尺寸**
    （这正是「三种长相」的复发点，已被静态断言锁住）。
    卡片角标去掉自带深色圆底：勾选框在深色卡片上本就是亮色块，圆底纯属多余噪声。
- `scripts/check_checkbox_dom.py`（新增）：真实浏览器里断言
  - 页面内**没有残留原生外观**的勾选框（`appearance` 必须为 `none`）；
  - 卡片角标 / 表格行 / 全选三处采样的**尺寸与圆角完全一致**，且 ≥16px；
  - 控件「可见性对比度」（块面与描边取大者）≥1.4:1，两套主题、1440 与 390 两组视口；
  - 已勾选态真的有勾形（`::after` 有宽度），白色勾与勾选块对比度 ≥3:1；
  - 全选框半选态真的有横杠；
  - 把父容器字号从 13px 改到 28px，控件宽度**不变**（原生控件会被缩放）。
- `tests/test_build.py`：新增 `test_shared_checkbox_control_is_defined_once`
  （全局规则存在 / `appearance:none` / 勾形与半选 / 焦点环 / 固定 18px /
  各处不得再自定尺寸）与 `test_checkbox_dom_audit_is_committed`。

**验证证据**

- `python3 -m pytest tests/ -q` → **300 passed**；`ruff check .` 通过；
- `python3 scripts/check_checkbox_dom.py --serve` → **0 处问题**
  （2 主题 × 2 视口，每组 12 个勾选框全部自绘、三处形态一致）；
- **反例守卫实测有效**（各跑两次确认）：
  - 删掉全局控件规则 → 走查脚本报「仍有 12 个勾选框是浏览器原生外观 / 尺寸 13x13 /
    已勾选态没有勾形 / 半选态没有横杠」；静态断言同步报「缺少全局复选/单选控件规则」；
  - 把卡片角标改回 15px → 走查脚本报「同一页勾选框尺寸/圆角不一致」；
    静态断言报「又自定了勾选框尺寸」；
- 媒体库 / 概览 / 设置 / 日志栏 / 发现区 / TMDB 筛选栏 六套 DOM 走查 0 处问题；
  `check_contrast.py` 18 组全达标；
- 前端产物已重建，连续三次构建 md5 一致（无漂移）。

> 说明：本轮同时撤下了文件夹海报卡（用户「算了，还是不要海报了」的撤回指令），
> 相关 TMDB 匹配能力保留在库里不动 —— 撤回的是**呈现方式**，不是接口。
> 该项在 PR `#34`（登录页视觉一致性）合并后单独提交；`#34` 仍在冲突待人工处理，
> 两者文件几乎不重叠，谁先合都能干净落地。

## [当前] 媒体库：文件夹卡也走 TMDB 匹配（issue #27 跟进）

> 用户反馈（附图是媒体库根目录）：「这个页面不能通过 TMDB 匹配图片文字吗」。

上一版图文卡片**只给「文件」匹配海报**，而媒体库根目录（如 `/影视`）下清一色是
**文件夹** —— 于是整页都是灰色文件夹图标，看起来「完全没走 TMDB」。
网盘目录名（`复仇者联盟4`、`繁花`、`三体`）本身就是作品名，必须一起参与匹配。

**改动**

- `static/src/scripts/library.js`
  - `_libFileNames()` 取消 `it.dir` 过滤：文件夹与文件一起进 `/api/tmdb/match`；
  - `_libDirCardHtml()` 内新增海报挂载点 `libPoster-<key>`（默认文件夹图标块）。
    **「进入目录」的链接本身不被替换**，海报只是链接内的可视块 ——
    无论是否匹配到，点哪都能进目录；
  - `_libApplyPoster()` 放开文件夹，命中海报时叠一枚 `lib-dir-badge`「文件夹」角标
    （海报只回答「这是哪部作品」，不能让人误判点击行为）；
    且**只在真拿到海报时才打补丁**，没海报就保持原占位形态，不做无意义的重绘；
  - `libPosterBroken()` 按卡片类型回退图标：文件夹 → `icon-folder`，文件 → `icon-movie`。
- `static/src/styles/main.css`
  - 文件夹卡改为「海报块 + 居中标题/元信息」结构，海报块与文件卡同为 2:3
    （未匹配到仍是 16:9 图标块）；新增 `.lib-dir-badge`；
    文件夹海报的评分角标移到底部右侧，避开左上角的勾选框与「文件夹」角标。
- `app_modules/tmdb.py`
  - `_similarity()`：包含关系不再一律 0.9，改为按**覆盖比例**取值（0.7~0.9），
    并对「候选被查询完整包含」的情形再降一档。
    背景：目录名常是作品本名，`复仇者联盟4` 与 `复仇者联盟4：终局之战`、
    `复仇者联盟` 都是包含关系；固定同分只能靠返回顺序决胜，很容易把续集挂成前作。
    现在「补了副标题」的候选（0.82）稳定压过「被截短」的候选（0.79）。
- `scripts/check_library_dom.py`：桩数据新增「可匹配的文件夹」与「匹配不上的文件夹」，
  断言文件夹海报数 ≥1、每个文件夹海报都有角标、匹配不上的仍保留文件夹占位、
  每张目录卡都还有「进入目录」点击区。
- `tests/test_build.py`：新增 `test_library_folder_cards_get_tmdb_posters`
  （匹配名单不得再排除 dir / 文件夹卡有海报挂载点且保留 enterDir / 补丁函数不得拒绝文件夹 /
  兜底图标按类型区分）与 `test_tmdb_similarity_grades_containment`。

**验证证据**

- `python3 -m pytest tests/ -q` → **298 passed**；`python -m ruff check .` 通过；
- `python3 scripts/check_library_dom.py --serve` → **0 处问题**；
  把 `_libFileNames()` 临时改回排除文件夹，脚本如期报「没有任何文件夹卡吃到 TMDB 海报」，
  确认这道门禁拦得住回归；
- 概览页 / 日志栏 / 设置页三套 DOM 走查 0 问题；`check_contrast.py` 18 组全达标；
- 前端产物已重建，连续三次构建 md5 一致（无漂移）。

## [历史] 媒体库：网盘文件改为 TMDB 风格图文卡片（issue #27）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 用户诉求：
> 「我想把当前媒体库文件夹样式的选中，改成图文并茂可以吗？像 TMDB 那样，
> 使用 TMDB 的数据就可以」。

网盘 `get_savepath_detail` 只返回 `fid / file_name / dir / size / updated_at`，
没有海报、没有简介。要做图文卡片，就必须先从**文件名**判断「这个文件是哪部作品」。

**两条不能破的底线**

1. **不能让「等海报」拖慢目录浏览。** 上一轮刚为「网盘文件访问有点慢」收窄过等待预算
   （见下方条目），如果这次把目录渲染和 TMDB 匹配串在一起，等于把坑挖回来。
2. **不能让 TMDB 决定媒体库可用性。** 未配置 key / 图片域名不可达时，
   浏览与删除必须照常可用。

**改动**

- `app_modules/tmdb.py`：新增「文件名 → TMDB 作品」匹配链路
  - `_strip_extension` / `_split_fields` / `_episode_cut` / `_split_filename_noise`
    / `filename_hints`：文件名解析（纯函数，可直接单测）；
  - 解析顺序刻意是「先定位季集标记、再找噪声 token」：`S02E05` 这类标记比年份更可靠，
    而 `Some.Show.US.S02E05` 里的 `US` 属于标题，必须靠「向后扩展 ≤2 位短 token」保留下来；
  - `_score_candidate`：以**核心标题相关性**做闸门（核心名对不上直接 0 分），
    年份相同 +0.15、冲突 −0.35。同名不同年最容易挂错海报，这条是防它的；
  - `match_tmdb_by_name`：`/search/multi`（带年份 / 不带年份 / 第 2 页）→
    未命中再退到 `/search/movie`、`/search/tv`；结果进 `_tmdb_cache`（TTL 30 分钟），
    重复浏览同一目录不再打网络；
  - **网络失败不写缓存**，否则 TMDB 恢复后仍一直显示占位卡。
- `app_modules/routes_tmdb.py`：`GET /api/tmdb/match?names=a&names=b`
  （单次上限 `MAX_MATCH_NAMES = 60`，超限如实回报 `truncated`）。
- `static/src/scripts/library.js`：**两阶段渲染**
  - 阶段一：目录数据一到就出卡片骨架（文件夹 = 图标卡、文件 = 占位卡），
    立刻可点、可勾、可删，不依赖任何网络；
  - 阶段二：后台按 ≤12 个/批、最多 2 批并发调 `/api/tmdb/match`，
    拿到结果后**只就地补丁该卡片的海报与评分**，不重渲染整个列表
    （否则会把用户的勾选状态与滚动位置一起丢掉）；
  - 会话级 `libMatch` 缓存：翻回父目录 / 重新进入，海报秒出且零请求；
  - 新增「图文视图 / 列表视图」切换（`localStorage: lib_view`，默认图文），
    原表格视图完整保留；
  - 匹配请求同样走 `libFetch` 短预算通道，失败静默降级为占位卡。
- `static/src/body.html` / `static/src/styles/main.css`：卡片栅格与查看切换按钮，
  海报用 2:3、评分角标复用发现区的 `.tmdb-rating-badge` 与 `.tmdb-no-poster` 观感。
- `scripts/check_library_dom.py`：走查扩到卡片视图
  （卡片数 / 勾选框 / 删除入口 / 海报数 / 评分角标 / 列表视图表格度量 /
  **TMDB 接口 500 时仍出占位卡且删除入口不丢**）。
- `tests/test_tmdb_match.py`（新增，26 条）：文件名解析、打分、候选提取、
  匹配缓存 / 阈值过滤 / 网络失败不写缓存；全部不联网。
- `tests/test_build.py`：新增两条接线与预算门禁（卡片接线、匹配必须走短预算通道）。

**放弃的方案**

- 「前端直连 TMDB 拉海报」：API key 会暴露在浏览器，且每个用户各自打 TMDB，
  缓存完全无法共享 —— 改为后端批量匹配 + 服务端缓存。
- 「匹配成功再渲染」：首屏会被 TMDB 的响应时间绑架（超时 8s × 多地址），
  退化成第二个「网盘文件访问很慢」。
- 「按文件名直接拼 TMDB 图片 URL」：TMDB 的 `poster_path` 只能由搜索接口拿到，
  文件名拼不出海报地址。

**验证证据**

- `python3 -m pytest tests/ -q` → `281 passed`；`ruff check .` 通过。
- `python3 scripts/check_library_dom.py --serve` → 0 处问题：
  - 1440x900 / 390x844：卡片 4 张（含 1 张文件夹图标卡）、勾选框与删除入口齐全、
    2 张海报卡带评分角标、无横向溢出；
  - 「列表视图」切换后仍是原表格栅格（桌面 ≥4 列、行高 ≥32px）；
  - **TMDB 接口返回 500** 时：卡片与删除入口照旧，全部退化为占位卡、无破图；
  - 慢接口 12s 内快速失败可重试、父目录缓存复用（请求数 +1 / +0）仍成立。
- 概览页 / 日志栏 / 设置页 / 对比度四套走查 0 问题；前端产物已重建（两次构建哈希一致）。

## [当前] 媒体库：可删除夸克自动转存的任务条目（issue #27 跟进）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 用户确认：
> 「夸克自动转存的条目删除任务配置可以做，夸克自动转存是自部署的，
> 所以不存在改别人的任务配置」。

此前刻意没做这一步，理由是「直接改别人的任务配置风险高」。用户确认 QAS 为
**自部署服务**后，改 `tasklist` 只影响自己的任务表，风险点因此从「能不能改」
转移到「怎么改不会把别的任务一起抹掉」。本次按后者的标准实现。

**为什么必须整表写回**：QAS 的 `/update` 是**整表替换**语义
（其 WebUI 的 `removeTask` 也是 splice 后提交整个 tasklist）。
只把「被删的那一条」发过去，等于删掉其余全部任务 —— 这是不可逆事故。

**改动**

- `app_modules/api_client.py`：`QASClient.update_tasklist(tasklist)` —— 走 `/update`
  写回整份列表，**不改动列表内任何字段**。任务上的 `addition`
  （任务级插件配置，如 `auto_unarchive` 的解压开关）、`shareurl_ban`（失效标记）
  若被重写或丢弃，解压/订阅行为会静默改变。
- `app_modules/media_library.py`：`delete_tasks(items)`
  - 定位键为 `taskname + savepath`（QAS 不保证 taskname 唯一），也支持传 `index`；
  - **不做模糊匹配**：删任务不可逆，宁可不删也不误删；
  - 写回前做条目数一致性校验（`写回条目数 == 原条目数 - 删除数`），不成立即中止；
  - **任一目标定位失败 → 整批不写回**。一次写回影响整张 tasklist，
    「删掉一半、漏掉一半」比直接报错更危险；
  - 成功后同步 `transfer._qas_cache`，避免「删了任务、本地仍判为已转存而跳过」；
  - 单次上限 `MAX_TASK_DELETE_BATCH = 20`（任务配置不可再生，比文件删除更保守）。
- `app_modules/routes_library.py`：新增 `POST /api/qas/delete_tasks`
  （`{tasks:[{taskname,savepath} | {index}]}`，参数校验 + 502 语义与既有接口一致）。
- 前端 `static/src/scripts/library.js` + `body.html` + `main.css`：
  QAS 任务表支持单条删除、勾选批量删除、全选；行内参数沿用 `jsArg()` 单引号字面量，
  且只传 `qasKey` 定位、不把任务名拼进 HTML 属性；确认弹窗写明
  「只删除任务配置，网盘里已转存的文件不受影响」；删除成功后直接用后端返回的
  写回结果重渲染，省一次往返。`scripts/check_library_dom.py` 同步补了校准逻辑。

**验证证据**

- `tests/test_media_library.py`：新增 11 条 —— 只删目标且保留其余任务与字段、
  同名任务按 savepath 区分、index 定位、定位失败整批中止（断言未向 QAS 写回）、
  写回失败不谎报成功、QAS 读取异常兜底、空/超量/非法参数、缓存同步、路由 200/400/502。
- `tests/test_build.py`：新增 2 条 —— 删除链路接线 + 「必须整表写回且有一致性校验」
  的源码断言、内联参数只传 key 不拼字段。
- `scripts/check_library_dom.py`（扩展）：真实浏览器断言任务表 8 行、行内有删除入口、
  单条删除弹确认且文案含「网盘文件不受影响」→ 行数 8→7；全选计数与批量操作条可见
  → 批量删除后 0 行并给空态。
- 对接桩 QAS 端到端实测：3 条任务删中间 1 条，QAS 侧最终为 `['电影A','电影C']`
  且写回体中其余任务的 `pattern` / `addition` 原样保留；重复删同一条返回
  「未做任何改动」且 QAS 无变化；接口返回失败时前端不移除行、如实透出失败原因。
- 移动端 390/320 与桌面 1440 均无横向溢出、删除入口未被挤出视口。
- 全量 `252 passed`，`ruff check .` 通过；概览/设置/日志栏/对比度四套走查 0 问题；
  前端产物已重建。

## [当前] 媒体库：网盘文件置顶 + 目录浏览提速（issue #27 跟进）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 反馈：
> 「把网盘文件这个模块放到顶部，和夸克自动转存任务交换一下位置」，
> 以及「网盘文件这个模块访问有点慢，有办法解决吗」。

**改动一：模块换位**

- `static/src/body.html`：媒体库页内「网盘文件」卡片移到「QAS 转存任务」之前
  （源文件 DOM 顺序即可见顺序，旧位置已删除，不是复制一份）。
- 底部导航把「媒体库」提到「历史」之前（`tabs.js` 的 `BOTTOM_NAV` +
  `body.html` 底栏按钮同步换位），与页内「网盘文件置顶」的优先级一致。

**改动二：目录浏览提速 —— 先定位「慢在哪」，再对症改**

排查出三个叠加原因，都是「等待时间」而不是「渲染时间」：

1. **单次请求的失败预算过长**（主因）。`/api/qas/library` 走
   `QASClient(timeout=15)`，而 `utils.http_get` 对网络层错误重试 3 次，
   QAS 不可达/超时时最坏等待 ≈ 15+1+5+5+5+1 ≈ 32s，期间页面只有「加载中...」。
2. **前端自带长重试**。`apiGet` 默认 `retries=2`，与后端重试叠加，
   把「快速失败」彻底抵消。
3. **没有进度反馈、没有本地复用**。用户无法区分「慢」与「死了」；
   翻进子目录再返回，父目录也要重新打一次网络。

对应改法：

- 后端 `app_modules/media_library.py`：新增 `LIBRARY_READ_TIMEOUT = 6`，
  读接口（`/data`、`/get_savepath_detail`）走 `_read_client()` 收窄超时；
  **删除保持 15s 预算不变**（破坏性操作需要更完整的响应等待）。
- 前端 `static/src/scripts/library.js`：新增 `libFetch()` 短预算通道 ——
  软超时 8s + 仅 1 次重试，**且超时预算覆盖整次调用**（见下方踩坑），
  失败立刻渲染「读取失败 + 已用时长 + 重试按钮」，不再无限转圈。
- 加载反馈：加载态带**已用时长**（每秒刷新），用户能看出「在等」而非「卡死」；
  成功后计数区显示「耗时 0.1s · 缓存」，把性能变成可见信息。
- 并发与缓存：`loadLibraryPage()` 并行发起目录与任务请求（原先串行）；
  目录响应按 60s TTL 缓存，翻回父目录直接复用并标注「缓存」；
  删除/强制刷新会作废对应路径缓存。
- `app_modules/routes.py`：`do_GET` / `do_POST` 外层加异常兜底 ——
  未捕获异常一律转成 500 并记录堆栈。此前线程化 server 下异常会让请求
  **悬停到连接超时**，前端表现恰好也是「加载很慢」。

**踩坑记录（已加 CI 守卫）**：`libFetch` 最初写成「每次尝试各自 `setTimeout(8s)`」，
实测最坏等待变成 8s + 退避 + 8s ≈ **16.4s**，快速失败形同虚设
（`scripts/check_library_dom.py` 的「接口永不响应」用例把它抓出来了）。
现在 AbortController 的定时器只在进入循环时建立一次，到点即中止，
循环内还会检查 `signal.aborted` 不再发起新请求。

**验证证据**

- `tests/test_media_library.py`：新增 4 条 —— 读接口必须用收窄超时、
  `_read_client()` 真的把超时透传给 `QASClient`、QAS 异常时路由返回 502。
- `tests/test_build.py`：新增 `test_library_files_card_is_first`
  （DOM 顺序 + 加载顺序 + 产物已重建）与 `test_library_fetch_has_short_budget`
  （软超时 ≤10s、重试 ≤1、超时定时器不得落在重试循环内）。
- `tests/test_pure.py`：新增 2 条 —— 路由入口必须有 try/except + 500 兜底，
  并真实触发一次异常确认返回 500。
- `scripts/check_library_dom.py`（新）：1440×900 / 390×844 断言模块顺序与首屏可见、
  无横向溢出；「接口永不响应」用例断言 12s 内快速失败且带重试入口；
  缓存用例断言请求序列 `[2,3,3]`（返回父目录 0 新请求）并标注「缓存」。
- 全量 `239 passed`，`ruff check .` 通过。

## [当前] 媒体库双向管理：网盘删除自动同步转存历史

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27)：
> 「在夸克网盘删除了某些影视电影的时候，夸克自动转存并不会自动删除任务」，
> 希望「在当前项目内显示夸克自动转存内的任务并展示完整的媒体库，
> 可以在媒体库上进行删除管理，双向同步」。

**问题**：DBAuto 此前只有「正向」链路（豆瓣/TMDB → PanSou → QAS 转存）。
用户到夸克网盘手工删掉影片后，①QAS 的 tasklist 仍留着失效任务；
②DBAuto 的转存历史仍标记「已转存」，导致重新转存被判定为重复而跳过。
网盘侧与本地侧完全脱钩。

**方案**：复用 QAS 上游已有的文件级接口，补齐反向链路，不引入新依赖。

- 新增 `app_modules/media_library.py`：
  - `list_tasks()` —— 只读 QAS `/data` 的 tasklist；
  - `list_library()` —— 调 `/get_savepath_detail` 列出网盘真实文件（归一化字段）；
  - `delete_entries()` —— 调 `/delete_file` 删除网盘文件，并按需联动清理本地历史。
- `app_modules/api_client.py`：`QASClient` 增加 `get_savepath_detail()` / `delete_file()`。
- `app_modules/storage.py`：增加 `delete_history_item()`（单条删除 + 失效缓存）。
- 新增 `app_modules/routes_library.py` + 路由注册：
  - `GET  /api/qas/tasks` —— QAS 任务列表；
  - `GET  /api/qas/library?path=/` —— 网盘目录浏览（拦截 `..` 越界）；
  - `POST /api/qas/delete` —— 删除 + 历史同步（`sync_history` 开关）。
- 前端新增「媒体库」页（`static/src/scripts/library.js` + body/CSS）：
  展示「QAS 转存任务」与「网盘文件」两张表，支持面包屑进目录、
  勾选/单条删除，并提供「同步清理转存历史」开关。

**删除语义（防误判）**：只有「全部条目删除成功」才返回 `success=true`；
部分成功返回 `success=false` 并逐条给出结果，避免调用方以为全量成功。
历史同步用「显式 title 优先、其次文件名去扩展名」的**精确匹配**，
匹配不到就如实回报「历史无匹配」，宁可不删也不误删。

**验证证据**

- `tests/test_media_library.py`：19 条用例覆盖归一化、异常兜底、
  同步/不同步、部分失败语义、批量上限、路径穿越、路由参数校验。
- `tests/test_build.py`：新增 `test_library_tab_wired`（页面/导航/构建清单/路由四件套）
  与 `test_library_inline_handlers_use_safe_js_arg`（内联参数必须用单引号字面量）。
- 浏览器实测（1440×900，对接桩 QAS）：媒体库页加载 2 条文件 + 2 条任务，
  勾选 → 确认弹窗提示含历史同步说明 → 确认后网盘删除成功，
  本地历史由 `['电影A']` 变为 `[]`，无页面报错。

**踩坑记录（已加 CI 守卫）**：内联事件处理器参数最初用 `JSON.stringify` 生成，
产出 `"f2"`（双引号）与 `onchange="..."` 的双引号冲突，HTML 属性被截断为
`onchange="toggleLibSelect(" f2",="" this.checked)"`，勾选框毫无反应。
改为 `jsArg()` 生成单引号字面量（转义 `\ ' < & "`），并用单测锁死该形态。

## [当前] 高级筛选标题被内层底卡上边框横切（「字体重叠」）

> 对应 [issue #24](https://cnb.cool/ciweicc/DBAuto/-/issues/24)：
> 「这里的字体出现重叠」。

**先量，再改。** 截图里看着像「字体重叠」，量出来是**排版越位**：
高级筛选的标题行整行被提到了内层底卡的上边框上，卡片边框从
「高级筛选」与「仅「发现」榜单有效」胶囊中间横穿过去。

**根因：`fieldset > legend` 的 UA 特殊排版**

标题行原本写在 `<fieldset id="tmdbAdv"><legend class="tmdb-adv-legend">` 里。
UA 样式会把 `legend` 提到 fieldset 的**边框上**排版，而不是放进内边距流。
浏览器实测（1440×900）两个对照：

- 在 fieldset 首位插一个普通 `div`：顶边相对 fieldset 为 **+46.5px**（内边距流内）；
- 同一个位置的 `<legend>`：顶边相对 fieldset 为 **+0px**（落在边框上）。

于是内层底卡内容盒顶边在 **259px**，而标题行顶边在 **244px** ——
溢出 **15px**，胶囊同样溢出 15px、标题文字溢出 12.7px，
正好被 `border-radius:10px` 的上边框切开。

**改动**

- `static/src/body.html`：标题行由 `<legend>` 换成 `<div class="tmdb-adv-legend">`，
  回到 fieldset 的内边距流内。`<fieldset>` 保留（折叠时用 `disabled` 级联关闭内部
  控件的能力不能丢），分组语义与无障碍名改由 `role="group"` + `aria-label="高级筛选"` 承担；
- `static/src/styles/main.css`：给 `.tmdb-adv-legend` 补注释说明「必须是普通块级元素，
  不能退回 legend」，样式本身不变。

**验证证据（可复核）**

- 浏览器实测（9 组视口，2560×1392 → 390×844）：标题行溢出 **15px → 0px**，
  胶囊溢出 15px → 0px，标签名 `legend → div`，标题行底边不再越出底卡；
- 明暗两主题、筛选栏展开/收起两种状态各测一遍，溢出均为 0px；
  折叠时 `#tmdbAdv.disabled === true`，`disabled` 级联仍然有效；
- `tests/test_build.py` 新增 2 项静态防复发断言：
  `test_adv_legend_is_not_fieldset_legend`（产物无 `<legend>`、源码用 div、
  fieldset 与 `role=group`/`aria-label` 都在）、
  `test_discovery_dom_script_asserts_adv_legend_overflow`（守住 A7 几何断言与阈值）；
- `scripts/check_tmdb_filters_dom.py` 新增 **A7 几何断言**：
  用 `getBoundingClientRect` 比「内容盒顶边（含边框 + 内边距）」与标题/胶囊顶边，
  上限 `MAX_LEGEND_OVERFLOW = 0.5px`；
- **反向对照**：把 `<div>` 改回 `<legend>` 后，A7 在 9 组视口全部报
  「溢出底卡内容盒 15.0px」，静态断言同时失败 —— 证明门禁不是恒通过；
- `pytest tests/ -q` → **209 passed**（207 → 209）；
- 筛选栏 9 组 / 发现区 9 组 / 概览页 9 组 / 设置页 9 组 / 日志栏 10 组 DOM 走查均 0 处问题；
- 对比度 18 组 0 未达标；`ruff check .` 通过；
- 产物已用 `bash static/src/build.sh` 重建并提交，二次重建逐字节幂等。

## [当前] 概览「最近转存」去掉重复的历史入口 — 同一面板只留一个出口

> 用户反馈：「这 4 个箭头指向的跳转都同时指向了历史页面，我不需要这么多的入口，
> 我只需要保留一个跳转就可以」。

**先数入口，再删。** 「最近转存」面板在 4 行表格的上下各挂了一个链接：
表头下的「查看全部 →」和表尾的「打开历史 →」，`onclick` 都是 `switchTab('history')`。
再叠加左侧导航的「历史」与概览右列的「查看转存记录 →」，同一次操作有 4 个去向相同的入口，
其中 2 个就在同一块面板里隔了 4 行。

**改动**

- 删除面板底部的 `.ov-recent-foot` 整行（含 `.ov-recent-note` 说明文案与「打开历史 →」按钮），
  **保留面板头的「查看全部 →」**——它在面板标题旁，是用户扫视该面板时最先看到的位置；
- 「库内」列的语义说明没有丢，改为该列表头的 `title` 属性（hover 可见），
  不再为一句注释长期占据一行高度；
- 清掉对应的死样式：`.ov-recent-foot` / `.ov-recent-foot .ov-link-btn` / `.ov-recent-note`，
  以及移动端断点里 `display:none` 的 `.ov-recent-note` 选择器。

**验证证据（可复核）**

- `tests/test_build.py` 新增 2 项静态防复发断言：
  `test_overview_recent_has_single_history_entry`（源文件层面：面板内 `switchTab('history')`
  只能出现 1 次、`查看全部` 必须在、底部入口文案与死样式不得回归）、
  `test_product_dist_has_no_duplicate_history_link`（产物层面复核）。
  把底部入口加回去，第一项如期失败（`assert 2 == 1`）；
- 浏览器实测（1440×900）：面板内链接列表 = `['查看全部 →']`，`.ov-recent-foot` 不存在，
  表头 `title` 文案完整保留；
- `pytest tests/ -q` → **207 passed**（205 → 207）；
- 概览页 9 组 / 设置页 9 组 / 发现区 9 组 / 日志栏 10 组 DOM 走查均 0 处问题；
- 对比度 18 组 0 未达标；`ruff check .` 通过；
- 产物已用 `bash static/src/build.sh` 重建并提交，二次重建逐字节幂等。

## [当前] TMDB 发现区筛选栏视觉升级 + 重命名为「TMDB 发现」

> 对应 [issue #15](https://cnb.cool/ciweicc/DBAuto/-/issues/15)
> 「对发现模块的筛选框 UI 设计进行优化，当前的 UI 设计还是太素了」
> 「把发现模块重命名为 TMDB 发现」。

**先量「素」，再改。** 「太素」是主观描述，本轮先把它变成数字（1440×900 实测）：

| 度量点 | 修复前 | 修复后 |
| --- | --- | --- |
| 控件底色与面板底色的 RGB 通道均差 | **2**（肉眼无边界） | **19** |
| 筛选控件圆角 | 4px（与面板 10px 不协调） | 10px |
| 字段图标 | 0/8 | 8/8 |
| 高级筛选内层底卡与面板底色差 | 0（没有底卡） | 11 |
| 类型 chips 圆角 / 选中色 | 6px / 绿色 | 999px / 强调蓝 |
| 模块标题 | 发现 | **TMDB 发现** |

**根因**：控件用的是 `--card-2`（`rgba(17,18,23,.74)`），而它住在半透明面板
（`rgba(22,23,28,.55)`）里 —— 两者各自与页面底合成后**只差约 2 个 RGB 通道**，
观感上「控件不存在」，只剩下一排排等距的灰方块。这不是缺阴影，是缺色阶。

**改动**

- 新增 `--field-bg` / `--field-bg-deep` / `--accent-border-soft` / `--field-hi`
  四个令牌（深色 = 实色控件底，浅色 = 纯白），控件改用**实色底 + 顶部渐变高光**，
  与面板拉开 19 个通道的色阶；面板半透明（磨砂）保持不变；
- 8 个筛选字段全部配图标，标签由「大写字母间距风格」改为带图标的中文小标；
- 筛选栏分「快捷筛选 / 高级筛选」两层，各自带图标化标题栏；高级筛选整块加了
  一层内层底卡（像独立托盘），与外面的快捷筛选在视线上分得开；
- 平台多选下拉：实底浮层 + 顶部强调色高光 + 更大投影；
- 类型 chips 改为胶囊 + 选中态用强调蓝发光（旧版是绿色，与页面主色不是一套）；
- **新增「重置筛选」按钮**：一键还原 9 项条件（这一版筛选有了选中态与高亮，
  必须给出回退出口）；
- 重命名：模块标题、顶栏图标提示、指标行按钮、命令面板条目、待办按钮
  统一为「TMDB 发现」。

**顺手挖出并修掉一个真 bug（重置首次实现就是错的）**：
`clearProviderSelection() → renderProviderSelection()` 在 `list_type==='discover'`
时会顺手 `loadTmdbList()`，而 `loadTmdbList()` 会用**当时的**
`genre_id / #tmdbMinRating / #tmdbYear` 重新推导榜单类型 —— 重置中途先动平台多选、
后清 genre，榜单就被重新推导回 `discover`。实测表现是「点了重置，榜单类型没变」。
改法：新增只刷 UI 的 `syncProviderSelectionUi()`，并把 `genre_id` 清零提到平台多选之前。

**验证证据（可复核）**

- 新增 `scripts/check_tmdb_filters_dom.py`（9 组视口）：断言控件底色差 ≥4 /
  圆角 ≥8px / 8 个字段全带图标 / 高级筛选有内层底卡 / chips 胶囊且选中为强调色 /
  移动端触控 ≥32px / 标题为「TMDB 发现」 / 面板仍半透明 / **重置语义逐项核对**
  （含「改乱步骤是否真的生效」的前置校验）。**在旧版产物上跑会报 72 处问题**
  （8 类缺陷 × 9 组视口：同色 2 通道 / 圆角 4px / 0 个字段图标 / chips 6px 且选中是绿色 /
  标题「发现」/ 无分组标题 / 无重置按钮），修复后 0 处；
  把重置顺序改回错误写法，该脚本如期报「未还原 榜单类型」；
- `tests/test_build.py` 新增 3 项静态防复发断言（命名与层次 / 重置语义与顺序 /
  走查脚本阈值不得放宽）。分别把标题改回「发现」、把控件底色改回 `--card-2`、
  把重置顺序改错，门禁都如期失败；
- `pytest tests/ -q` → **205 passed**（202 → 205）；
- 概览页 9 组 / 发现区 9 组 / 设置页 9 组 / 日志栏 10 组 DOM 走查均 0 处问题；
- 对比度 18 组 0 未达标；`ruff check .` 通过；
- 产物已用 `bash static/src/build.sh` 重建并提交，二次重建逐字节幂等。

## [当前] 修复概览页首屏两列的大面积纯空白 — 板块高度不该由「最长的邻居」决定

> 对应 [issue #15](https://cnb.cool/ciweicc/DBAuto/-/issues/15)
> 「我觉得现在的 UI 设计有点丑」。用户在前几轮提出的是「设置页卡片空白太多」，
> 本轮把同一类问题在**概览页**上量化并修掉。

**复现（DOM 度量，1440×900）**：首屏「转存概览 | 最近转存」两列各 480px 高，
但左列内容只有约 210px —— 差额全是死白。

| 度量点 | 修复前 | 修复后 |
| --- | --- | --- |
| 左列底部纯空白 | **279px** | **0px** |
| 「待办事项」块内纯空白 | **291px** | **0px** |
| 概览页总高 | 1970px | 1758px |
| 首屏两列高度 | 480px（由右列决定） | 261px（由内容决定） |

**两个根因**：

1. `.ov-top{align-items:stretch}` + 「最近转存」固定渲染 10 行（≈480px）
   → 左列被迫同高，而左列只有 3 行指标 + 1 行待办；
2. `.ov-side-todo{flex:1}` 让「待办事项」块吃掉右列全部剩余高度 ——
   常态只有一条「系统运行正常，暂无待办」（32px），块却被拉到 360px
   （81% 是空的）。

**改动**：

- `.ov-side-todo` 去掉 `flex:1`：块按内容取高，剩余空间留在列尾；
- `.ov-overview` 加 `min-height:261px`，与「最近转存」4 行的面板齐平；
- 指标行改为在可用高度内均分并设 `max-height:68px` 上限
  （不设上限时被右列拉到 95px/行，指标之间空得发虚）；
- `overview.js` 新增 `OV_RECENT_ROWS_FIT = 4`，**行数改为固定值**。
  曾尝试「按实测高度反推行数」，但它与右列 `max-height` 互相触发形成正反馈
  （1366×900 实测从 268px 一路涨到 384px），已放弃并在代码注释里写明原因；
- `.ov-side` 两块统一 14px 竖向内边距，分隔线与内容贴齐。

**验证证据（可复核）**：

- `scripts/check_overview_dom.py` 新增 `leftBlank` / `todoBlank` 两组度量与
  断言（阈值 `MAX_HERO_BLANK = 24px`，9 组视口）。**在修复前的产物上跑会报
  271px（左列）/ 291px（待办块）**，修复后 0 处 —— 门禁确实拦得住；
- `scripts/check_discovery_dom.py` 的浮层可见性等待由固定 `sleep(700ms)`
  改为轮询（超宽视口下 transition 偶发超过 700ms，会把动画中间态误判为
  「浮层不可见」，实测 2560×1392 连续复现）；
- `tests/test_build.py` 新增 2 项静态防复发断言
  （锁住 `min-height` / 禁止 `flex:1` / 禁止高度反推 / 门禁阈值不被放宽）；
- `pytest tests/ -q` → **202 passed**（200 → 202）；
- 概览页 9 组 / 设置页 9 组 / 日志栏 10 组 / 发现区 9 组 DOM 走查均 0 处问题；
- 对比度 18 组 0 未达标；`ruff check .` 通过；
- 产物已用 `bash static/src/build.sh` 重建并提交，二次重建逐字节幂等。

## [当前] 修复发现区选中资源后看不到转存按钮 — CSS 包含块把浮层钉在了页面深处

> 对应 [issue #15](https://cnb.cool/ciweicc/DBAuto/-/issues/15)
> 「有一个很严重的问题。就是发现页面选中的资源后没有弹出转存按钮」。

**根因（CSS 包含块，不是「按钮没渲染」）**：`.tmdb-sel-bar` 是 `position:fixed`
浮层，但**固定定位并不总是相对视口** —— 只要祖先链上存在
`filter` / `backdrop-filter` / `transform` / `perspective` / `contain:paint`，
它就会改为相对那个祖先定位。issue #14 把 TMDB 并入概览页后，浮层住进了
`.ov-panel-discovery`（`#tmdbPanel`），而 `.ov-panel` 带着
`backdrop-filter: var(--blur)`，于是浮层被钉在**面板内部**。

实测（1440×900，点选第 1 张海报卡后立刻测量 `#tmdbSelBar` 的 rect）：

| 祖先上的 backdrop-filter | 浮层视口 top | 结果 |
| --- | --- | --- |
| 保留 `.ov-panel`（现状） | **1361**（视口高只有 900） | 整体在屏幕下方，看不到 |
| 去掉 `.ov-panel` | **846** | 贴底可见 |

**隐藏的第二处缺陷（同一根因的另一半）**：即使浮层落在视口内，
右侧仍被硬编码留白 `right:340px` 顶住。1440~1600 视口下右边界与内容区之间
多出 20~80px，按钮组被挤到第二行，浮层高度从 54px 涨到 **92px**；
而它又是固定定位 —— 换行后「已选 N 部」与按钮自己盖住自己，
「开始转存」的命中测试落到 `#tmdbGrid` 上，照样点不动。

**改动**：

1. **`.ov-panel` 去掉 `backdrop-filter`**（含降级分支），从根上断掉包含块；
   面板本身是 55% 半透明，磨砂观感不受影响，并留注释防止回归。
2. **`#tmdbSelBar` 加硬兜底**：`filter / backdrop-filter / transform / perspective`
   一律 `none !important`；`tmdb.js` 再写一遍内联样式
   （`!important` 类规则 > 内联 > 普通类规则，两层叠加后正常样式表无法再降级它）。
3. **新增 `--z-selbar: 450`**：高于 `--z-backtop(400)` 与 `--z-fab(150)`。
   右下角的「回到顶部」/ 日志 FAB 本来就压在「开始转存」上，
   现在选中态下两个 FAB 一起上移让位（转存按钮是选中后的唯一动作）。
4. **边界改为实测**：`tmdbSelBarBounds()` 用 `.content` / 侧栏 / 日志栏的
   `getBoundingClientRect` 内联设置 `left/right/bottom`，移动端按 `.bottom-nav`
   实测高度让位（不再硬编码 76px）；`resize` 只绑一次（rAF 合并）。
5. 共享磨砂选择器里移除 `.tmdb-sel-bar`（它自己已是浮层，不需要再叠一层模糊）。

**效果（9 组视口，浮层四条边 + 命中测试）**：

| 视口 | 浮层视口 top | 高度 | 「开始转存」命中自身 |
| --- | --- | --- | --- |
| 2560×1392 | 1338 | 54 | ✓ |
| 1920×1080 | 1026 | 54 | ✓ |
| 1600×900 | 846 | 54 | ✓ |
| 1440×900 | 846 | 54 | ✓（修复前 **1361**，不可见） |
| 1280×900 | 846 | 54 | ✓ |
| 1200×900 | 846 | 54 | ✓ |
| 1024×768 | 714 | 54 | ✓ |
| 760×900 | 846 | 54 | ✓ |
| 390×844 | 713 | 58 | ✓ |

**验证证据（可复核）**：

- 新增 `scripts/check_discovery_dom.py`（9 组视口）：断言点选后浮层进入
  `.active`、不透明度 ≥0.9、四条边不越出视口、高度 ≤66px（不换行）、
  「开始转存」中心命中回浮层自身；失败时额外打印「定位包含块探针」指出元凶。
  **在修复前的产物上跑会报 29 处问题**（含 1440×900 top=1361），修复后 0 处。
- `tests/test_build.py` 新增 2 项静态防复发断言
  （`#tmdbSelBar` 兜底声明、`.ov-panel` 不得含 backdrop-filter、
  共享磨砂规则不得含 `.tmdb-sel-bar`、`--z-selbar` 层级序、边界函数接线）。
  分别把 `.ov-panel` 的 backdrop-filter 加回去 / 删掉 `#tmdbSelBar` 兜底，
  门禁都如期失败。
- `pytest tests/ -q` → **200 passed**（198 → 200）。
- 端到端点击验证：dark / light / 移动端三种形态下真实点击「开始转存」，
  均发出 `POST /api/transfer` 并收到成功 toast。
- 概览页 9 组 / 设置页 9 组 / 日志栏 10 组 DOM 走查均 0 处问题（无回归）；
  `ruff check .` 通过；对比度 18 组 0 未达标。
- 产物已用 `bash static/src/build.sh` 重建并提交，二次重建逐字节幂等。

## [当前] 移除「发现快捷」横滑条 — 同一批热门数据只渲染一份

> 对应 [issue #15](https://cnb.cool/ciweicc/DBAuto/-/issues/15)
> 「把发现快捷那个栏去掉。内容重复了」。

**缺陷（真实 DOM 取证）**：issue #14 把 TMDB 整块搬进概览页之后，首屏上方那条
「发现快捷」横滑条与下方 `#tmdbPanel` 发现区**读的是同一个接口**
（`/api/tmdb/list?media_type=movie&list_type=trending&page=1`）、渲染的是同一批
「海报 + 标题 + 评分」，只是尺寸不同（88px 小卡 vs 完整网格）。

在 1440x900 视口下用确定性桩数据实测（同一批 12 条热门）：

| | 「发现快捷」栏标题数 | 发现区标题数 |
| --- | --- | --- |
| 移除前 | **12 条**（与发现区逐条相同） | 12 条 |
| 移除后 | **DOM 不存在** | 12 条 |

也就是说首屏凭空多出一整栏、外加一次重复的 TMDB 请求，信息量为零。

**改动**：

- 删除 `static/src/body.html` 里的 `.ov-panel-rec` 面板（标题「发现快捷」+
  `#ovRecScroll` 横滑条 + 「去筛选 / 转存」按钮）。
- 删除 `overview.js` 的 `renderOvRecs()`、骨架屏里的海报卡骨架，
  以及概览加载时那次 `/api/tmdb/list` 请求（`Promise.allSettled` 收敛回 2 个）。
- 删除对应 CSS（`.ov-panel-rec / .ov-rec-scroll / .ov-rec-card / .ov-rec-*`）
  与骨架样式（`.ov-rec-skel / .ov-skel-poster / .ov-skel-line`）。
- **保留** `jumpToDiscovery()`：顶栏 TMDB 图标按钮、指标行「去发现」、
  ⌘K「前往 发现（TMDB）」仍依赖它滚到发现区。
- `--ov-rec-w` 令牌保留（现在只描述首屏右列「服务状态 + 待办」的宽度），
  注释与 `tokens.css` 说明同步更新。

**防复发**：

- `tests/test_build.py` 新增 `test_discovery_quick_panel_removed`：断言
  `renderOvRecs / ovRecScroll / ov-rec-* / 发现快捷` 在产物中不存在、
  概览页不再拉 `/api/tmdb/list`，同时断言 `#tmdbPanel` 仍在概览页内
  （去重复不等于把发现能力一起删掉）。同时删除已随面板下线的
  `test_overview_recommendation_poster_uses_api_contract`（其渲染代码已不存在）。
- `scripts/check_overview_dom.py` 新增真实 DOM 断言 `quickPanel`：
  `#pageOverview .ov-panel-rec, #ovRecScroll` 必须不存在。

**验证证据**：

- `pytest tests/ -q` → **198 passed**（删 1 项失效断言、增 1 项防复发断言）。
- 概览页 DOM 走查 9 组视口 **0 处问题**（首屏底边 551，与移除前一致；发现区仍在页内、
  筛选栏仍在渲染）；设置页 9 组、日志栏 10 组均 0 处问题（无回归）。
- `ruff check .` 通过；`check_contrast.py` 18 组 0 组未达标。
- 产物由 `bash static/src/build.sh` 重建（229901 bytes），未手工编辑。

> 说明：底层工程治理与安全加固（P0 / P1 层级）已由仓库既有提交等价实现并合并至 `master`
> （例如 `500571b` 依赖锁定 + Dependabot + CI 漏洞扫描、`d465526` Docker 非 root + ruff 门禁、
> `9563551` 前端构建 minify + 删除重复登录页，以及更早的 P0 治理基线）。

## [当前] TMDB 并入概览页 — 一个页面，一套筛选栏

> 对应 [issue #14](https://cnb.cool/ciweicc/DBAuto/-/issues/14) 第三轮追问：
> 「我想直接把完整的筛选栏功能，就是 TMDB 页面的功能，原版的移植到概览页，
> 这样就可以少一个页面」。

**背景（前两轮的取舍被推翻）**：PR #16 的结论是「TMDB 只留发现页一个目的地，
概览只做快照 + 跳转」。用户明确表示要的是**反过来**——把 TMDB 整块搬进概览页。
本轮按用户诉求重做：**不再有两个页面，也不再有第二块海报墙**。

**改动（结构性，非样式微调）**：

- `#pageTmdb` 页面容器**删除**；原 TMDB 的筛选栏 / 多选 / 转存 / 分页
  **整块迁移**为概览页内的 `#tmdbPanel` 发现分区（DOM 与 ID 原样保留，
  `#tmdbGrid / #tmdbFilters / #tmdbSelBar / #tmdbPagination` 全站只此一份）。
- 导航收敛：侧栏「发现」与移动端底栏「发现」移除（5 项，与 `BOTTOM_NAV` 严格对齐）；
  顶栏新增 TMDB 图标按钮作为移动端/窄屏的直达入口，⌘K 命令面板的「前往发现」保留。
- 概览页首屏保留一条「发现快捷」横滑条（原「热门推荐 / TMDB 快照」）：
  点任意海报 → `jumpToDiscovery()` 滚到下方发现分区，不再跳页。
- `PAGE_IDS / PAGES / BOTTOM_NAV` 同步去掉 `tmdb`；
  `switchTab('overview')` 时按滚动位置刷新发现分区状态（回顶按钮 / 滚动加载）。

**关键实现细节（都是踩出来的）**：

- **作用域查找白名单**：为了让同一套 ID 在概览页里工作，`tmdb.js` 的 DOM 访问
  统一走 `getId()/query()/queryAll()`。但**不能**把所有 `getElementById()` 一律
  改写成「先查 `#tmdbPanel` 作用域」——页面级节点（`#log` 日志区、`#stopBtn`）会
  解析成 null，实测在 `getId('log').textContent=''` 上抛 TypeError、**转存直接中断**。
  因此只对 TMDB 自己的控件做作用域解析，`log/stopBtn/logPanel/searchInput`
  留在全局白名单里。
- **回到概览不再重置筛选**：旧版每次进 TMDB 都无脑 `loadTmdbList()`（回第 1 页）。
  TMDB 常驻概览后若沿用，用户翻到第 5 页、去设置再回来会「凭空回到第 1 页」。
  改为 `enterTmdbDiscovery()`：筛选未变则只滚回发现区，变了才重载。
- **滚动加载 / 回顶按钮改按「发现区是否可见」判定**（`updateTmdbVisibility()`），
  不再用「页面是否激活」——否则在概览页顶部滚动就会触发无限加载、
  回顶按钮也会浮在概览工作区上。翻页回顶改为回到发现区顶部而非整页顶部。
- **筛选栏折叠回归可达性**：折叠（`display:none`）时把高级筛选控件的表单语义
  一并置 `disabled`，否则 Tab 键会依次走进 5 个不可见控件。
- **顺手修掉一个既有的初始化中断**：`initTmdbPage()` 读 `opts.languages.map(...)`
  时没有兜底，桩数据 / 老接口只给 `countries` 或 `regions` 时抛
  `undefined.map`，整段初始化中断（海报墙空着、地区/语言下拉全空）。
  另外 `movie_list_types` 只认 `code` 字段，拿到 `{id,name}` 会渲染出
  `value="undefined"` 的下拉项。两处都补了字段兜底。
  未配置 API Key 时的早 `return` 也改为「只提示、不早退」，否则整块发现区停在空壳。
- **首屏度量口径修正**：`check_overview_dom.py` 原先断言「`#pageOverview` 最后一个块
  的底边 ≤ 视口高」。发现区常驻概览页后，该断言会恒为失败（它的底边天然在折叠线以下）。
  改为「发现区之前那些块的最大底边」= 首屏折叠线，并显式断言「发现区在 `#pageOverview` 内」
  「概览页内渲染出了筛选栏」。另外折叠线测量前先把 `.content.scrollTop` 归零，
  使结论与滚动位置无关。

**验证证据**：

- `pytest tests/ -q` → **198 passed**（`test_build.py` 52 项，本轮新增 7 项防复发断言：
  单页化 / 原版筛选字段齐全 / 作用域白名单 / 进入不重置 / 可见性判定 / 折叠可达性 / 单套表面）。
- `ruff check .` 通过；`check_contrast.py` 18 组 0 组未达标。
- DOM 走查：概览 9 组（新增「发现区页内」「筛选栏已渲染」两项断言）、
  日志栏 10 组、设置页 9 组，全部 exit 0。
- E2E：`merge_test`（已重写为单页断言：点快照→滚到同页发现区→筛选栏可折叠→
  选片→转存后仍在概览页）、`smoke_test`、`phase3_test`、`phase4_test`、`phase5_test`
  全部退出码 0。
- 产物重建幂等（`bash static/src/build.sh` 后 `git diff` 为空）。

## [当前] 设置页空白空间整改 — 分组并排 + 取消强制等高

> 对应 [issue #15](https://cnb.cool/ciweicc/DBAuto/-/issues/15)
> 「设置页。有部分卡片占用的空间非常多存在大部分的空白空间」。

**缺陷（DOM 度量复现）**：设置页在 2560x1392 视口下页面总高 **1756px**、
卡片可用空白低至 **27%**；「搜索服务」卡片内容只有 149px 高，却因为与
「TMDB 数据源」同行被 `align-items:stretch` 强行等高，被撑到 337px，
**底部空出 202px 纯空白**（1440px 视口下 220px、760px 下 238px）。

**根因**（两处，叠加放大）：

1. `.settings-groups` 是 `flex; flex-direction:column` —— 每个分组独占整行；
   分组内的 `.settings-grid{auto-fit,minmax(300px,1fr)}` 于是按**容器宽度**
   （1440px）而非内容宽度分配轨道，单卡分组里的卡片被拉到 1340px 宽。
2. `.settings-grid{align-items:stretch}` 让同行卡片强行等高，短卡被撑高，
   多出来的高度全部变成底部空白。

**修复**：

- 分组层改为 `grid`，自适应并排（内容少的分组只占半行，另一半交给下一个分组）；
  `.settings-group`（`<details>`）显式 `display:grid` 并让 `summary` 跨整组。
- 去掉 `align-items:stretch`，改为 `align-items:start`（卡片按内容高度排布）；
  单卡内仍保留 `margin-top:auto` 让底部动作位贴底。
- 卡片宽度上限用 `max-width` 表达（`--set-card-max`），**不写进 minmax 上界**
  —— 写了会把轨道钉死，同组两卡反而退回纵向堆叠（实测页高从 1151px 反弹到 1455px）。
- 新增 `.set-fields` 字段容器 + `@container settings-card` 容器查询：
  卡片宽度 ≥600px 时字段铺成两列，把「留空不修改」这类提示占掉的纵向空间收掉；
  奇数个字段的最后一项跨列，避免末尾半格空白。
- 单卡分组放开宽度上限（铺满组宽 + 两列字段），修掉 1024px 下「984px 组里放
  660px 卡」的右侧空白。

**效果**（`scripts/check_settings_dom.py` 实测，卡片底部空白，单位 px）：

| 视口 | 页高 前 → 后 | 卡片底部空白 前 → 后 |
| --- | --- | --- |
| 2560x1392 | 1756 → **1151** | 202 → **13** |
| 1920x1080 | 1756 → **1169** | 202 → **13** |
| 1440x900 | 1774 → **1585** | 220 → **13** |
| 1024x768 | 1756 → **1567** | 202 → **13** |
| 390x844 | 2059 → **2051** | 27 → **13** |

（13px = 卡片自身 `--card-pad` 底部内边距，即已无冗余空白。）

**验证**：

- 新增 `scripts/check_settings_dom.py`（9 组视口）——断言「卡片底部纯空白 ≤40px /
  超宽视口页高 ≤1500px / 字段区宽 ≥260px / 无横向溢出」。该脚本在**修复前**的产物上
  报出 11 处问题（含「搜索服务」202px 空白），修复后 0 处。
- `tests/test_build.py` 新增 4 项静态防复发断言（分组并排 / 禁止强制等高 /
  字段两列容器查询 / 度量脚本落库）。
- `pytest tests/` 191 项通过（187 → 191）；`python scripts/check_contrast.py` 18 组
  0 未达标；`check_overview_dom.py` / `check_log_panel_dom.py` 均 0 问题。

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

## [当前] 修复「删除确认点取消也会删」+ 媒体库根目录可配置（issue #27 严重问题）

> 对应 [issue #27](https://cnb.cool/ciweicc/DBAuto/-/issues/27) 用户反馈：
> 「按下删除键弹出的确认窗口，无论点取消还是确认，都会删除掉，这是一个非常严重的问题」
> 「我需要界定资源根目录的设置选项，指定其中一个路径当作媒体库的根目录」。

**1. 确认框「取消也执行」——根因与修复**

`showConfirm()` 返回 `Promise<bool>`：确认 resolve(true)，取消/遮罩/ESC resolve(false)，
**从不 reject**。而两个删除入口写成了：

```js
try{ await showConfirm(...); }catch(e){ return; }   // 把「resolve」误当成「同意」
// 紧接着无条件执行删除 → 点取消/按 ESC 也一样删
```

`catch` 分支永远走不到，取消的 `false` 被直接丢弃，破坏性操作没有任何护栏。

修复：
- `static/src/scripts/confirm.js`：新增语义化入口 `confirmThen()`，
  内部把返回值收敛为 `ok === true`，把「取返回值」这件事固化下来，
  破坏性操作只调用它，就不会再漏判；同时修掉重复打开确认框时旧 Promise 悬挂的问题。
- `static/src/scripts/library.js`：`doDelete()`（网盘删除）与
  `doDeleteQasTasks()`（QAS 任务删除）改用 `confirmThen()`，并在其后**显式早退**
  `if(!confirmed) return;`。
- `static/src/scripts/history.js`：清空历史同样改走 `confirmThen()`。

**2. 媒体库根目录可配置**

- `app_modules/config.py`：`savepaths.library_root`（默认 `/`），作为媒体库浏览下界。
- `app_modules/media_library.py`：`get_library_root()` 读取并规范化该值，
  非法值（为空 / 非绝对路径 / 含 `..`）一律回落到 `/`，保证入口永远可用。
- `app_modules/routes_library.py`：`/api/qas/library` 未显式传 `path` 时使用配置的根目录。
- `app_modules/routes_config.py`：`savepaths` 校验列表加入 `library_root`。
- 前端：设置页「转存路径」新增「媒体库根目录」输入；媒体库页以此为起点，
  **不能返回其上一级**（面包屑根节点、`libGoUp`、上一级按钮三处同时约束）。

**3. 媒体库卡片观感**

- 文件夹卡不再被同行海报卡拉伸（`align-self:start`），消除大片空白；
- 无海报的文件卡占位块由 2:3 改为紧凑的 16:9，不再拉高整行。

**验证**

- `tests/test_confirm_guard.py`：新增 8 例。源码层锁定「破坏性操作必须用
  `confirmThen` + 早退」，并用 node 实跑 `confirmThen` 语义（取消/undefined/0/''/null 均返回 false）。
  已验证：把 `doDelete` 还原成裸 `showConfirm` 会让用例失败。
- `tests/test_media_library.py`：新增 5 例覆盖 `get_library_root` 规范化/回落，
  以及路由「无 path 用配置根目录 / 显式 path 优先」。
- `tests/test_build.py`：更新 QAS 删除用例，断言强化为 `confirmThen` + 早退。
- `scripts/check_settings_dom.py`：桩数据补充 `library_root`。
- 全量 `pytest` 294 通过；`check_contrast.py`、`check_settings_dom.py`、
  `check_library_dom.py`、`ruff check .` 均通过。
