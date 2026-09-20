# utils.py — HTTP 工具、日志、SSE 广播、加密工具、通用缓存
import json, logging, logging.handlers, re, time, hashlib, hmac, base64, os, random
import requests
from collections import deque
from threading import Lock, local
from config import DATA_DIR


class TTLCache:
    """带 TTL 和最大条目数的线程安全缓存"""

    def __init__(self, ttl=300, max_size=100):
        self._cache = {}
        self._ttl = ttl
        self._max_size = max_size
        self._lock = Lock()

    def get(self, key):
        now = time.time()
        with self._lock:
            if key in self._cache:
                ct, val = self._cache[key]
                if now - ct < self._ttl:
                    return val
                del self._cache[key]
            return None

    def set(self, key, value):
        now = time.time()
        with self._lock:
            self._cache[key] = (now, value)
            self._prune()

    def _prune(self):
        if len(self._cache) <= self._max_size:
            return
        sorted_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][0])
        to_remove = len(self._cache) - self._max_size
        for k in sorted_keys[:to_remove]:
            del self._cache[k]

    def clear(self):
        with self._lock:
            self._cache.clear()

    def __contains__(self, key):
        return self.get(key) is not None

    def __len__(self):
        return len(self._cache)


_HASH_SALT_FILE = os.path.join(DATA_DIR, ".salt")
_SECRET_KEY = None
_secret_lock = Lock()

def _get_secret_key():
    global _SECRET_KEY
    with _secret_lock:
        if _SECRET_KEY is not None:
            return _SECRET_KEY
        salt_path = _HASH_SALT_FILE
        if os.path.exists(salt_path):
            with open(salt_path, "rb") as f:
                _SECRET_KEY = f.read()
        else:
            _SECRET_KEY = os.urandom(32)
            os.makedirs(os.path.dirname(salt_path), exist_ok=True)
            with open(salt_path, "wb") as f:
                f.write(_SECRET_KEY)
            try:
                os.chmod(salt_path, 0o600)
            except Exception:
                pass
        return _SECRET_KEY

def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + dk).decode('ascii')

def verify_password(password, hashed):
    try:
        raw = base64.b64decode(hashed.encode('ascii'))
        salt = raw[:16]
        stored_hash = raw[16:]
        dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return hmac.compare_digest(dk, stored_hash)
    except Exception:
        return False

def _derive_key(purpose=b"secret"):
    sk = _get_secret_key()
    return hashlib.pbkdf2_hmac('sha256', sk, purpose, 10000)

def _get_fernet_key():
    dk = _derive_key(b"fernet_key")
    return base64.urlsafe_b64encode(dk[:32])

def encrypt_secret(plaintext):
    if not plaintext:
        return ""
    from cryptography.fernet import Fernet
    key = _get_fernet_key()
    f = Fernet(key)
    return f.encrypt(plaintext.encode('utf-8')).decode('ascii')

def decrypt_secret(encrypted):
    if not encrypted:
        return ""
    try:
        from cryptography.fernet import Fernet
        key = _get_fernet_key()
        f = Fernet(key)
        return f.decrypt(encrypted.encode('ascii')).decode('utf-8')
    except Exception:
        return ""

LOG_PROGRESS_MAX = 500
log_progress = deque(maxlen=LOG_PROGRESS_MAX)
sse_clients = {}
sse_lock = Lock()
SSE_MAX = 20
SSE_TIMEOUT = 300

# 日志文件路径
LOG_FILE = os.path.join(DATA_DIR, "app.log")

def _prune_sse_clients():
    now = time.time()
    expired = [cid for cid, info in sse_clients.items() if now - info["time"] > SSE_TIMEOUT]
    for cid in expired:
        del sse_clients[cid]

def clear_progress():
    log_progress.clear()

def setup_logging():
    logger = logging.getLogger("douban")
    logger.setLevel(logging.INFO)
    # 避免重复添加 handler
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        # 控制台输出
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        # 文件持久化（滚动 3 个文件，每个最大 5MB）
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass
    return logger

