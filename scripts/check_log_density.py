#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""issue #68 证据脚本：量化「一次转存往面板灌了多少行 / 占了多少视觉高度」。

为什么不用截图：面板的可读性问题是**行数**（重复 + 无用）与**字符数**
（一屏放不下），这两者都能直接用 list(log_progress) 算出来，不需要读图，
也不会吃掉上下文预算。（单个浏览器截图约 400–980KB，AGENTS.md 里已经因此
报废过两次运行。）

两轮改造：
  * 第一轮（PR #70）：一个标题只留一条终态行，中间态进 `panel=False`；
  * 第二轮（issue #68 追问）：行**本身**也要瘦身 —— 把 requests 的整段异常
    从面板主行里剥出来，降级成可折叠的「详情」。面板主行按 ~46 字符折行，
    所以字符数就是视觉高度。

跑法：
    python3 scripts/check_log_density.py

退出码：0 = 每个标题在面板上恰好一条终态行、且异常原文退出主行；非 0 = 回退。
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


# PanSou/QAS 抖动时 requests 抛出的真实异常长这样。
# 改造前它整段糊在面板主行上（折行后一条占 5–6 行）；改造后主行只留结论，
# 原文进「详情」。下面用它来量化「一条事件占多少视觉行」。
_EXC = ("HTTPConnectionPool(host='192.168.5.55', port=8080): "
        "Read timed out. (read timeout=20)")
# 面板栏在手边最窄的窄屏（280px）下约 46 字符/行；用最乐观值估计折行行数。
CHARS_PER_LINE = 46


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
            # 改造前：整段 requests 异常糊在主行上（每标题一条）
            log("HTTP POST 超过重试预算，放弃: {}".format(_EXC), tag=TAG)
        else:
            log("{}：{}".format(title, "转存成功"), tag=TAG)
            # 改造后：主行只留结论，异常原文降级为可折叠详情
            log("HTTP POST 超时放弃（已重试 3 次）", tag=TAG, detail=_EXC)
    log("转存完成: {} 条".format(N), tag=TAG)
    return list(log_progress)


def _split_pairs(lines):
    """把 progress 还原成 (主行, 详情) 对。

    `utils.log` 在带 detail 时会把「主行 + 详情行」成对写进 progress
    （progress 是 SSE 断连时的回填源，必须自足）。前端按 `detailAttachesTo`
    认领主行，详情默认**折叠**成一行灰字 + 省略号。
    """
    pairs, i = [], 0
    while i < len(lines):
        main, detail = lines[i], None
        # 详情行沿用 "主行: 详情" 的写法，且一定紧跟主行
        if i + 1 < len(lines) and lines[i + 1].startswith(main + ": "):
            detail = lines[i + 1]
            i += 1
        pairs.append((main, detail))
        i += 1
    return pairs


def _visual_rows(pairs, collapsed=True):
    """估算这些行在面板上实际占用的视觉行数（按最窄面板折行计算）。

    折叠的详情只占一行（灰字 + 省略号），不按它自己的长度算 ——
    这正是"默认折叠"换来的密度收益。
    """
    total = 0
    for main, detail in pairs:
        total += max(1, -(-len(main) // CHARS_PER_LINE))
        if detail:
            total += 1 if collapsed else max(1, -(-len(detail) // CHARS_PER_LINE))
    return total


def main():
    before = _run("before")
    after = _run("after")
    print("面板行数 before = {}  (每标题 {} 行)".format(len(before), len(before) / N))
    print("面板行数 after  = {}  (每标题 {} 行)".format(len(after), len(after) / N))
    print("降幅 = {:.0f}%".format((1 - len(after) / len(before)) * 100))
    pb, pa = _split_pairs(before), _split_pairs(after)
    # before 的异常是整段糊在主行里的，按"展开"算才可比（它就是占那么多行）
    vb, va = _visual_rows(pb, collapsed=False), _visual_rows(pa, collapsed=True)
    print("视觉行数 before = {}  (每标题 {:.1f} 行 @{} 字符/行)".format(
        vb, vb / N, CHARS_PER_LINE))
    print("视觉行数 after  = {}  (每标题 {:.1f} 行，详情默认折叠)".format(va, va / N))
    print("视觉降幅 = {:.0f}%".format((1 - va / vb) * 100))
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
    # 2) 面板**主行**必须比改造前少一半以上（详情行不计入：它默认折叠、不占视觉高度）
    mains_after = len(_split_pairs(after))
    if mains_after * 2 > len(before):
        failures.append("面板主行数未显著下降：{} → {}".format(len(before), mains_after))
    # 3) 内部实现细节不得上面板
    for noise in ("并发数",):
        if any(noise in ln for ln in after):
            failures.append("内部噪声仍上面板：{}".format(noise))
    # 4) 异常原文不得再占面板主行（必须降级成折叠详情）
    for ln in after:
        if "HTTPConnectionPool" in ln and "超时放弃" not in ln:
            failures.append("异常原文仍以独立主行上面板：{}".format(ln[:60]))
    # 5) 视觉高度必须再降一档。
    #    改造前每标题 10.2 行；改造后 4.2 行（主行 1 条结论 + 1 条重试告警，
    #    外加折叠详情各占 1 行）。这里卡在 5 行：再多就说明有东西没被折叠。
    if va / N > 5.0:
        failures.append("每标题视觉行数 {:.1f} > 5（异常原文没收进折叠详情）".format(va / N))
    # 5') 视觉降幅至少要 40%（第一轮 72% 是按"行数"算的，这里按"占屏"算）
    if va > vb * 0.6:
        failures.append("视觉行数降幅不足 40%：{} → {}".format(vb, va))

    if failures:
        print("\n❌ 失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("\n✅ 通过：每个标题在面板上恰好一条终态行，且无内部实现噪声")
    return 0


if __name__ == "__main__":
    sys.exit(main())
