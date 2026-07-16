# pipeline.py — 编排：任务构建 + 批量转存执行
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, get_ident, enumerate as enumerate_threads
from config import LOCAL_TZ
from utils import log, clear_progress, sse_broadcast
from storage import load_history, save_history, add_exec_record, update_exec_record
from douban import get_douban_list
from search import search_pansou
from dedup import (
    _get_qas_client, add_to_qas, get_qas_cache_snapshot,
    build_history_index, find_in_history
)
from verify import validate_share_link

SEARCH_CONCURRENCY = 3
VIDEO_SUB = r".*?\.(mp4|mkv|avi|ts|rmvb|flv|mov|srt|ass|ssa|sub|idx)"
TV_REPLACE = "{TASKNAME}.{SXX}E{E}.{EXT}"

# 全局状态（保留在 transfer.py 中，通过参数传入）
transfer_status = {
    "running": False, "summary": None,
    "start_time": None,
    "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0},
    "thread_id": None
}
transfer_lock = Lock()


def is_transfer_running():
    with transfer_lock:
        if not transfer_status.get("running"):
            return False
        tid = transfer_status.get("thread_id")
        if tid is None:
            return False
        for t in enumerate_threads():
            if t.ident == tid and t.is_alive():
                return True
        transfer_status["running"] = False
        transfer_status["thread_id"] = None
        transfer_status["stop"] = False
        log("检测到转存线程已结束，自动重置状态")
        return False


def add_and_run(title, shareurl, savepath, pattern="", replace=""):
    """添加 QAS 任务并立即执行"""
    client = _get_qas_client()
    add_res = client.add_task(title, shareurl, savepath, pattern, replace)
    if not add_res.get("success"):
        return {"status": "error", "msg": add_res.get("message", "fail")}
    add_to_qas(title)
    lines = []
    try:
        with client.run_script_now_stream([{"taskname": title, "shareurl": shareurl, "savepath": savepath}]) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=True):
                line = raw.strip() if raw else ""
                if line.startswith("data: "):
                    m = line[6:]
                    if m and m != "[DONE]":
                        lines.append(m)
    except Exception as e:
        return {"status": "error", "msg": str(e)}
    summary = "\n".join(lines)
    if "新的" in summary or "没有" in summary:
        return {"status": "exists", "msg": "已存在"}
    elif "成功" in summary or "更新" in summary:
        return {"status": "ok", "msg": "转存成功"}
    return {"status": "done", "msg": "转存成功"}


def transfer_one(title, shareurl, savepath, pattern="", replace="", category="movie"):
    """单条转存"""
    tid = get_ident()
    with transfer_lock:
        transfer_status.update({
            "running": True, "summary": None,
            "start_time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 1},
            "thread_id": tid, "stop": False
        })
    clear_progress()
    try:
        res = add_and_run(title, shareurl, savepath, pattern, replace)
        with transfer_lock:
            if res["status"] in ("ok", "done"):
                transfer_status["stats"]["ok"] += 1
            elif res["status"] == "exists":
                transfer_status["stats"]["skipped"] += 1
            else:
                transfer_status["stats"]["failed"] += 1
            sse_broadcast("transfer_progress", dict(transfer_status))
        history = load_history()
        history[title] = {
            "date": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"),
            "status": res["status"], "category": category
        }
        save_history(history)
        return res
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["stop"] = False
            transfer_status["thread_id"] = None


def build_transfer_tasks(tasks_config, filters=None):
    """从豆瓣榜单 + 想看列表构建转存任务列表"""
    filters = filters or {}
    all_t = []
    for tk in tasks_config:
        try:
            if tk.get("_wish") and tk.get("title"):
                all_t.append({"title": tk["title"], "savepath": tk["savepath"],
                              "category": tk.get("category", "movie")})
                continue
            items = get_douban_list(
                tk["path"], tk["type"], 20,
                min_rating=filters.get("min_rating", 0),
                sort_by=filters.get("sort_by", "rating"),
                year_from=filters.get("year_from", 0),
                year_to=filters.get("year_to", 0),
                exclude_keywords=filters.get("exclude_keywords", []),
                genre=filters.get("genre", "")
            )
            for i in items:
                all_t.append({"title": i["title"], "savepath": tk["savepath"],
                              "category": tk.get("category", "movie")})
        except Exception as e:
            log("获取错误: {}".format(e))
    seen = set()
    uniq = []
    for ti in all_t:
        if ti["title"] not in seen:
            seen.add(ti["title"])
            uniq.append(ti)
    log("共获取 {} 条".format(len(uniq)))
    return uniq


def _search_single_task(task):
    title = task["title"]
    try:
        log("搜索: {}".format(title))
        sr = search_pansou(title)
        return task, sr
    except Exception as e:
        log("搜索异常 {}: {}".format(title, e))
        return task, []