logger = setup_logging()

# —— 面板日志的"信息密度"过滤（issue #68）——
# 面板日志的问题不是"行太长"，而是"行太多且互相重复"：
# 用户搜一部电影会看到「搜索: X」「找到: X 的候选标题」「转存成功」「 转存成功」
# 四条在说同一件事，而读日志文件的人又确实需要全程留痕。
#
# 因此把"面板"当成**进度条**而不是"日志文件"：
#   * 纯内部实现的噪声（缓存预热、并发度、把参数回显一遍）不进面板，
#     但仍写 logger，`data/app.log` 里一条不少；
#   * 一个标题只留一条终态结论行（成功/跳过/失败），中间态不进面板。
# 谁被过滤了就写在这里，避免以后有人凭感觉再往 log() 里塞噪声。
_PANEL_DROP = (
    re.compile(r"^待搜索任务: \d+ 条，并发数"),
    re.compile(r"^搜索: "),
    re.compile(r"^QAS: \d+ 个任务已缓存$"),
    re.compile(r"^失效检测目录范围: "),
)

# —— 面板的"详情行"（issue #68 二次追问：密度仍然太低）——
# 第一轮过滤后每标题只剩一条终态行，但**行本身**仍然把一整段 requests 异常
# 糊在面板上，并且被视觉折成 5–6 行：
#
#   [20:46:56] HTTP POST 超过重试预算，放弃: HTTPConnectionPool(host='19
#   2.168.5.55', port=8080): Read timed out. (read timeout=20)
#   [20:46:56] PanSou 重试: HTTPConnectionPool(host='192.168.5.55', ...
#
# 这两行是同一件事的两条流水，且用户能读的只有前半句。后端在这里把行拆成
# 「主行 + detail」，前端默认只渲染主行（计数进进度区），点「详情」才展开 ——
# 这样面板上一次 1 条任务的转存从 7 行压缩到 3 行。
_DETAIL_MARKERS = (
    "HTTPConnectionPool",
    "HTTPSConnectionPool",
    "Max retries exceeded",
    "Read timed out",
    "ConnectTimeout",
    "ConnectionError",
    "ProtocolError",
    "RemoteDisconnected",
    "SSLError",
    "JSONDecodeError",
    "Traceback (most recent call last)",
)

# "主行: 详情" 的分隔符。要求**分隔符前后都有内容、且冒号后紧跟空格**：
#   * "TMDB: 未配置 api_key"   → ": " 前面只有 4 个字符，是产物标签不是主行，
#                                整条留作主行（这里用一个最小主行长度卡住）；
#   * "PanSou 重试: HTTPCon..." → 切成 ("PanSou 重试", "HTTPCon...")。
_DETAIL_SPLIT = re.compile(r"(?<=\S)\s*: \s*")
_MIN_MAIN_LEN = 8


def _is_detail(text):
    """整段文本是否只是一个「详情」（不该占面板一行）。"""
    for mk in _DETAIL_MARKERS:
        if mk in text:
            return True
    # 只有一层括号的纯参数回显，例如 "(timeout)"
    return bool(re.match(r"^[\(\[].*[\)\]]$", text.strip()))


def _split_detail(msg):
    """把一条日志拆成 (主行, 详情)。

    返回 detail 为 None 表示这条日志本身就是主行。
    只切**第一处**分隔符：详情里再出现冒号（`... (read timeout=20)`）留在 detail 里，
    避免把一句人话切碎。
    """
    if not msg:
        return msg, None
    # 1) 整条就是一段异常/参数 → 整条降级为详情，主行由调用方另外给
    if _is_detail(msg):
        return None, msg
    # 2) "主行: 详情" —— 只切第一处
    m = _DETAIL_SPLIT.search(msg)
    if m:
        main, rest = msg[:m.start()].strip(), msg[m.end():].strip()
        # 分隔符前太短（如 "TMDB"）说明这是产物标签而不是「主行: 详情」
        if len(main) >= _MIN_MAIN_LEN and rest:
            return main, rest
    return msg, None


