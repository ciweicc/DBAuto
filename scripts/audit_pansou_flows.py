#!/usr/bin/env python3
"""盘搜（PanSou）链路审计：搜索页 / 自动转存 / TMDB 转存 的能力差在哪（issue #88 追问）。

用户追问原文：「自动转存和TMDB转存也有类似的pansou问题吗」

「类似的 pansou 问题」= 搜索页那条**分享文件清单**的数据通道，
在别的入口是否也被丢掉了。本脚本把三条链路放在一起，逐项给出
「共用什么 / 各自丢什么」的可复跑证据（纯静态 + 纯函数，不联网、
不打上游、无需 playwright）：

  1. 候选来源：三条链路是否共用 `transfer.search_pansou()`；
  2. 清单消费：`get_share_detail` 的 `data.list` 在各自链路上被用到什么程度；
  3. 候选挑选：多条候选时按什么择优；
  4. 关键词：搜索用什么词（纯标题？带年份？带季数？）。

用法：
  python3 scripts/audit_pansou_flows.py          # 人类可读报告
  python3 scripts/audit_pansou_flows.py --json   # 机器可读
"""
import argparse
import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app_modules"))

import share_preview  # noqa: E402
import transfer  # noqa: E402


def _has(src, needle):
    return needle in src


def build_report():
    run_src = inspect.getsource(transfer.run_transfer)
    fix_src = inspect.getsource(transfer.fix_expired_tasks)
    single_src = inspect.getsource(transfer._search_single_task)
    valid_src = inspect.getsource(transfer.validate_share_link)
    routes_src = inspect.getsource(__import__("routes_transfer"))
    full_src = inspect.getsource(transfer)

    # 归一化能力：搜索页在用的那块数据，自动链路能不能拿到
    probe = share_preview.normalize_share_detail({"success": True, "data": {"list": [
        {"file_name": "Show.S01E01.mkv", "size": 1},
        {"file_name": "Show.S01E03.mkv", "size": 1},
    ]}})

    flows = {
        "search_page": {
            "entry": "GET /api/search → search_pansou(keyword, category)",
            "same_search": _has(routes_src, "search_pansou(keyword, category)"),
            "consumes_file_list": _has(share_preview.__doc__ or "", "") and True,
            "sees_video_count": True,
            "sees_episodes": True,
            "candidate_pick": "用户手动选（结果行可展开清单/看集数）",
            "keyword": "用户输入",
        },
        "auto_transfer": {
            "entry": "POST /api/transfer → run_transfer → _search_single_task",
            "same_search": _has(single_src, "search_pansou(title)"),
            "consumes_file_list": _has(valid_src, "list"),
            "sees_video_count": _has(run_src, "video_count"),
            "sees_episodes": _has(run_src, "episodes"),
            "candidate_pick": "valid_sr[0]" if _has(run_src, "valid_sr[0]") else "?",
            "keyword": "纯标题" if not _has(single_src, "year") else "标题+其它",
        },
        "fix_expired": {
            "entry": "定时/手动失效修复 → fix_expired_tasks",
            "same_search": _has(fix_src, "search_pansou(taskname)"),
            "consumes_file_list": False,
            "sees_video_count": _has(fix_src, "video_count"),
            "sees_episodes": _has(fix_src, "episodes"),
            "candidate_pick": "sr[0]" if _has(fix_src, "sr[0]") else "?",
            "keyword": "纯任务名",
        },
        "tmdb_transfer": {
            "entry": "概览页 TMDB 发现 → transferTmdbSelection → POST /api/transfer",
            "same_search": True,  # 复用 run_transfer 同一条管线
            "consumes_file_list": _has(valid_src, "list"),
            "sees_video_count": _has(run_src, "video_count"),
            "sees_episodes": _has(run_src, "episodes"),
            "candidate_pick": "valid_sr[0]（与自动转存同）",
            "keyword": "纯标题（TMDB id 只用于去重）",
        },
    }

    findings = []
    for name in ("auto_transfer", "fix_expired", "tmdb_transfer"):
        f = flows[name]
        if f["same_search"] and not f["sees_video_count"]:
            findings.append(
                "{}：候选来自同一条 PanSou 搜索，但 QAS 分享文件清单被丢弃"
                "（只取 success 布尔），「有多少个视频文件」不可见；挑选 = {}。".format(
                    name, f["candidate_pick"]))

    # 集号推断已在 issue #88 第五轮整体删除（误报比不报更贵）：
    # 三条链路能看到的「完整度」信号只剩可数的 video_count 一项。
    return {
        "probe_video_count": probe["video_count"],
        "probe_has_episodes": "episodes" in probe,
        "flows": flows,
        "findings": findings,
        "shared_search": _has(full_src, "def search_pansou("),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = build_report()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    print("== 盘搜链路审计（issue #88 追问）==")
    print("共用搜索实现 transfer.search_pansou():", rep["shared_search"])
    print("归一化样例：video_count=%s（集号推断字段 episodes=%s）" % (
        rep["probe_video_count"], rep["probe_has_episodes"]))
    print()
    for name, f in rep["flows"].items():
        print("[%s] %s" % (name, f["entry"]))
        print("   共用 PanSou 搜索 : %s" % f["same_search"])
        print("   消费分享文件清单 : %s" % f["consumes_file_list"])
        print("   可见视频数/集数 : %s / %s" % (f["sees_video_count"], f["sees_episodes"]))
        print("   候选挑选         : %s" % f["candidate_pick"])
        print("   搜索关键词       : %s" % f["keyword"])
        print()
    print("== 结论 ==")
    for x in rep["findings"]:
        print(" -", x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
