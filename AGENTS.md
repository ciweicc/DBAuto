# AGENTS.md — 给 AI/自动化协作者

本文件约束在本仓库内工作的 AI Agent（含 NPC / CodeBuddy 等）。
提交任何 PR 前请完整阅读「任务完成门禁」。

## 1. 仓库速览

- **产品**：DBAuto —— 豆瓣 → 夸克网盘的自动转存服务（Python 3.12 + vanilla JS）。
- **后端**：`app_modules/`（Flask 蓝图 + 调度器 + SQLite 存储）。
- **前端源码**：`static/src/**`（`styles/`、`scripts/`、`body.html`）。
- **前端产物**：`static/index_new.html`（**生成文件，随仓库提交，禁止手工编辑**）。
- **文档**：`docs/`；变更记录 `docs/CHANGELOG.md`。

## 2. 构建与校验命令

```bash
bash static/src/build.sh                     # 前端产物重建（唯一正确入口）
python -m pytest tests/ -q                   # 单元测试（CI 收集范围）
python -m pytest tests/test_build.py -q      # 产物结构 / 防复发断言
ruff check .                                 # 静态检查（仅 E9 + F821）
python scripts/check_contrast.py             # 对比度门禁（改 tokens.css 后必跑）
python scripts/check_overview_dom.py --serve # 概览页 DOM 度量走查（改概览页后必跑）
python scripts/check_log_panel_dom.py --serve # 日志栏 DOM 度量走查（改日志栏后必跑）
python scripts/check_settings_dom.py --serve  # 设置页空白度量走查（改设置页后必跑）
python scripts/check_library_dom.py --serve  # 媒体库顺序/加载反馈/缓存度量走查（改媒体库后必跑）
python scripts/check_library_keys_dom.py --serve    # 媒体库键盘选择走查（改键盘逻辑后必跑）
python scripts/check_library_selection_dom.py --serve # 媒体库选中/取消选中走查（改勾选逻辑后必跑）
python scripts/check_qas_keys_dom.py --serve # QAS 任务表键盘选择走查（改任务表后必跑）
python scripts/check_qas_view_dom.py --serve # QAS 任务表列表/卡片视图切换走查（改任务表后必跑）
python scripts/check_login_dom.py --serve    # 登录页真实合成对比度/磨砂/色带走查（改登录页后必跑）
python scripts/check_transfer_schedule_dom.py --serve # 转存页/定时任务管理走查（改转存页后必跑）
python scripts/check_search_kbd_dom.py --serve # 顶栏搜索框快捷键走查（改快捷键/顶栏后必跑）
python scripts/check_share_detail_dom.py --serve # 搜索结果文件清单展开走查（改搜索结果/分享预览后必跑）
python scripts/check_search_dropdown_surface_dom.py --serve # 搜索结果下拉底衬实度走查（改搜索下拉/浮层底色后必跑）
python scripts/check_sidebar_dom.py --serve  # 侧边栏选中动效走查（改导航/侧边栏后必跑）
python scripts/check_tmdb_placeholder_dom.py --serve # TMDB 无封面占位卡居中/同轴走查（改占位图后必跑）
```

