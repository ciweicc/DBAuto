# 更新日志 (Changelog)

本文件记录 DBAuto 的重要变更。按合并 / 发布倒序排列。

> 说明：底层工程治理与安全加固（P0 / P1 层级）已由仓库既有提交等价实现并合并至 `master`
> （例如 `500571b` 依赖锁定 + Dependabot + CI 漏洞扫描、`d465526` Docker 非 root + ruff 门禁、
> `9563551` 前端构建 minify + 删除重复登录页，以及更早的 P0 治理基线）。

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