def _shorten(text, limit=120):
    """把一段异常文本压成一行可读的主行（详情完整地留在 detail 里）。"""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def log(msg, tag=None, panel=True, detail=None):
    """记录一条日志并广播到 SSE / 前端进度面板。

    Args:
        msg: 日志正文（**面板主行**的正文）。
        tag: 可选的来源/批次标签（如 "TMDB"、"TRANSFER#ab12cd"）。
            提供时日志行格式变为 "[HH:MM:SS][TAG] msg"（标签转大写、去除内部空格），
            便于在混合日志流中区分来源；为 None 时保持原 "[HH:MM:SS] msg" 格式，
            向后兼容所有旧调用方。
        panel: 是否同时投递到前端「执行日志」面板（默认 True）。
            置 False 表示"只写日志文件，不上面板"——用于过滤实现细节类噪声。
        detail: 可选的完整细节（如 requests 的整段异常）。前端默认**折叠**，
            点「详情」才展开；`data/app.log` 里无论折叠与否都是完整原文。
            不传时按 `"主行: 详情"` 的写法自动拆分（见 `_split_detail`），
            这样 `log("PanSou 重试: {}".format(e))` 这种旧调用会被自动压成一行。
    """

    ts = time.strftime("%H:%M:%S")
    tag_label = tag.upper().replace(" ", "") if tag else None

    def _fmt(text):
        return "[{}][{}] {}".format(ts, tag_label, text) if tag_label \
            else "[{}] {}".format(ts, text)

    # —— 文件日志：完整原文 + 完整 detail，一条不少 ——
    logger.info(msg if detail is None else "{} | {}".format(msg, detail))

    if not panel:
        return
    if any(rx.search(msg) for rx in _PANEL_DROP):
        return

    if detail is None:
        # 未显式给 detail：先按 "主行: 详情" 自动拆（兼容旧调用写法）
        main, auto_detail = _split_detail(msg)
        if main is not None:
            short, detail = _shorten(main), auto_detail
        elif auto_detail is not None:
            # 整条就是一段异常（没有可读主行）：给一个可读主行，原文进详情
            short, detail = "请求失败（可展开详情）", msg
        else:
            # 长句但没有结构化详情：不是异常，绝不塞进「详情」里藏起来，
            # 只是把 progress/SSE 里那条主行截短（前端有 title 展示全文）。
            short, detail = _shorten(msg), None
    else:
        short = _shorten(msg)

    line = _fmt(short)
    log_progress.append(line)
    if not detail:
        # 普通一行：SSE 与 progress 用同一条文本，前端断连回填时两边一致。
        sse_broadcast("log", {"line": line})
        return

    # 带详情：progress 里主行 + 详情行成对出现，SSE 用一条事件发出去
    # （progress 是 SSE 断连时的降级回填源，必须自足）。
    # 详情行沿用 "主行: 详情" 的写法，前端按 `detailAttachesTo` 认领成折叠块；
    # 进度区（成功/失败计数）读的是 transfer_status.stats，不会被这里影响。
    detail_line = "{}: {}".format(_fmt(short), _flatten(detail))
    log_progress.append(detail_line)
    sse_broadcast("log", {"line": line, "detail": detail_line, "summary": line})


def _flatten(text):
    """详情里的换行/连续空白压平：一条 detail 折成 5–6 行就失去"折叠"的意义了。"""
    return re.sub(r"\s+", " ", text or "").strip()

_sse_thread_local = local()