> `scripts/check_library_selection_dom.py` 覆盖媒体库的「选中 / 取消选中」：
> 点勾选框、点行空白、Ctrl/⌘+点击、空格、Ctrl+A+Esc、Shift+连选六条路径，
> 每条都同时校验**逻辑状态（`libSelected`）与屏幕状态（`input:checked`）**，
> 外加操作条计数与「删除所选」按钮的可用性 —— 这两者不一致就是
> 「看着选了、删的时候没有」这类最贵的 bug。issue #68（「选中后无法取消」）
> 就是它抓出来的：改动前 16 处问题，改动后 0 处。
> `scripts/check_overview_dom.py` 需要 playwright + chromium；缺少时会以退出码 0
> 跳过，不会阻断 CI。它是「改概览页 → 先跑脚本拿数值证据」的入口，
> 能替代大部分截图走查（首屏一屏内 / 指标行等高 / 无横向滚动 / 状态与日期语义 /
> 移动端分类前缀宽度）。`scripts/check_settings_dom.py` 同理，重点断言「卡片底部
> 纯空白 ≤40px / 超宽视口页高 ≤1500px / 无横向溢出」，对应 issue #15 的
> 「卡片占位过多、大片空白」。
> `scripts/check_library_dom.py` 覆盖媒体库页：网盘文件必须排在 QAS 任务之前且
> 首屏可见、请求发出即有带时长的加载反馈、目录接口卡死时按时快速失败并可重试、
> 返回父目录命中缓存（不再打网络）。
> `scripts/check_qas_view_dom.py` 覆盖 QAS 任务表的「列表视图 / 卡片视图」切换：
> 真点按钮断言「换了布局且**没发新请求**」、卡片里选择语义与表格逐条一致
> （点卡片 / Ctrl+A / Shift+←→ / 空格 / Esc）、↑↓ 在网格里让位给浏览器滚动、
> 筛选与分页在两种布局下一致、卡片高度与路径折行（卡片存在的意义）、
> 偏好写进 `qas_view` 且与媒体库的 `lib_view` 不串味。
> `scripts/check_transfer_schedule_dom.py` 覆盖「转存」页（含合并进来的定时任务管理）：
> 导航里不再有独立的「定时」页、分类 chips 与筛选控件**只有一份**（重复 DOM 已消除）、
> 定时任务管理控件齐全（启用开关 / 时间 / 下次执行 / 立即执行 / 检测目录）、
> 「下次执行」按「时间 / 未启用 / 计算失败」三态显示、勾选榜单确被写进保存请求、
> 点「立即执行」发出的 run_now 请求**带 section**（此前概览页漏传，按钮点了必 400）。
> `scripts/check_sidebar_dom.py` 覆盖侧边栏的「选中动态效果」（issue #84：
> 「侧边栏选项添加选中动态效果」）。它钉住的是「选中这件事必须看得见位移」：
> 侧边栏里**恰有一块**滑动底板，切换时底板平滑滑到新项（用页面内 rAF 连续采样，
> 判据是「位移途中出现过中间位置」，不是「等 120ms 再量一次」——
> headless 下 rAF 会被节流，固定窗口会随机量到起点或终点）、
> 切换瞬间新旧不同时亮、底板就位后选中项不再自绘底色（两层叠加会过亮）、
> **首帧就站在正确位置**（否则一加载底板从列表顶部滑下来，像动画故障）、
> 点击脉冲会被回收（恒亮=整排都在动）、窄屏抽屉里不做无意义动画。
> 反向验证：把 `.side-nav-ind.anim` 的过渡摘掉，它会立刻红 2 项。
>
> `scripts/check_search_kbd_dom.py` 覆盖顶栏「资源搜索」框的快捷键（issue #78：
> 「这个搜索框的快捷键是无效的」）。它钉住的是「提示长在谁身上、谁就得响应」：
> 搜索框里的 `Ctrl+K` / `⌘K` 徽标按下后必须**聚焦这个搜索框**，而不是打开另一个
> 东西（命令面板已挪到 `Ctrl/⌘ + /`）；徽标按平台显示（Windows 没有 ⌘ 键）；
> 焦点在别的输入框里时按下要**跳过来且不留下字面 k**；窄视口下搜索框默认
> `display:none`，按下必须先展开移动搜索层再聚焦，否则就是「按了没反应」。
>
> `scripts/check_share_detail_dom.py` 覆盖搜索结果的「文件清单」展开（issue #88：
> 「搜出来点进去不能看集数全不全」）。它钉住的是「展开这件事真的发生了、且真的
> 拿到了清单」：有链接的结果才给「文件」入口、**展开前零请求**（懒加载）、
> 展开后真的渲染出文件行与体积、集号摘要真的**把缺口指出来**（缺 8、9）、
> 再点一下真的收起、**二次展开零请求**（前端缓存）、失败态**带真实原因**
> （静默空白会让人以为分享是空的）。反向验证：把集号摘要改成只显示条目数，
> 它立刻红 1 项。
>
> `scripts/check_search_dropdown_surface_dom.py` 覆盖搜索结果下拉的**底衬实度**
> （issue #90：「搜索结果和背景混在一起」）。浮层专用底 `--glass-bg-overlay`
> （0.94/0.95）必须就位：面板 alpha ≥0.90 且**等于该 token**（钉死不退回卡片底
> 0.55 或强玻璃底 0.74）、展开区不再叠第二层半透明、文件清单是实底，
> 外加**真实像素校验** —— 截一小条面板留白列，量亮度离散度（std ≤8）。
> 「透了」是合成后的观感，这里把它量化成「面板 alpha」+「留白列的明暗起伏」。
> 反向验证：把面板退回 `--card`，它立刻红 8 项，像素离散度从 3~4 涨到 18~21。
>
> `scripts/check_qas_keys_dom.py` 覆盖 QAS 任务表的键盘选择：Ctrl+A 只勾**当前
> 可见行**（筛选/分页后不越界）、Shift+↑↓ 可扩可缩且不吞掉先前选择、↑↓ 到页末
> 接着翻页、焦点在搜索框里时全部放行。它还会验证两张表的快捷键**归属**：
> 点过哪张表，快捷键就作用在哪张表（媒体库优先会吃掉 Ctrl+A，实测踩到）。

