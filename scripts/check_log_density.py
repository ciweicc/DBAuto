#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""issue #68 证据脚本：量化「一次转存往面板灌了多少行」。

为什么不用截图：面板的可读性问题是**行数**（重复 + 无用），
这能直接用 list(log_progress) 数出来，不需要读图，也不会吃掉上下文预算。
（单个浏览器截图约 400–980KB，AGENTS.md 里已经因此报废过两次运行。）

跑法：
    python3 scripts/check_log_density.py

退出码：0 = 每个标题在面板上恰好一条终态行；非 0 = 出现回退。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app_modules"))

from utils import log, log_progress, clear_progress, logger  # noqa: E402

# 只关心面板：把文件日志静音，避免脚本输出被 logger 的 INFO 行淹掉
logging_handlers = logger.handlers[:]
for h in logging_handlers:
    logger.removeHandler(h)

N = 20
TAG = "TRANSFER#ab12cd"


def _run(mode):
    clear_progress()
    log("共获取 {} 条".format(N))
    log("待搜索任务: {} 条，并发数: 4".format(N), **({} if mode == "before" else {"panel": False}))
    log("搜索完成，开始转存...", tag=TAG)
    for i in range(N):
        title = "示例电影{}".format(i)
        sub = {} if mode == "before" else {"panel": False}
        log("搜索: {}".format(title), tag=TAG, **sub)
        log("找到: [电影] {}".format(title), tag=TAG, **sub)
        log("链接校验异常(不可信,保守保留): https://x/{} (timeout)".format(i), tag=TAG, **sub)
        if mode == "before":
            log("  {}".format("转存成功"), tag=TAG)
        else:
            log("{}：{}".format(title, "转存成功"), tag=TAG)
    log("转存完成: {} 条".format(N), tag=TAG)
    return list(log_progress)


def main():
    before = _run("before")
    after = _run("after")
    print("面板行数 before = {}  (每标题 {} 行)".format(len(before), len(before) / N))
    print("面板行数 after  = {}  (每标题 {} 行)".format(len(after), len(after) / N))
    print("降幅 = {:.0f}%".format((1 - len(after) / len(before)) * 100))
    print("\n--- after 面板前 8 行 ---")
    for line in after[:8]:
        print(line)

    failures = []
    # 1) 每标题只留一条终态行
    for i in range(N):
        title = "示例电影{}".format(i)
        # 用「标题：」定界，否则「示例电影1」会误匹配到「示例电影10..19」
        hits = [ln for ln in after if "{}：".format(title) in ln]
        if len(hits) != 1:
            failures.append("{} 在面板上出现 {} 行（期望 1）".format(title, len(hits)))
    # 2) 面板行必须比改造前少一半以上
    if len(after) * 2 > len(before):
        failures.append("面板行数未显著下降：{} → {}".format(len(before), len(after)))
    # 3) 内部实现细节不得上面板
    for noise in ("并发数",):
        if any(noise in ln for ln in after):
            failures.append("内部噪声仍上面板：{}".format(noise))

    if failures:
        print("\n❌ 失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("\n✅ 通过：每个标题在面板上恰好一条终态行，且无内部实现噪声")
    return 0


if __name__ == "__main__":
    sys.exit(main())
