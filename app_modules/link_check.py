# link_check.py — 链接检测异步队列
#
# 目的：把「手动搜索 → 对每个结果做链接检测」这条路径从 ThreadedHTTPServer 的请求线程中
# 彻底剥离。原先每个 /api/check_link 都会在一个请求线程上同步阻塞 QAS get_share_detail
# （最多 10s），无界线程被占满后会饿死 /api/search 与 /api/config，表现为页面无法搜索、
# 并误报「未配置 TMDB API Key」。
#
# 现在：/api/check_link 只做「入队 + 立即返回任务状态」，真正的验证调用由一个固定大小的
# worker 线程池异步执行；前端提交后轮询 /api/check_link/status 拿结果。请求线程因此零占用，
# 并发上限 = worker 数（与 HTTP 服务器线程模型解耦）。
#
# 验证后端依据任务的 source 自动路由：
#   - source == "qas"    （默认，向后兼容）：走 QAS get_share_detail，用于转存/失效检测链路
#   - source == "pansou"            ：走 PanSou /api/check/links，用于盘搜搜索结果链接检测
#     （盘搜返回的链接可能是 quark/baidu/aliyun/115 等多种格式，QAS 无法识别，必须走 PanSou 自身校验）
import hashlib
import threading
import queue
import time

from utils import log

_LINK_WORKERS = 3          # 实际执行验证调用的 worker 数
_LINK_QUEUE_MAX = 32       # 待处理队列上限，超过即返回「繁忙」
_LINK_RESULT_TTL = 120     # 检测结果缓存秒数（期内同 URL+source 命中缓存，不再打后端）
_LINK_TASK_TTL = 600       # 任务元数据保留秒数（用于清理，防止内存无限增长）
_LINK_SWEEP_INTERVAL = 60  # 过期清理周期（秒）

# 支持的验证后端标识
_SOURCE_QAS = "qas"
_SOURCE_PANSOU = "pansou"

_q = queue.Queue(maxsize=_LINK_QUEUE_MAX)
_store = {}                # task_id -> {url, source, status, result, ts}
_url_index = {}            # "<url>|<source>" -> task_id（去重用）
_lock = threading.Lock()
_threads = []
_started = False


def _task_key(url, source=_SOURCE_QAS):
    """去重/缓存用的组合键（url + source），确保不同后端对同一 url 的检测结果互不串味。"""
    return "{}|{}".format(url, source)


def _task_id(url, source=_SOURCE_QAS):
    return hashlib.md5(_task_key(url, source).encode("utf-8")).hexdigest()[:16]


def _check_pansou(url):
    """用 PanSou /api/check/links 检测单个链接，结果归一化为 {success, message}。

    PanSou 返回示例：
      {"results":[{"url":..., "state":"ok"|"bad"|"locked"|"unsupported"|"uncertain",
                   "summary":"链接有效"}]}
    错误响应示例：{"code":400, "message":"items不能为空"}
    """
    from transfer import _get_pansou_client
    client = _get_pansou_client()
    raw = client.check_links([url])
    if not raw or not isinstance(raw, dict):
        return {"success": False, "message": "盘搜检测无返回"}
    results = raw.get("results")
    if not results:
        # 可能是整体错误响应 {code, message}
        msg = raw.get("message", "") or "盘搜链接无法识别或检测失败"
        return {"success": False, "message": msg}
    item = results[0] if isinstance(results, list) else {}
    state = item.get("state", "")
    summary = item.get("summary", "") or ""
    # 仅当 state 明确为 "bad" 时判定为失效；ok 为有效；
    # locked/unsupported/uncertain 等无法确认失效，视为「未确认失效」以免误标红。
    if state == "ok":
        return {"success": True, "message": summary or "链接正常"}
    if state == "bad":
        return {"success": False, "message": summary or "链接失效"}
    # locked / unsupported / uncertain 等：不阻断转存，提示真实状态
    state_hint = {
        "locked": "链接已锁定或需要密码",
        "unsupported": "暂不支持检测该类型链接",
        "uncertain": "链接状态不确定，请自行确认",
    }.get(state, "链接状态未知")
    return {"success": True, "message": summary or state_hint}


def _worker():
    while True:
        item = _q.get()
        try:
            if item is None:
                break
            url, source = item
            tid = _task_id(url, source)
            with _lock:
                entry = _store.get(tid)
                if entry is None:
                    # 任务已被清理，跳过
                    continue
                entry["status"] = "running"
            try:
                if source == _SOURCE_PANSOU:
                    result = _check_pansou(url)
                else:
                    from transfer import _get_qas_client
                    client = _get_qas_client()
                    result = client.get_share_detail(url)
            except Exception as e:
                log("链接检测 worker 异常: {}".format(e))
                result = {"success": False, "message": "检测失败: {}".format(e)}
            with _lock:
                _store[tid] = {"url": url, "source": source, "status": "done",
                               "result": result, "ts": time.time()}
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
                        _url_index.pop(_task_key(e.get("url", ""), e.get("source", _SOURCE_QAS)), None)
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


def enqueue(url, source=_SOURCE_QAS):
    """提交一次链接检测。

    :param url: 待检测链接
    :param source: 验证后端，"qas"（默认，向后兼容）或 "pansou"。盘搜搜索结果应传 "pansou"。
    :return dict:
      {"state": "done", "task_id": tid, "result": <归一化结果>}  # 命中新鲜缓存
      {"state": "pending", "task_id": tid}                     # 已入队/进行中
      {"state": "busy"}                                         # 队列已满，请稍后重试
    """
    start_workers()  # 惰性保活：即使 main 未显式启动也能工作
    u = url.strip()
    src = source if source in (_SOURCE_QAS, _SOURCE_PANSOU) else _SOURCE_QAS
    key = _task_key(u, src)
    tid = _task_id(u, src)
    now = time.time()
    with _lock:
        entry = _store.get(tid)
        if entry and entry["status"] == "done" and (now - entry.get("ts", 0)) < _LINK_RESULT_TTL:
            return {"state": "done", "task_id": tid, "result": entry["result"]}
        if entry and entry["status"] in ("pending", "running"):
            return {"state": entry["status"], "task_id": tid}
        # 新建 / 覆盖过期任务
        _store[tid] = {"url": u, "source": src, "status": "pending", "result": None, "ts": now}
        _url_index[key] = tid
    try:
        _q.put((u, src), block=False)
        return {"state": "pending", "task_id": tid}
    except queue.Full:
        with _lock:
            # 回滚本次登记，避免留下永远 pending 的僵尸任务
            if _store.get(tid, {}).get("status") == "pending":
                _store.pop(tid, None)
                _url_index.pop(key, None)
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
                _url_index.pop(_task_key(entry.get("url", ""), entry.get("source", _SOURCE_QAS)), None)
                return {"state": "unknown"}
            return {"state": "done", "result": entry["result"]}
        return {"state": entry["status"]}
