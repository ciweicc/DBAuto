# transfer.py — 转存执行、PanSou 搜索、QAS 交互、失效检测、目录清理
import time, re, uuid, copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock, get_ident, enumerate as enumerate_threads, local, Thread
from config import ConfigManager, load_settings, LOCAL_TZ
from utils import http_get, http_post, log, TTLCache, clear_progress, sse_broadcast
from storage import load_history, add_exec_record, update_exec_record, upsert_history_item
from douban import get_douban_list
from tmdb import search_tmdb_id

SEARCH_CONCURRENCY = 3
# 转存阶段并发数（受 QAS 速率限制约束，保守取 2）。
# 调大可提升大批量转存吞吐，但可能触发 QAS 限流，请按需评估。
TRANSFER_CONCURRENCY = 2

VIDEO_SUB = r".*?\.(mp4|mkv|avi|ts|rmvb|flv|mov|srt|ass|ssa|sub|idx)"
TV_REPLACE = "{TASKNAME}.{SXX}E{E}.{EXT}"

transfer_status = {"running": False, "summary": None,
                   "start_time": None, "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0},
                   "thread_id": None}
transfer_lock = Lock()

_pansou_cache = TTLCache(ttl=600, max_size=200)

_qas_cache = set()
_qas_cache_lock = Lock()

# TMDB id 解析缓存：标题规范化 + category → 整型 id；失败/未配置 key 记为 None（降级到标题去重）
_tmdb_id_cache = {}
_tmdb_id_cache_lock = Lock()

def _resolve_tmdb_id(title, category):
    """解析标题对应的 TMDB 作品 id（带进程内缓存与失败降级），返回 int 或 None。"""
    if not title:
        return None
    key = (_clean_title(title), category)
    with _tmdb_id_cache_lock:
        if key in _tmdb_id_cache:
            return _tmdb_id_cache[key]
    tid = None
    try:
        mt = "tv" if category == "tv" else "movie"
        y, _ = _extract_meta(title)
        tid = search_tmdb_id(title, mt, int(y) if y else 0)
    except Exception:
        tid = None
    with _tmdb_id_cache_lock:
        _tmdb_id_cache[key] = tid
    return tid

# ---------------------------------------------------------------------------
# P1 任务状态子系统：以 task_id 隔离状态，避免全局单状态互相覆盖 / SSE 串台
# ---------------------------------------------------------------------------
# 任务注册表：最近任务（进行中 + 刚结束），用于可观测性。
# 进行中的任务直接引用 transfer_status 同一对象（实时更新）；结束替换为不可变快照。
transfer_tasks = {}
_tasks_lock = Lock()
_MAX_TASKS = 20

# 调度器排队：当前有任务运行时，定时转存入队，结束后续跑（替代"硬跳过"）
_scheduled_queue = []
_scheduled_queue_lock = Lock()


def _is_running_locked():
    """持 transfer_lock 时调用：当前是否有活跃转存任务；若线程已死则自动重置。"""
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


def is_transfer_running(run_type=None):
    """是否正在运行。run_type 给定时仅统计该类型（如 'transfer'）。"""
    with transfer_lock:
        if not _is_running_locked():
            return False
        if run_type and transfer_status.get("run_type") != run_type:
            return False
        return True


def _register_running_task(task_id):
    """把当前 transfer_status（已写入 task_id/run_type）登记为进行中任务。"""
    with _tasks_lock:
        transfer_tasks[task_id] = transfer_status  # 进行中：引用同一对象，便于实时观测
        _prune_finished_locked()


def _snapshot_finish(task_id):
    """任务结束：把进行中引用替换为不可变快照（深拷贝，避免 stats 等嵌套字段被后续任务污染）。"""
    with _tasks_lock:
        if transfer_tasks.get(task_id) is not None:
            snap = copy.deepcopy(transfer_status)
            snap["running"] = False
            transfer_tasks[task_id] = snap
        _prune_finished_locked()


