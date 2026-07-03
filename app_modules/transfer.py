# transfer.py — 转存执行、PanSou 搜索、QAS 交互、失效检测、目录清理
import json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from threading import Lock, get_ident, enumerate as enumerate_threads, local
from config import ConfigManager, load_settings
from utils import http_get, http_post, log, TTLCache, clear_progress, sse_broadcast
from storage import load_history, save_history, add_exec_record, update_exec_record
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

_qas_thread_local = local()
_qas_client_lock = Lock()

def _get_qas_client():
    cfg = ConfigManager.get_instance()
    if not hasattr(_qas_thread_local, "client"):
        from api_client import QASClient
        _qas_thread_local.client = QASClient(cfg.qas, cfg.qas_token, timeout=20)
        with _qas_client_lock:
            log("QAS Client 创建，token 长度: {}".format(len(cfg.qas_token or "")))
    return _qas_thread_local.client

def reset_qas_client():
    if hasattr(_qas_thread_local, "client"):
        delattr(_qas_thread_local, "client")
    init_qas_cache()

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
            results = data.get("data", {}).get("merged_by_type", {}).get("quark", [])
            if not isinstance(results, list):
                results = data.get("results", [])
            formatted_results = []
            for item in results:
                title = item.get("Title", item.get("title", ""))
                links = item.get("Links", item.get("links", []))
                url = ""
                if isinstance(links, list) and len(links) > 0:
                    url = links[0].get("URL", links[0].get("url", ""))
                elif isinstance(links, dict):
                    url = links.get("URL", links.get("url", ""))
                else:
                    url = item.get("URL", item.get("url", ""))
                if title and url:
                    formatted_results.append({
                        "title": title,
                        "url": url,
                        "source": item.get("Source", item.get("source", "夸克网盘"))
                    })
            _pansou_cache.set("{}:{}".format(category, keyword), formatted_results)
            return formatted_results
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
        # 获取失效检测的目录配置
        settings = load_settings()
        expired_dirs = settings.get("expired_check", {}).get("directories", [])
        # 过滤夸克链接
        to_check = [t for t in tasks if t.get("shareurl", "") and "quark.cn" in t.get("shareurl", "")]
        # 如果配置了目录，只检测指定目录范围内的任务
        if expired_dirs:
            to_check = [t for t in to_check if t.get("savepath", "") and any(d in t.get("savepath", "") for d in expired_dirs)]
            log("失效检测目录范围: {}".format(expired_dirs))
        if limit:
            to_check = to_check[:limit]
        if not to_check:
            log("失效检测: 无符合条件的任务")
            return []
        log("检测失效链接: {} 个，并发数: {}".format(len(to_check), EXPIRED_CHECK_CONCURRENCY))
        expired = []
        with ThreadPoolExecutor(max_workers=EXPIRED_CHECK_CONCURRENCY) as executor:
            future_map = {executor.submit(_check_single_expired, t): t for t in to_check}
            for future in as_completed(future_map):
                # 检查停止标志
                if transfer_status.get("stop"):
                    for f in future_map:
                        f.cancel()
                    log("检测已被用户终止")
                    break
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

