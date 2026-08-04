# link_check.py — 链接检测异步队列
#
# 目的：把「手动搜索 → 对每个结果做链接检测」这条路径从 ThreadedHTTPServer 的请求线程中
# 彻底剥离。原先每个 /api/check_link 都会在一个请求线程上同步阻塞 QAS get_share_detail
# （最多 10s），无界线程被占满后会饿死 /api/search 与 /api/config，表现为页面无法搜索、
# 并误报「未配置 TMDB API Key」。
#
# 现在：/api/check_link 只做「入队 + 立即返回任务状态」，真正的 QAS 调用由一个固定大小的
# worker 线程池异步执行；前端提交后轮询 /api/check_link/status 拿结果。请求线程因此零占用，
# 并发上限 = worker 数（与 HTTP 服务器线程模型解耦）。
import hashlib
import threading
import queue
import time

from utils import log

_LINK_WORKERS = 3          # 实际执行 QAS get_share_detail 的 worker 数
_LINK_QUEUE_MAX = 32       # 待处理队列上限，超过即返回「繁忙」
_LINK_RESULT_TTL = 120     # 检测结果缓存秒数（期内同 URL 命中缓存，不再打 QAS）
_LINK_TASK_TTL = 600       # 任务元数据保留秒数（用于清理，防止内存无限增长）
_LINK_SWEEP_INTERVAL = 60  # 过期清理周期（秒）

_q = queue.Queue(maxsize=_LINK_QUEUE_MAX)
_store = {}                # task_id -> {url, status, result, ts}
_url_index = {}            # url -> task_id（去重用）
_lock = threading.Lock()
_threads = []
_started = False


def _task_id(url):
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def _worker():
    while True:
        item = _q.get()
        try:
            if item is None:
                break
            url = item
            tid = _task_id(url)
            with _lock:
                entry = _store.get(tid)
                if entry is None:
                    # 任务已被清理，跳过
                    continue
                entry["status"] = "running"
            try:
                from transfer import _get_qas_client
                client = _get_qas_client()
                result = client.get_share_detail(url)
            except Exception as e:
                log("链接检测 worker 异常: {}".format(e))
                result = {"success": False, "message": "检测失败: {}".format(e)}
            with _lock:
                _store[tid] = {"url": url, "status": "done", "result": result, "ts": time.time()}
        finally:
            _q.task_done()


def _sweeper_loop():
    while True:
        time.sleep(_LINK_SWEEP_INTERVAL)
        try:
            now = time.time()
            with _lock:
                expired = [tid for tid, e in _store.items()
                           if (now - e.get("ts", 0)) > _LINK_TASK_TTL]
                for tid in expired:
                    e = _store.pop(tid, None)
                    if e:
                        _url_index.pop(e.get("url"), None)
        except Exception as e:
            log("链接检测清理异常: {}".format(e))


def start_workers():
    """幂等启动 worker 池与清理线程。"""
    global _started
    if _started:
        return
    with _lock:
        if _started:
            return
        for _ in range(_LINK_WORKERS):
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            _threads.append(t)
        threading.Thread(target=_sweeper_loop, daemon=True).start()
        _started = True
    log("链接检测 worker 池已启动: workers={}, queue_max={}".format(_LINK_WORKERS, _LINK_QUEUE_MAX))


def enqueue(url):
    """提交一次链接检测。

    返回 dict:
      {"state": "done", "task_id": tid, "result": <QAS结果>}   # 命中新鲜缓存
      {"state": "pending", "task_id": tid}                     # 已入队/进行中
      {"state": "busy"}                                         # 队列已满，请稍后重试
    """
    start_workers()  # 惰性保活：即使 main 未显式启动也能工作
    u = url.strip()
    tid = _task_id(u)
    now = time.time()
    with _lock:
        entry = _store.get(tid)
        if entry and entry["status"] == "done" and (now - entry.get("ts", 0)) < _LINK_RESULT_TTL:
            return {"state": "done", "task_id": tid, "result": entry["result"]}
        if entry and entry["status"] in ("pending", "running"):
            return {"state": entry["status"], "task_id": tid}
        # 新建 / 覆盖过期任务
        _store[tid] = {"url": u, "status": "pending", "result": None, "ts": now}
        _url_index[u] = tid
    try:
        _q.put(u, block=False)
        return {"state": "pending", "task_id": tid}
    except queue.Full:
        with _lock:
            # 回滚本次登记，避免留下永远 pending 的僵尸任务
            if _store.get(tid, {}).get("status") == "pending":
                _store.pop(tid, None)
                _url_index.pop(u, None)
        return {"state": "busy"}


def get_status(task_id):
    """查询检测状态。

    返回 dict:
      {"state": "done", "result": <QAS结果>}
      {"state": "pending" | "running"}
      {"state": "unknown"}   # 任务不存在或已过期清理
    """
    with _lock:
        entry = _store.get(task_id)
        if not entry:
            return {"state": "unknown"}
        if entry["status"] == "done":
            if (time.time() - entry.get("ts", 0)) > _LINK_TASK_TTL:
                _store.pop(task_id, None)
                _url_index.pop(entry.get("url"), None)
                return {"state": "unknown"}
            return {"state": "done", "result": entry["result"]}
        return {"state": entry["status"]}
