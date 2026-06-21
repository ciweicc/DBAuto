# transfer.py — 转存执行、PanSou 搜索、QAS 交互、失效检测、目录清理
import json, time, urllib.request, re
from threading import Lock
from config import PANSOU, QAS, QAS_TOKEN, OPENLIST_URL, OPENLIST_TOKEN
from utils import http_get, http_post, log
from storage import load_history, save_history
from douban import get_douban_list

VIDEO_SUB = r".*?\.(mp4|mkv|avi|ts|rmvb|flv|mov|srt|ass|ssa|sub|idx)"
TV_REPLACE = "{TASKNAME}.{SXX}E{E}.{EXT}"

transfer_status = {"running": False, "summary": None,
                   "start_time": None, "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0}}
transfer_lock = Lock()

_pansou_cache = {}
_pansou_lock = Lock()
_PANSOU_TTL = 600

_qas_cache = set()
_qas_cache_lock = Lock()

def init_qas_cache():
    for attempt in range(3):
        try:
            data = http_get("{}://data?token={}".format(QAS, QAS_TOKEN), timeout=15)
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

def search_pansou(keyword):
    now = time.time()
    with _pansou_lock:
        if keyword in _pansou_cache:
            ct, cd = _pansou_cache[keyword]
            if now - ct < _PANSOU_TTL:
                return cd
    for attempt in range(2):
        try:
            data = http_post("{}/api/search".format(PANSOU), {"kw": keyword, "cloud_types": ["quark"], "res": "merge"}, timeout=20)
            result = data.get("data", {}).get("merged_by_type", {}).get("quark", [])
            with _pansou_lock:
                _pansou_cache[keyword] = (now, result)
            return result
        except Exception as e:
            if attempt == 0:
                log("PanSou 重试: {}".format(e))
                time.sleep(2)
            else:
                log("PanSou 错误: {}".format(e))
                return []

def check_pansou_links(urls):
    """通过 PanSou 链接检测 API 验证链接有效性，返回有效 URL 集合"""
    if not urls:
        return set()
    items = [{"disk_type": "quark", "url": u} for u in urls if u]
    if not items:
        return set()
    try:
        data = http_post("{}/api/check/links".format(PANSOU), {"items": items}, timeout=30)
        valid = set()
        for r in data.get("results", []):
            if r.get("state") == "ok":
                valid.add(r.get("url", ""))
        return valid
    except Exception as e:
        log("PanSou 链接检查错误: {}".format(e))
        return set(urls)  # 检查失败时信任所有链接

def validate_share_link(url):
    try:
        payload = json.dumps({"shareurl": url}).encode()
        req = urllib.request.Request("{}/get_share_detail?token={}".format(QAS, QAS_TOKEN), data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            r = json.loads(resp.read().decode())
            return r.get("success", False), r.get("message", "")
    except Exception as e:
        return False, str(e)

def add_and_run(title, shareurl, savepath, pattern="", replace=""):
    payload = {"taskname": title, "shareurl": shareurl, "savepath": savepath}
    if pattern:
        payload["pattern"] = pattern
    if replace:
        payload["replace"] = replace
    add_res = http_post("{}/api/add_task?token={}".format(QAS, QAS_TOKEN), payload, timeout=20)
    if not add_res.get("success"):
        return {"status": "error", "msg": add_res.get("message", "fail")}
    add_to_qas(title)
    url = "{}/run_script_now?token={}".format(QAS, QAS_TOKEN)
    body = json.dumps({"tasklist": [payload]}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    lines = []
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode().strip()
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

def check_expired_tasks():
    try:
        data = http_get("{}/data?token={}".format(QAS, QAS_TOKEN), timeout=15).get("data", {})
        tasks = data.get("tasklist", [])
        expired = []
        for task in tasks:
            url = task.get("shareurl", "")
            if not url or "quark.cn" not in url:
                continue
            try:
                payload = json.dumps({"shareurl": url}).encode()
                req = urllib.request.Request("{}/get_share_detail?token={}".format(QAS, QAS_TOKEN), data=payload, method="POST")
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                    if not result.get("success"):
                        expired.append(task)
            except Exception:
                expired.append(task)
            time.sleep(0.5)
        return expired
    except Exception as e:
        log("检测失效出错: {}".format(e))
        return []

def update_expired_task(task, new_url):
    try:
        data = http_get("{}/data?token={}".format(QAS, QAS_TOKEN), timeout=15).get("data", {})
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
            result = http_post("{}/update?token={}".format(QAS, QAS_TOKEN), data, timeout=15)
            return result.get("success", False)
        return False
    except Exception as e:
        log("更新失效出错: {}".format(e))
        return False

def _find_in_history(title, history):
    if title in history:
        return True
    for k in history:
        if title in k or k in title:
            return True
    return False

def run_transfer(task_list, limit):
    global transfer_status
    from datetime import datetime, timezone, timedelta
    TZ = timezone(timedelta(hours=8))
    with transfer_lock:
        transfer_status.update({"running": True, "summary": None,
                                "start_time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                                "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)}})
    log("开始转存，上限{}".format(limit))
    history = load_history()
    transferred = 0
    results = []

    for task in task_list:
        if transferred >= limit:
            log("已达上限: {}".format(limit))
            break
        title, savepath = task["title"], task["savepath"]
        category = task.get("category", "movie")
        if _find_in_history(title, history):
            log("已跳过: {}".format(title))
            results.append({"title": title, "status": "skipped", "msg": "skip"})
            with transfer_lock:
                transfer_status["stats"]["skipped"] += 1
            continue

        log("搜索: {}".format(title))
        sr = search_pansou(title)
        with transfer_lock:
            transfer_status["stats"]["searched"] += 1

        if not sr:
            log("未找到: {}".format(title))
            results.append({"title": title, "status": "not_found", "msg": "not_found"})
            with transfer_lock:
                transfer_status["stats"]["failed"] += 1
            time.sleep(2)
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
        elif res["status"] == "exists":
            with transfer_lock:
                transfer_status["stats"]["skipped"] += 1
        else:
            with transfer_lock:
                transfer_status["stats"]["failed"] += 1
        time.sleep(3)

    save_history(history)
    with transfer_lock:
        transfer_status["running"] = False
        transfer_status["summary"] = {"transferred": transferred, "total": len(task_list), "results": results}
    log("转存完成: {} 条".format(transferred))