def _prune_finished_locked():
    if len(transfer_tasks) <= _MAX_TASKS:
        return
    finished = [t for t in transfer_tasks.values() if not t.get("running")]
    finished.sort(key=lambda t: t.get("start_time", ""))
    for t in finished[: len(transfer_tasks) - _MAX_TASKS]:
        transfer_tasks.pop(t.get("task_id"), None)


def get_recent_tasks(limit=20):
    """返回最近任务列表（快照），按开始时间倒序。"""
    with _tasks_lock:
        items = [dict(t) for t in transfer_tasks.values()]
    items.sort(key=lambda t: t.get("start_time", ""), reverse=True)
    return items[:limit]


def enqueue_scheduled_transfer(uniq, limit):
    """调度触发的转存：忙则入队，空闲则直接启动。返回 'started' 或 'queued'。"""
    with transfer_lock:
        if not _is_running_locked():
            Thread(target=run_transfer, args=(uniq, limit), daemon=True).start()
            return "started"
    with _scheduled_queue_lock:
        _scheduled_queue.append((uniq, limit))
    return "queued"


def _drain_pending_scheduled():
    """当前任务结束时调用：若队列中有排队的定时转存则启动下一个。"""
    with _scheduled_queue_lock:
        if not _scheduled_queue:
            return
        uniq, limit = _scheduled_queue.pop(0)
    if is_transfer_running():
        # 仍有任务在跑（如手动转存），重新入队，等其结束再 drain
        with _scheduled_queue_lock:
            _scheduled_queue.insert(0, (uniq, limit))
        return
    Thread(target=run_transfer, args=(uniq, limit), daemon=True).start()


def _get_pansou_client():
    cfg = ConfigManager.get_instance()
    from api_client import PanSouClient
    return PanSouClient(cfg.pansou, timeout=20)

_qas_thread_local = local()
_qas_client_lock = Lock()
_qas_client_version = 0

def _get_qas_client():
    cfg = ConfigManager.get_instance()
    # 版本号变化时重新创建客户端（配置更新后所有线程都会重建）
    cached_version = getattr(_qas_thread_local, "version", -1)
    if cached_version != _qas_client_version or not hasattr(_qas_thread_local, "client"):
        from api_client import QASClient
        _qas_thread_local.client = QASClient(cfg.qas, cfg.qas_token, timeout=20)
        _qas_thread_local.version = _qas_client_version
        with _qas_client_lock:
            log("QAS Client 创建，token 长度: {}".format(len(cfg.qas_token or "")))
    return _qas_thread_local.client

def reset_qas_client():
    global _qas_client_version
    with _qas_client_lock:
        _qas_client_version += 1
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
    client = _get_pansou_client()
    last_err = None
    for attempt in range(2):
        try:
            data = client.search(keyword)
            results = data.get("data", {}).get("merged_by_type", {}).get("quark", [])
            if not isinstance(results, list):
                results = data.get("results", [])
            formatted_results = []
            for item in results:
                title = item.get("note", item.get("Title", item.get("title", "")))
                url = item.get("url", item.get("URL", ""))
                if isinstance(url, list) and len(url) > 0:
                    url = url[0].get("url", url[0].get("URL", ""))
                elif isinstance(url, dict):
                    url = url.get("url", url.get("URL", ""))
                if title and url:
                    formatted_results.append({
                        "title": title,
                        "url": url,
                        "source": item.get("source", item.get("Source", "夸克网盘"))
                    })
            _pansou_cache.set("{}:{}".format(category, keyword), formatted_results)
            return formatted_results
        except Exception as e:
            last_err = e
            if attempt == 0:
                log("PanSou 重试: {}".format(e))
                time.sleep(1.5)
            else:
                log("PanSou 错误: {}".format(e))
    # 不再静默 return []，抛出让调用方把真实原因透传到执行历史
    raise RuntimeError("PanSou 搜索失败 ({}): {}".format(keyword, last_err)) from last_err

def validate_share_link(url):
    try:
        client = _get_qas_client()
        r = client.get_share_detail(url)
        return r.get("success", False), r.get("message", "")
    except Exception as e:
        return False, str(e)

