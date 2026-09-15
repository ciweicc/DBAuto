---
name: dbauto-local-verify
description: DBAuto 本地验证流程（廉价证据优先、截图配额的 UI 走查、构建产物重建）。改动静态页/前端/后端后需要"证明改对了"时使用。
---

# DBAuto 本地验证

目标：**用最少的上下文和最少的时间，拿到可复现、可复核的验证证据。**

## 0. 铁律（违反会导致本回合直接失败）

| 禁止 | 原因 | 替代做法 |
|---|---|---|
| `curl -N .../api/sse`、任何流式请求 | 永不返回，挂死回合 | 读源码 / 单元测试 |
| 前台起服务（无 `nohup`/重定向） | 命令永不返回 + 日志回灌上下文 | `nohup ... >/tmp/x.log 2>&1 &` |
| 无参数 `git status` / `git diff` | 产物巨大，刷爆上下文 | `git status --porcelain`、`--stat`、限定路径 |
| `cat static/index_new.html` | 约 220KB | `grep -n` 定位 |
| 大量读图（截图） | 每张 400–980KB，累计触发 `request entity too large` | 先用断言脚本，只对异常项读图 |
| 用 `static/src/build.py` 重建产物 | 与 `build.sh` 输出不一致，必然造成漂移 | `bash static/src/build.sh` |

**读图配额：单次任务 ≤ 8 张，单轮 ≤ 2 张。**

## 1. 通用校验（改动后按范围选择）

```bash
python -m pytest tests/ -q                       # 全量单元测试
python -m pytest tests/test_build.py -q          # 前端产物结构 / 防复发断言
ruff check .                                     # 静态检查
python scripts/check_contrast.py                 # 改了 tokens.css 的文本/语义色
bash static/src/build.sh                         # 改了 static/src/**（必须重建并提交产物）
```

产物是否漂移（等价于 CI 门禁）：

```bash
bash static/src/build.sh && git status --porcelain -- static/index_new.html
# 无输出 = 一致；有输出说明只改了 src 没重建，或手改了产物
```

## 2. 前端断言优于截图

绝大多数"视觉问题"其实可以用文本指标判定，先写断言脚本，别急着截图。
推荐流程（在 `/tmp` 下建脚本，勿提交临时文件）：

1. 起服务：
   ```bash
   nohup python main.py >/tmp/dbauto.log 2>&1 & echo $! >/tmp/dbauto.pid
   for i in $(seq 1 30); do curl -s -o /dev/null -m 2 http://127.0.0.1:8099/ && break; sleep 1; done
   ```
2. 用 Playwright 做**度量断言**（一次性输出全部指标，失败项才读图）：
   `page.goto` 打开 `static/index_new.html`（或通过服务），
   对目标元素取 `boundingBox()` / `getComputedStyle()`，
   断言宽度、可见性、`aria-*`、类名切换，形如
   `{"barTop":751,"barBottom":804,"lastCardBottom":1259}`。
   需要多视口/多主题时，在同一次脚本里遍历，**只打印结论行**。
3. 只有断言表达不了的（对齐、溢出、层叠、对比度观感）才截图，
   截图后立刻读图并**记入配额**，不要"先截 60 张再慢慢看"。

> `tests/*_test.py`（smoke/merge/phase*）是依赖 playwright + 实时服务的 E2E，
> 不在默认 `pytest` 收集范围内，可作断言脚本的起点参考。

## 3. 后端改动的验证

```bash
python -m pytest tests/test_<相关模块>.py -q
```

接口变更要同时给出「请求 → 响应」的确定性证据（可用 `pytest` + Flask test client），
不要只凭代码阅读下结论。

## 4. 收尾

- 停掉后台服务：`kill "$(cat /tmp/dbauto.pid)"`。
- 确认工作区没有临时文件：`git status --porcelain`。
- **把确定性的校验固化进 `tests/`**，把本 skill 里踩到的新坑补进本文件 —— 让下一次
  Agent 不再重复付出上下文代价。
