# utils.py — HTTP 工具、日志、SSE 广播
import json, urllib.request, logging, time
from collections import deque
from threading import Lock
from config import *

log_lock = Lock()
log_progress = []
sse_clients = {}
sse_lock = Lock()
SSE_MAX = 20

def clear_progress():
    log_progress.clear()

def setup_logging():
    logger = logging.getLogger("douban")
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)
    return logger

logger = setup_logging()

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    logger.info(msg)
    log_progress.append(line)
    _sse_broadcast("log", {"line": msg})

def _sse_broadcast(evt, data):
    payload = "event: {}\ndata: {}\n\n".format(evt, json.dumps(data, ensure_ascii=False))
    with sse_lock:
        dead = []
        for cid, q in sse_clients.items():
            try:
                q.put(payload)
            except Exception:
                dead.append(cid)
        for cid in dead:
            del sse_clients[cid]

def http_get(url, timeout=60):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15")
    req.add_header("Referer", "https://m.douban.com/")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def http_post(url, data, timeout=15):
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())