> 不要调用 `static/src/build.py`：它是历史遗留实现，输出与 `build.sh`
> 存在细微差异（换行/指纹派生方式），用它重建会把产物改成漂移版本。
> 详见 `docs/CONTRIBUTING.md`。

## 3. 自动化长任务规范（硬性）

Agent 的上下文预算是最稀缺的资源，**用上下文换取的执行顺序必须是：
「先用廉价方式证明，再用昂贵方式取证」**。

1. **后台长任务禁止与前台命令混跑**
   起服务必须用 `nohup <cmd> >/tmp/<name>.log 2>&1 &`（或 `setsid`）并
   **重定向全部输出**：不加 `nohup`/重定向时，命令工具会等待其退出或把
   服务日志回灌进 Agent 上下文。
2. **禁止 `curl -N` / `curl` 拉取 SSE（`/api/sse`）或任何流式/长连接接口**
   —— 这些请求永不返回，会直接挂死本回合。SSE 相关逻辑用单元测试或
   直接读源码验证，不做端到端拉流。
3. **禁止 `git status` / `git diff` 全量输出**
   用 `git status --porcelain`、`git --no-pager diff --stat` 或限定路径。
4. **读图（截图/png）是重量级操作：单次任务总量硬上限 ≤ 8 张，单轮 ≤ 2 张**
   每张截图按约 400–980KB 进入上下文，几十张就会触发
   `500 Internal error: request entity too large`（本项目已因此报废两次运行，
   见 `docs/CHANGELOG.md` 顶部两条记录）。
   - 优先用「断言脚本」替代「看图」：用 DOM 度量（`getBoundingClientRect`）、
     计算样式、`document.querySelector` 存在性来判定，这些都是廉价文本证据；
   - 只有在断言无法表达**视觉问题**（对齐、溢出、层叠）时才读图；
   - 需要多视口/多主题时，先跑脚本产出全部数值指标，**只对异常项**读图。
5. **禁止把大文件全文读入上下文**
   产物 `static/index_new.html` 约 220KB，禁止 `cat`；用 `grep`/`rg` 定位片段。

## 4. 任务完成门禁（提交 PR 前必须全部满足）

- [ ] **1. 先本地复现/验证，再谈交付**
      任何「已修复」的结论都必须由本仓库内可执行的证据支撑：单元测试输出、
      脚本输出、确定性断言结果。**不要**把「CI 会验」「跑一下 CI 看看」当成验证。
- [ ] **2. 交付物必须落库**
      代码/文档改动 → 提交到 PR 源分支；
      验证手段 → 若是「跑一段临时脚本」，必须把其中**确定性的、可复跑的**部分
      落库（`tests/` 或 `scripts/`），不要只存在于 Agent 的一次性 shell 历史里。
      纯视觉、依赖浏览器截图的走查不进 CI，另见 `docs/CONTRIBUTING.md`。
- [ ] **3. 改了 `static/src/**` 必须重建产物**
      `bash static/src/build.sh`，并把 `static/index_new.html` 一起提交。
- [ ] **4. 必须提交 PR，并在 PR 描述中写明验证证据**
      未产出 PR 的「总结性评论」不算交付；结论要能被人工复核。
- [ ] **5. 不要轮询 CI**
      推送后立即结束。CI 失败会自动重新唤起 Agent。

## 5. 分支与提交约定

- 从 `master` 切特性分支（如 `fix/xxx`、`auto/xxx`），提交信息用
  `fix:` / `feat:` / `docs:` / `test:` 前缀，保持小而聚焦。
- **不要**直接 push `master`，**不要**自行合并 PR —— 合并由人工决定。