def add_and_run(title, shareurl, savepath, pattern="", replace=""):
    # 前置校验：失效链接直接返回，避免在网盘创建空目录
    try:
        ok, msg = validate_share_link(shareurl)
        if not ok:
            log("链接无效，跳过转存: {} ({})".format(shareurl, msg))
            return {"status": "invalid", "msg": msg or "链接已失效"}
    except Exception as e:
        log("链接校验异常(放行): {}".format(e))
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
    """单条转存，正确管理 transfer_status 状态"""
    tid = get_ident()
    task_id = uuid.uuid4().hex
    with transfer_lock:
        transfer_status.update({
            "running": True,
            "summary": None,
            "start_time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 1},
            "thread_id": tid,
            "stop": False,
            "task_id": task_id,
            "run_type": "transfer_one"
        })
        clear_progress()
    _register_running_task(task_id)
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
        # 更新历史记录（单条增量写入，不再全量 load+save），并写入 TMDB id 以便后续按作品去重
        tmdb_id = _resolve_tmdb_id(title, category)
        upsert_history_item(title, {"date": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"),
                                    "status": res["status"], "category": category,
                                    "tmdb_id": tmdb_id})
        return res
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["stop"] = False
            transfer_status["thread_id"] = None
            sse_broadcast("transfer_progress", dict(transfer_status))
        _snapshot_finish(task_id)

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
    task_id = uuid.uuid4().hex
    with transfer_lock:
        transfer_status.update({
            "running": True,
            "thread_id": tid,
            "summary": "fix_expired",
            "start_time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0},
            "stop": False,
            "task_id": task_id,
            "run_type": "fix_expired"
        })
        clear_progress()
    _register_running_task(task_id)
    expired = []
    fixed = 0
    failed = 0
    results = []
    try:
        expired = check_expired_tasks()
        if not expired:
            log("没有失效链接，无需修复")
            return {"total": 0, "fixed": 0, "failed": 0, "results": []}
        
        log("开始修复 {} 个失效链接".format(len(expired)))
        with transfer_lock:
            transfer_status["stats"]["total"] = len(expired)
        
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
                    with transfer_lock:
                        transfer_status["stats"]["failed"] = failed
                        sse_broadcast("transfer_progress", dict(transfer_status))
                    continue
                
                chosen = sr[0]
                new_url = chosen.get("url", "")
                if not new_url:
                    log("  资源无有效链接")
                    failed += 1
                    results.append({"taskname": taskname, "status": "no_url", "msg": "资源无有效链接"})
                    with transfer_lock:
                        transfer_status["stats"]["failed"] = failed
                        sse_broadcast("transfer_progress", dict(transfer_status))
                    continue
                
                valid, msg = validate_share_link(new_url)
                if not valid:
                    log("  新链接无效: {}".format(msg))
                    failed += 1
                    results.append({"taskname": taskname, "status": "invalid", "msg": msg})
                    with transfer_lock:
                        transfer_status["stats"]["failed"] = failed
                        sse_broadcast("transfer_progress", dict(transfer_status))
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
                with transfer_lock:
                    transfer_status["stats"]["ok"] = fixed
                    transfer_status["stats"]["failed"] = failed
                    sse_broadcast("transfer_progress", dict(transfer_status))
                
                time.sleep(2)
            except Exception as e:
                log("  ❌ 异常: {}".format(e))
                failed += 1
                results.append({"taskname": taskname, "status": "error", "msg": str(e)})
                with transfer_lock:
                    transfer_status["stats"]["failed"] = failed
                    sse_broadcast("transfer_progress", dict(transfer_status))
        
        log("修复完成: 成功 {} / 失败 {}".format(fixed, failed))
        return {"total": len(expired), "fixed": fixed, "failed": failed, "results": results}
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["thread_id"] = None
            transfer_status["stop"] = False
            transfer_status["stats"]["total"] = len(expired)
            transfer_status["stats"]["ok"] = fixed
            transfer_status["stats"]["failed"] = failed
            sse_broadcast("transfer_progress", dict(transfer_status))
        _snapshot_finish(task_id)
        _drain_pending_scheduled()