def run_transfer(task_list, limit):
    """批量转存：搜索 → 去重 → 转存 → 记录"""
    global transfer_status
    tid = get_ident()
    exec_record_id = None
    try:
        rec = add_exec_record("transfer", "开始转存 ({} 条)".format(len(task_list)), "running")
        exec_record_id = rec["id"]
    except Exception:
        pass
    with transfer_lock:
        transfer_status.update({
            "running": True, "summary": None,
            "start_time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)},
            "thread_id": tid, "stop": False
        })
    clear_progress()
    log("开始转存，上限{}".format(limit))
    history = load_history()
    qas_cache_data = get_qas_cache_snapshot()
    history_index = build_history_index(history, qas_cache_data)
    transferred = 0
    results = []
    error_msg = None

    try:
        pending_tasks = []
        for task in task_list:
            title = task["title"]
            if find_in_history(title, history, history_index):
                log("已跳过: {}".format(title))
                results.append({"title": title, "status": "skipped", "msg": "skip", "category": task.get("category", "")})
                with transfer_lock:
                    transfer_status["stats"]["skipped"] += 1
                    sse_broadcast("transfer_progress", dict(transfer_status))
                continue
            pending_tasks.append(task)

        log("待搜索任务: {} 条，并发数: {}".format(len(pending_tasks), SEARCH_CONCURRENCY))

        search_results = {}
        with ThreadPoolExecutor(max_workers=SEARCH_CONCURRENCY) as executor:
            future_map = {executor.submit(_search_single_task, t): t for t in pending_tasks}
            for future in as_completed(future_map):
                if transfer_status.get("stop"):
                    for f in future_map:
                        f.cancel()
                    log("任务已被用户终止，取消剩余搜索")
                    break
                try:
                    task, sr = future.result()
                    search_results[task["title"]] = sr
                    with transfer_lock:
                        transfer_status["stats"]["searched"] += 1
                        sse_broadcast("transfer_progress", dict(transfer_status))
                except Exception as e:
                    task = future_map[future]
                    log("搜索任务异常 {}: {}".format(task["title"], e))
                    search_results[task["title"]] = []
                    with transfer_lock:
                        transfer_status["stats"]["searched"] += 1
                        sse_broadcast("transfer_progress", dict(transfer_status))

        log("搜索完成，开始转存...")

        for task in pending_tasks:
            if transfer_status.get("stop"):
                log("任务已被用户终止")
                break
            if transferred >= limit:
                log("已达上限: {}".format(limit))
                break
            title, savepath = task["title"], task["savepath"]
            category = task.get("category", "movie")

            sr = search_results.get(title, [])
            if not sr:
                log("未找到: {}".format(title))
                results.append({"title": title, "status": "not_found", "msg": "not_found", "category": category})
                with transfer_lock:
                    transfer_status["stats"]["failed"] += 1
                    sse_broadcast("transfer_progress", dict(transfer_status))
                continue

            chosen = sr[0]
            log("找到: {}".format(chosen.get("note", title)))
            pattern = VIDEO_SUB
            replace = TV_REPLACE if category == "tv" else ""
            res = add_and_run(title, chosen.get("url", ""), "{}/{}".format(savepath, title), pattern, replace)
            log("  {}".format(res["msg"]))
            history[title] = {
                "date": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"),
                "status": res["status"], "category": category
            }
            results.append({"title": title, "status": res["status"], "msg": res["msg"], "category": category})
            if res["status"] in ("ok", "done"):
                transferred += 1
                with transfer_lock:
                    transfer_status["stats"]["ok"] += 1
                    sse_broadcast("transfer_progress", dict(transfer_status))
            elif res["status"] == "exists":
                with transfer_lock:
                    transfer_status["stats"]["skipped"] += 1
                    sse_broadcast("transfer_progress", dict(transfer_status))
            else:
                with transfer_lock:
                    transfer_status["stats"]["failed"] += 1
                    sse_broadcast("transfer_progress", dict(transfer_status))
            time.sleep(3)
    except Exception as e:
        error_msg = str(e)
        log("转存异常: {}".format(e))
        results.append({"title": "异常中断", "status": "error", "msg": error_msg})
    finally:
        save_history(history)
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["stop"] = False
            transfer_status["summary"] = {
                "transferred": transferred,
                "total": len(task_list),
                "results": results,
                "error": error_msg,
            }
        log("转存完成: {} 条".format(transferred))
        if exec_record_id:
            ok_count = sum(1 for r in results if r.get("status") in ("ok", "done"))
            fail_count = sum(1 for r in results if r.get("status") not in ("ok", "done", "skipped", "exists"))
            skip_count = sum(1 for r in results if r.get("status") in ("skipped", "exists"))
            final_status = "fail" if error_msg or fail_count > 0 else "ok"
            detail = "转存完成 成功{} 失败{} 跳过{}".format(ok_count, fail_count, skip_count)
            try:
                update_exec_record(exec_record_id, detail=detail, status=final_status,
                                   data={"results": results, "ok": ok_count, "failed": fail_count, "skipped": skip_count})
            except Exception:
                pass