def fix_expired_tasks():
    global transfer_status
    tid = get_ident()
    with transfer_lock:
        transfer_status.update({
            "running": True,
            "thread_id": tid,
            "summary": "fix_expired"
        })
    try:
        expired = check_expired_tasks()
        if not expired:
            log("没有失效链接，无需修复")
            return {"total": 0, "fixed": 0, "failed": 0, "results": []}
        
        log("开始修复 {} 个失效链接".format(len(expired)))
        fixed = 0
        failed = 0
        results = []
        
        for task in expired:
            # 检查停止标志
            if transfer_status.get("stop"):
                log("修复已被用户终止")
                break
            taskname = task.get("taskname", "")
            log("搜索替换: {}".format(taskname))
            
            try:
                sr = search_pansou(taskname)
                if not sr:
                    log("  未找到替代资源")
                    failed += 1
                    results.append({"taskname": taskname, "status": "not_found", "msg": "未找到替代资源"})
                    continue
                
                chosen = sr[0]
                new_url = chosen.get("url", "")
                if not new_url:
                    log("  资源无有效链接")
                    failed += 1
                    results.append({"taskname": taskname, "status": "no_url", "msg": "资源无有效链接"})
                    continue
                
                valid, msg = validate_share_link(new_url)
                if not valid:
                    log("  新链接无效: {}".format(msg))
                    failed += 1
                    results.append({"taskname": taskname, "status": "invalid", "msg": msg})
                    continue
                
                success = update_expired_task(task, new_url)
                if success:
                    log("  ✅ 替换成功: {}".format(chosen.get("note", "")))
                    fixed += 1
                    results.append({"taskname": taskname, "status": "fixed", "msg": chosen.get("note", "")})
                else:
                    log("  ❌ 更新失败")
                    failed += 1
                    results.append({"taskname": taskname, "status": "update_fail", "msg": "更新失败"})
                
                time.sleep(2)
            except Exception as e:
                log("  ❌ 异常: {}".format(e))
                failed += 1
                results.append({"taskname": taskname, "status": "error", "msg": str(e)})
        
        log("修复完成: 成功 {} / 失败 {}".format(fixed, failed))
        return {"total": len(expired), "fixed": fixed, "failed": failed, "results": results}
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["thread_id"] = None

def _clean_title(title):
    return re.sub(r'[^\u4e00-\u9fff0-9a-zA-Z]', '', title).lower()

def _build_history_index(history, qas_cache=None):
    index = {
        "exact": set(history.keys()),
        "clean": set()
    }
    for k in history:
        index["clean"].add(_clean_title(k))
    index["items"] = [(k, _clean_title(k)) for k in history]
    if qas_cache:
        index["qas_clean"] = set()
        for name in qas_cache:
            index["qas_clean"].add(_clean_title(name))
        index["qas_items"] = [(name, _clean_title(name)) for name in qas_cache]
    else:
        index["qas_clean"] = set()
        index["qas_items"] = []
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
            if title_clean == k_clean or (len(title_clean) >= 3 and title_clean in k_clean) or (len(k_clean) >= 3 and k_clean in title_clean):
                return True
        for name, name_clean in index["qas_items"]:
            if title_clean == name_clean or (len(title_clean) >= 3 and title_clean in name_clean) or (len(name_clean) >= 3 and name_clean in title_clean):
                return True
        return False
    title_clean = _clean_title(title)
    for k in history:
        k_clean = _clean_title(k)
        if title_clean == k_clean or (len(title_clean) >= 3 and title_clean in k_clean) or (len(k_clean) >= 3 and k_clean in title_clean):
            return True
    return False

def build_transfer_tasks(tasks_config, filters=None):
    filters = filters or {}
    all_t = []
    for tk in tasks_config:
        try:
            items = get_douban_list(
                tk["path"], tk["type"], 20,
                min_rating=filters.get("min_rating", 0),
                sort_by=filters.get("sort_by", "rating"),
                year_from=filters.get("year_from", 0),
                year_to=filters.get("year_to", 0)
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
    exec_record_id = None
    try:
        rec = add_exec_record("transfer", "开始转存 ({} 条)".format(len(task_list)), "running")
        exec_record_id = rec["id"]
    except Exception:
        pass
    with transfer_lock:
        transfer_status.update({"running": True, "summary": None,
                                "start_time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                                "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)},
                                "thread_id": tid, "stop": False})
    clear_progress()
    log("开始转存，上限{}".format(limit))
    history = load_history()
    with _qas_cache_lock:
        qas_cache_data = list(_qas_cache) if _qas_cache else []
    history_index = _build_history_index(history, qas_cache_data)
    transferred = 0
    results = []
    error_msg = None

    try:
        pending_tasks = []
        for task in task_list:
            title = task["title"]
            if _find_in_history(title, history, history_index):
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
            history[title] = {"date": datetime.now(TZ).strftime("%Y-%m-%d"),
                              "status": res["status"], "category": category}
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
                update_exec_record(exec_record_id, detail=detail, status=final_status, data={"results": results})
            except Exception:
                pass