def sse_broadcast(evt, data):
    # 使用线程局部变量防止同线程递归广播，不影响其他线程
    if getattr(_sse_thread_local, "in_broadcast", False):
        return
    _sse_thread_local.in_broadcast = True
    try:
        payload = "event: {}\ndata: {}\n\n".format(evt, json.dumps(data, ensure_ascii=False))
        now = time.time()
        with sse_lock:
            dead = []
            for cid, info in sse_clients.items():
                try:
                    info["queue"].put(payload)
                    info["time"] = now
                except Exception:
                    dead.append(cid)
            for cid in dead:
                del sse_clients[cid]
            _prune_sse_clients()
    finally:
        _sse_thread_local.in_broadcast = False

_http_session = None
_http_session_lock = Lock()
HTTP_TIMEOUT_GET = 30
HTTP_TIMEOUT_POST = 15
HTTP_TIMEOUT_STREAM = 120
# 单次请求重试总预算（秒）：防止上游抖动时无限重试形成"重试风暴"
HTTP_RETRY_TOTAL = 12


def _http_retryable(exc):
    """判断 HTTP 异常是否值得重试：仅网络层错误 / 429 / 5xx（含 408）可重试；
    4xx（如 400/401/403/404）通常不可重试，重试只会空耗时间。"""
    if isinstance(exc, (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.ChunkedEncodingError,
                        requests.exceptions.HTTPError)):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is None:
            return True  # 网络层错误（超时 / 连接失败）可重试
        return status in (408, 429) or status >= 500
    return False


def _http_backoff(attempt, base=1.0, cap=5.0):
    """指数退避 + 随机抖动，避免客户端同步重试把压力瞬间打满服务端。"""
    delay = min(base * (2 ** attempt), cap)
    return delay / 2 + random.uniform(0, delay / 2)


def _get_http_session():
    global _http_session
    with _http_session_lock:
        if _http_session is None:
            _http_session = requests.Session()
            _http_session.headers.update({
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
                "Accept": "application/json"
            })
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=20,
                pool_maxsize=20,
                # 重试统一由下方 http_get/http_post 的退避逻辑掌控，避免适配器层叠加放大
                max_retries=0
            )
            _http_session.mount("http://", adapter)
            _http_session.mount("https://", adapter)
        return _http_session

def http_get(url, timeout=None, referer=None, retries=3):
    session = _get_http_session()
    headers = {}
    if referer:
        headers["Referer"] = referer
    t = timeout or HTTP_TIMEOUT_GET
    start = time.monotonic()
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=t, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            can_retry = attempt < retries - 1 and _http_retryable(e)
            within_budget = (time.monotonic() - start) < HTTP_RETRY_TOTAL
            if can_retry and within_budget:
                log("HTTP GET 重试 {}".format(attempt + 1), detail="GET {} | {}".format(url, e))
                time.sleep(_http_backoff(attempt))
            else:
                if _http_retryable(e) and not within_budget:
                    log("HTTP GET 超时放弃（已重试 {} 次）".format(attempt + 1), detail="GET {} | {}".format(url, e))
                raise

def http_post(url, data, timeout=None, retries=3):
    session = _get_http_session()
    t = timeout or HTTP_TIMEOUT_POST
    start = time.monotonic()
    for attempt in range(retries):
        try:
            resp = session.post(url, json=data, timeout=t)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            can_retry = attempt < retries - 1 and _http_retryable(e)
            within_budget = (time.monotonic() - start) < HTTP_RETRY_TOTAL
            if can_retry and within_budget:
                log("HTTP POST 重试 {}".format(attempt + 1), detail="POST {} | {}".format(url, e))
                time.sleep(_http_backoff(attempt))
            else:
                if _http_retryable(e) and not within_budget:
                    log("HTTP POST 超时放弃（已重试 {} 次）".format(attempt + 1), detail="POST {} | {}".format(url, e))
                raise

def http_post_stream(url, data, timeout=None):
    session = _get_http_session()
    t = timeout or HTTP_TIMEOUT_STREAM
    return session.post(url, json=data, timeout=t, stream=True)

def atomic_write_json(filepath, data):
    dirname = os.path.dirname(filepath)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp_path, filepath)