def _clean_title(title):
    return re.sub(r'[^\u4e00-\u9fff0-9a-zA-Z]', '', title).lower()

# 归一化辅助：剥离年份 / 季 / 分辨率，得到"核心标题"
_YEAR_PAT = re.compile(r'(?:19|20)\d{2}')
_SEASON_PAT = re.compile(r'(?:s\d+|season\s*\d+|第\s*[0-9零一二三四五六七八九十]+\s*季)', re.I)
_RES_PAT = re.compile(r'(?:1080|720|2160)p|\b4k\b', re.I)


def _core_title(title):
    s = _clean_title(title)
    s = _YEAR_PAT.sub('', s)
    s = _SEASON_PAT.sub('', s)
    s = _RES_PAT.sub('', s)
    return s


def _extract_meta(title):
    """从标题中提取年份与季（用于区分同名不同年/不同季的作品）"""
    s = _clean_title(title)
    ym = _YEAR_PAT.search(s)
    year = ym.group(0) if ym else None
    sm = _SEASON_PAT.search(s)
    season = re.sub(r'\s+', '', sm.group(0)).lower() if sm else None
    return year, season


def _build_history_index(history, qas_cache=None):
    index = {
        "exact": set(history.keys()),
        "clean": set()
    }
    items = []
    for k in history:
        ck = _clean_title(k)
        items.append((k, ck, _core_title(k)) + _extract_meta(k))
        index["clean"].add(ck)
    index["items"] = items
    # TMDB id 去重通道：历史记录中已记录 tmdb_id 的，建 id → 标题 映射
    tmdb_ids = set()
    tmdb_map = {}
    for k, v in history.items():
        raw = v.get("tmdb_id")
        if raw:
            try:
                tid = int(raw)
            except (TypeError, ValueError):
                tid = None
            if tid:
                tmdb_ids.add(tid)
                tmdb_map[tid] = k
    index["tmdb_ids"] = tmdb_ids
    index["tmdb_map"] = tmdb_map
    if qas_cache:
        qitems = []
        for name in qas_cache:
            cn = _clean_title(name)
            qitems.append((name, cn, _core_title(name)) + _extract_meta(name))
        index["qas_items"] = qitems
    else:
        index["qas_items"] = []
    return index


def _match_one(t_clean, t_core, t_year, t_season, cand_clean, cand_core, cand_year, cand_season):
    """单条候选匹配（P0 优化：规范化主标题 + 年份/季，约束子串防误判）

    匹配优先级：
      1) clean 精确相等；
      2) 核心标题相同且年份/季不冲突（同一作品的不同写法/分辨率）；
      3) 受约束的包含匹配（短名是长名的一部分），但用双门槛
         （短名长度 ≥ 4 且长度差 ≥ 2，且核心也互为包含），
         避免"阿凡达"误吞"阿凡达2"、"速度与激情"误吞"速度与激情9"
         等续集/系列数字差异导致的误判。
    """
    if t_clean == cand_clean:
        return True
    if t_core and t_core == cand_core:
        if t_year and cand_year and t_year != cand_year:
            return False
        if t_season and cand_season and t_season != cand_season:
            return False
        return True
    if len(t_clean) >= 4 and len(cand_clean) >= 4:
        if (t_clean in cand_clean or cand_clean in t_clean) and abs(len(cand_clean) - len(t_clean)) >= 2:
            if t_core in cand_core or cand_core in t_core:
                return True
    return False


