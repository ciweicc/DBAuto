# utils.py — HTTP 工具、日志、SSE 广播、加密工具、通用缓存
import json, logging, logging.handlers, time, hashlib, hmac, base64, os
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
    from cryptography.fernet import Fernet
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

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    logger.info(msg)
    log_progress.append(line)
    sse_broadcast("log", {"line": msg})

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
                max_retries=1
            )
            _http_session.mount("http://", adapter)
            _http_session.mount("https://", adapter)
        return _http_session

def http_get(url, timeout=None, referer=None):
    session = _get_http_session()
    headers = {}
    if referer:
        headers["Referer"] = referer
    t = timeout or HTTP_TIMEOUT_GET
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=t, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                log("HTTP GET 重试 {}: {}".format(attempt + 1, e))
                time.sleep(2)
            else:
                raise

def http_post(url, data, timeout=None):
    session = _get_http_session()
    t = timeout or HTTP_TIMEOUT_POST
    for attempt in range(3):
        try:
            resp = session.post(url, json=data, timeout=t)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                log("HTTP POST 重试 {}: {}".format(attempt + 1, e))
                time.sleep(2)
            else:
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
