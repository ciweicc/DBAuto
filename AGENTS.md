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
```

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