def _find_in_history(title, history, index=None, tmdb_id=None):
    if title in history:
        return True
    # TMDB id 去重（最高优先级）：同一作品的不同译名/续集在 TMDB 拥有稳定唯一 id，
    # 只要 id 命中历史记录即判为已存在，避免标题规范化无法覆盖的跨名误转。
    if tmdb_id:
        try:
            tid = int(tmdb_id)
        except (TypeError, ValueError):
            tid = None
        if tid:
            if index and tid in index.get("tmdb_ids", set()):
                return True
            if not index:
                for v in history.values():
                    raw = v.get("tmdb_id")
                    if raw:
                        try:
                            if int(raw) == tid:
                                return True
                        except (TypeError, ValueError):
                            pass
    t_clean = _clean_title(title)
    t_core = _core_title(title)
    t_year, t_season = _extract_meta(title)
    if index:
        if title in index["exact"]:
            return True
        if t_clean in index["clean"]:
            return True
        for (k, k_clean, k_core, k_year, k_season) in index["items"]:
            if _match_one(t_clean, t_core, t_year, t_season, k_clean, k_core, k_year, k_season):
                return True
        for (name, n_clean, n_core, n_year, n_season) in index["qas_items"]:
            if _match_one(t_clean, t_core, t_year, t_season, n_clean, n_core, n_year, n_season):
                return True
        return False
    # 退化路径（无 index）：保持相同约束规则
    for k in history:
        k_clean = _clean_title(k)
        k_core = _core_title(k)
        k_year, k_season = _extract_meta(k)
        if _match_one(t_clean, t_core, t_year, t_season, k_clean, k_core, k_year, k_season):
            return True
    return False

def build_transfer_tasks(tasks_config, filters=None):
    filters = filters or {}
    all_t = []
    for tk in tasks_config:
        try:
            # 如果任务已包含 title（来自豆瓣想看列表），直接使用
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
        return task, sr, None
    except Exception as e:
        log("搜索异常 {}: {}".format(title, e))
        return task, [], str(e)

