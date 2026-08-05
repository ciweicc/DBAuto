# 贡献指南 (Contributing)

感谢参与 DBAuto 开发！请在提交 Pull Request 前阅读以下约定。

## 开发环境

- **Python 3.12+**（仓库已用 `.python-version` 固定为 3.12）。
- 建议使用虚拟环境：

  ```bash
  python -m venv .venv
  source .venv/bin/activate        # Windows: .venv\Scripts\activate
  pip install -r requirements-dev.lock.txt
  ```

  运行时仅需 `requirements.lock.txt`；开发 / 测试额外需要 `requirements-dev.lock.txt`
  （含 pytest / pytest-cov / pytest-rerunfailures）。

## 运行与测试

- 启动服务：`python main.py`（数据目录 `DATA_DIR`，默认 `/data/douban-history`）。
- 运行单元测试：

  ```bash
  python -m pytest -q
  ```

  > ⚠️ **本地请勿加 `--cov`**：CI 使用 `python -m pytest tests/ ... --cov=app_modules --cov-fail-under=40`
  > 作为覆盖率门禁，但本地带 `--cov` 可能触发环境相关的内部错误。本地验证请用上面的纯净命令。
  > E2E / 冒烟测试（`*_test.py`）依赖 playwright 与实时服务，不在默认 `pytest` 收集范围内
  > （见 `pyproject.toml` 的 `python_files = ["test_*.py"]`）。
- 静态检查：`ruff check app_modules tests`（当前仅启用 `E9` 语法错误与 `F821` 未定义名称）。

## 分支策略

1. 从 `master` 切出特性分支（如 `p2-improvements`、`fix-xxx`）。
2. 在分支上完成改动并提交（保持提交小而聚焦、信息清晰，参考现有 `chore:` / `feat:` 前缀）。
3. 发起 Pull Request 到 `master`，通过 CI（tests / lint / frontend-build / dependency-scan / Trivy）后，
   由维护者 review 并合并。
4. **不要**直接 push 到 `master`，也不要自行将特性分支 merge 到 `master`。

## 代码风格

- 以 `pyproject.toml` 中的 `[tool.ruff]` 配置为准：
  - `line-length = 160`，`target-version = "py312"`；
  - 前端源码（`static/src`）不在 lint 范围；
  - 当前 lint 仅启用 `E9`（语法错误）与 `F821`（未定义名称），并放行 `F403` / `F405`
    （项目大量使用 `from module import *`）。
- 保持改动最小、可读；**不要**顺手格式化无关文件。

## 依赖锁工作流

依赖以锁文件提交，保证构建可复现；CI 从锁文件安装：

- 顶层依赖声明在 `requirements.txt`（运行）与 `requirements/requirements-dev.txt`（开发 / 测试）。
- 锁文件由 `pip-compile` 生成：

  ```bash
  # 请在 Python 3.12 环境下执行，以匹配运行 / CI 目标
  pip install pip-tools
  pip-compile requirements.txt               # -> requirements.lock.txt
  pip-compile requirements/requirements-dev.txt          # -> requirements-dev.lock.txt
  ```

- **请勿手动编辑锁文件**；改依赖时修改 `requirements*.txt` 后重新生成锁文件，并将
  `requirements*.txt` 与对应锁文件一起提交。
- 生成锁文件时请使用 **Python 3.12**（现有锁文件头部可能记录其它 Python 版本，属历史产物，
  后续重新生成即可对齐）。
