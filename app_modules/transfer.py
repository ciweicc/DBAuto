# transfer.py — 转存执行、PanSou 搜索、QAS 交互、失效检测、目录清理
import json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from threading import Lock, get_ident, enumerate as enumerate_threads
from config import ConfigManager
from utils import http_get, http_post, log, TTLCache, clear_progress, sse_broadcast
from storage import load_history, save_history
from douban import get_douban_list

SEARCH_CONCURRENCY = 3

TZ = timezone(timedelta(hours=8))

VIDEO_SUB = r".*?\.(mp4|mkv|avi|ts|rmvb|flv|mov|srt|ass|ssa|sub|idx)"
TV_REPLACE = "{TASKNAME}.{SXX}E{E}.{EXT}"

transfer_status = {"running": False, "summary": None,
                   "start_time": None, "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0},
                   "thread_id": None}
transfer_lock = Lock()

_pansou_cache = TTLCache(ttl=600, max_size=200)

_qas_cache = set()
_qas_cache_lock = Lock()

def _get_pansou_client():
    cfg = ConfigManager.get_instance()
    from api_client import PanSouClient
    return PanSouClient(cfg.pansou, timeout=20)

def _get_qas_client():
    cfg = ConfigManager.get_instance()
    from api_client import QASClient
    log("QAS Client 创建，token 长度: {}".format(len(cfg.qas_token or "")))
    return QASClient(cfg.qas, cfg.qas_token, timeout=20)

def init_qas_cache():
    for attempt in range(3):
        try:
            client = _get_qas_client()
            data = client.get_data()
            tasks = data.get("data", {}).get("tasklist", [])
            with _qas_cache_lock:
                _qas_cache.clear()
                for t in tasks:
                    _qas_cache.add(t.get("taskname", ""))
            log("QAS: {} 个任务已缓存".format(len(_qas_cache)))
            return
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                log("QAS 初始化错误: {}".format(e))

def is_in_qas(name):
    with _qas_cache_lock:
        return name in _qas_cache

def add_to_qas(name):
    with _qas_cache_lock:
        _qas_cache.add(name)

def search_pansou(keyword, category="movie"):
    cached = _pansou_cache.get("{}:{}".format(category, keyword))
    if cached is not None:
        return cached
    for attempt in range(2):
        try:
            client = _get_pansou_client()
            data = client.search(keyword)
            result = data.get("data", {}).get("merged_by_type", {}).get("quark", [])
            _pansou_cache.set("{}:{}".format(category, keyword), result)
            return result
        except Exception as e:
            if attempt == 0:
                log("PanSou 重试: {}".format(e))
                time.sleep(2)
            else:
                log("PanSou 错误: {}".format(e))
                return []

def check_pansou_links(urls):
    if not urls:
        return set()
    try:
        client = _get_pansou_client()
        data = client.check_links(urls)
        valid = set()
        for r in data.get("results", []):
            if r.get("state") == "ok":
                valid.add(r.get("url", ""))
        return valid
    except Exception as e:
        log("PanSou 链接检查错误: {}".format(e))
        return set(urls)

def validate_share_link(url):
    try:
        client = _get_qas_client()
        r = client.get_share_detail(url)
        return r.get("success", False), r.get("message", "")
    except Exception as e:
        return False, str(e)

def add_and_run(title, shareurl, savepath, pattern="", replace=""):
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

EXPIRED_CHECK_CONCURRENCY = 5

def _check_single_expired(task):
    url = task.get("shareurl", "")
    try:
        client = _get_qas_client()
        result = client.get_share_detail(url)
        if not result.get("success"):
            return task, True
        return task, False
    except Exception as e:
        log("检测分享链接失败 {}: {}".format(url, e))
        return task, True

def check_expired_tasks(limit=None):
    try:
        client = _get_qas_client()
        data = client.get_data().get("data", {})
        tasks = data.get("tasklist", [])
        to_check = [t for t in tasks if t.get("shareurl", "") and "quark.cn" in t.get("shareurl", "")]
        if limit:
            to_check = to_check[:limit]
        if not to_check:
            return []
        log("检测失效链接: {} 个，并发数: {}".format(len(to_check), EXPIRED_CHECK_CONCURRENCY))
        expired = []
        with ThreadPoolExecutor(max_workers=EXPIRED_CHECK_CONCURRENCY) as executor:
            future_map = {executor.submit(_check_single_expired, t): t for t in to_check}
            for future in as_completed(future_map):
                try:
                    task, is_expired = future.result()
                    if is_expired:
                        expired.append(task)
                except Exception as e:
                    task = future_map[future]
                    log("检测任务异常 {}: {}".format(task.get("shareurl", ""), e))
                    expired.append(task)
        log("检测完成: {} 个失效".format(len(expired)))
        return expired
    except Exception as e:
        log("检测失效出错: {}".format(e))
        return []

def update_expired_task(task, new_url):
    try:
        client = _get_qas_client()
        data = client.get_data().get("data", {})
        tasks = data.get("tasklist", [])
        old_url = task.get("shareurl", "")
        updated = False
        for t in tasks:
            if t.get("shareurl") == old_url:
                t["shareurl"] = new_url
                updated = True
                break
        if updated:
            data["tasklist"] = tasks
            result = client.update(data)
            return result.get("success", False)
        return False
    except Exception as e:
        log("更新失效出错: {}".format(e))
        return False

def _clean_title(title):
    return re.sub(r'[^\u4e00-\u9fff0-9a-zA-Z]', '', title).lower()

def _build_history_index(history):
    index = {
        "exact": set(history.keys()),
        "clean": set()
    }
    for k in history:
        index["clean"].add(_clean_title(k))
    index["items"] = [(k, _clean_title(k)) for k in history]
    return index

def _find_in_history(title, history, index=None):
    if title in history:
        return True
    if index:
        if title in index["exact"]:
            return True
        title_clean = _clean_title(title)
        if title_clean in index["clean"]:
            return True
        for k, k_clean in index["items"]:
            if title_clean == k_clean or (len(title_clean) >= 4 and title_clean in k_clean) or (len(k_clean) >= 4 and k_clean in title_clean):
                return True
        return False
    title_clean = _clean_title(title)
    for k in history:
        k_clean = _clean_title(k)
        if title_clean == k_clean or (len(title_clean) >= 4 and title_clean in k_clean) or (len(k_clean) >= 4 and k_clean in title_clean):
            return True
    return False

def build_transfer_tasks(tasks_config):
    all_t = []
    for tk in tasks_config:
        try:
            items = get_douban_list(tk["path"], tk["type"], 20)
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
    global transfer_status
    tid = get_ident()
    with transfer_lock:
        transfer_status.update({"running": True, "summary": None,
                                "start_time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                                "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)},
                                "thread_id": tid, "stop": False})
    clear_progress()
    log("开始转存，上限{}".format(limit))
    history = load_history()
    history_index = _build_history_index(history)
    transferred = 0
    results = []
    error_msg = None

    try:
        pending_tasks = []
        for task in task_list:
            title = task["title"]
            if _find_in_history(title, history, history_index):
                log("已跳过: {}".format(title))
                results.append({"title": title, "status": "skipped", "msg": "skip"})
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
                results.append({"title": title, "status": "not_found", "msg": "not_found"})
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
            history[title] = {"date": datetime.now(TZ).strftime("%Y-%m-%d"),
                              "status": res["status"], "category": category}
            results.append({"title": title, "status": res["status"], "msg": res["msg"]})
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