# 更新日志 (Changelog)

本文件记录 DBAuto 的重要变更。按合并 / 发布倒序排列。

> 说明：底层工程治理与安全加固（P0 / P1 层级）已由仓库既有提交等价实现并合并至 `master`
> （例如 `500571b` 依赖锁定 + Dependabot + CI 漏洞扫描、`d465526` Docker 非 root + ruff 门禁、
> `9563551` 前端构建 minify + 删除重复登录页，以及更早的 P0 治理基线）。

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