def run_transfer(task_list, limit):
    global transfer_status
    tid = get_ident()
    task_id = uuid.uuid4().hex
    exec_record_id = None
    try:
        rec = add_exec_record("transfer", "开始转存 ({} 条)".format(len(task_list)), "running")
        exec_record_id = rec["id"]
    except Exception:
        pass
    with transfer_lock:
        transfer_status.update({"running": True, "summary": None,
                                "start_time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                                "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)},
                                "thread_id": tid, "stop": False,
                                "task_id": task_id, "run_type": "transfer"})
    _register_running_task(task_id)
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
            category = task.get("category", "movie")
            if _find_in_history(title, history, history_index):
                log("已跳过: {}".format(title))
                results.append({"title": title, "status": "skipped", "msg": "skip", "category": category})
                with transfer_lock:
                    transfer_status["stats"]["skipped"] += 1
                    sse_broadcast("transfer_progress", dict(transfer_status))
                continue
            # 标题未命中：用 TMDB id 兜底去重（同一作品的不同译名 / 续集 / 分季差异）
            tmdb_id = _resolve_tmdb_id(title, category)
            if tmdb_id:
                task["tmdb_id"] = tmdb_id
                if _find_in_history(title, history, history_index, tmdb_id=tmdb_id):
                    log("已跳过(TMDB): {}".format(title))
                    results.append({"title": title, "status": "skipped", "msg": "skip", "category": category})
                    with transfer_lock:
                        transfer_status["stats"]["skipped"] += 1
                        sse_broadcast("transfer_progress", dict(transfer_status))
                    continue
            pending_tasks.append(task)

        log("待搜索任务: {} 条，并发数: {}".format(len(pending_tasks), SEARCH_CONCURRENCY))

        search_results = {}
        search_errors = {}
        with ThreadPoolExecutor(max_workers=SEARCH_CONCURRENCY) as executor:
            future_map = {executor.submit(_search_single_task, t): t for t in pending_tasks}
            for future in as_completed(future_map):
                if transfer_status.get("stop"):
                    for f in future_map:
                        f.cancel()
                    log("任务已被用户终止，取消剩余搜索")
                    break
                try:
                    task, sr, err = future.result()
                    search_results[task["title"]] = sr
                    search_errors[task["title"]] = err
                    with transfer_lock:
                        transfer_status["stats"]["searched"] += 1
                        sse_broadcast("transfer_progress", dict(transfer_status))
                except Exception as e:
                    task = future_map[future]
                    log("搜索任务异常 {}: {}".format(task["title"], e))
                    search_results[task["title"]] = []
                    search_errors[task["title"]] = str(e)
                    with transfer_lock:
                        transfer_status["stats"]["searched"] += 1
                        sse_broadcast("transfer_progress", dict(transfer_status))

        log("搜索完成，开始转存...")

        def _do_transfer(task):
            """单条转存 worker（并发执行）。限额采用"先占位、失败回退"，
            保证 limit = 最大成功数 的语义与串行版本一致。"""
            nonlocal transferred
            if transfer_status.get("stop"):
                return {"title": task["title"], "status": "stopped", "msg": "stopped", "category": task.get("category", "")}
            with transfer_lock:
                if transferred >= limit:
                    return {"title": task["title"], "status": "skipped", "msg": "limit", "category": task.get("category", "")}
                transferred += 1
            title = task["title"]
            category = task.get("category", "movie")
            savepath = task["savepath"]
            sr = search_results.get(title, [])
            if not sr:
                err = search_errors.get(title)
                if err:
                    log("搜索失败: {} ({})".format(title, err))
                    return {"title": title, "status": "search_failed", "msg": err, "category": category}
                log("未找到: {}".format(title))
                with transfer_lock:
                    transferred -= 1
                return {"title": title, "status": "not_found", "msg": "not_found", "category": category}
            # —— 过滤失效链接，避免转存空文件夹 ——
            valid_sr = []
            for item in sr:
                u = item.get("url", "")
                if not u:
                    continue
                try:
                    ok, _msg = validate_share_link(u)
                except Exception as e:
                    log("链接校验异常(保守保留): {} ({})".format(u, e))
                    ok = True
                if ok:
                    valid_sr.append(item)
                else:
                    log("跳过失效链接: {} -> {}".format(item.get("title", title), u))
            if not valid_sr:
                log("全部候选链接失效: {}".format(title))
                with transfer_lock:
                    transferred -= 1
                return {"title": title, "status": "expired", "msg": "all_links_invalid", "category": category}

            chosen = valid_sr[0]
            log("找到: {}".format(chosen.get("note", title)))
            pattern = VIDEO_SUB
            replace = TV_REPLACE if category == "tv" else ""
            res = add_and_run(title, chosen.get("url", ""), "{}/{}".format(savepath, title), pattern, replace)
            log("  {}".format(res["msg"]))
            tmdb_id = task.get("tmdb_id") or _resolve_tmdb_id(title, category)
            info = {"date": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"),
                    "status": res["status"], "category": category, "tmdb_id": tmdb_id}
            upsert_history_item(title, info)
            if res["status"] not in ("ok", "done"):
                with transfer_lock:
                    transferred -= 1
            time.sleep(3)
            return {"title": title, "status": res["status"], "msg": res["msg"], "category": category}

        with ThreadPoolExecutor(max_workers=TRANSFER_CONCURRENCY) as executor:
            futures = {executor.submit(_do_transfer, t): t for t in pending_tasks}
            for future in as_completed(futures):
                if transfer_status.get("stop"):
                    for f in futures:
                        f.cancel()
                    log("任务已被用户终止")
                    break
                r = future.result()
                with transfer_lock:
                    if r["status"] in ("ok", "done"):
                        transfer_status["stats"]["ok"] += 1
                    elif r["status"] in ("skipped", "exists", "limit", "stopped", "not_found", "expired", "invalid"):
                        if r["status"] != "stopped":
                            transfer_status["stats"]["skipped"] += 1
                    elif r["status"] == "search_failed":
                        transfer_status["stats"]["failed"] += 1
                    else:
                        transfer_status["stats"]["failed"] += 1
                    sse_broadcast("transfer_progress", dict(transfer_status))
                results.append(r)
    except Exception as e:
        error_msg = str(e)
        log("转存异常: {}".format(e))
        results.append({"title": "异常中断", "status": "error", "msg": error_msg})
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["stop"] = False
            transfer_status["summary"] = {
                "transferred": transferred,
                "total": len(task_list),
                "results": results,
                "error": error_msg,
            }
            sse_broadcast("transfer_progress", dict(transfer_status))
        _snapshot_finish(task_id)
        _drain_pending_scheduled()
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