# utils.py — HTTP 工具、日志、SSE 广播、加密工具、通用缓存
import json, logging, time, hashlib, hmac, base64, os
import requests
from collections import deque
from threading import Lock
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


class DiskCache:
    """磁盘缓存，支持持久化存储"""

    def __init__(self, base_path, max_size=1000):
        self._base_path = os.path.join(base_path, "cache")
        os.makedirs(self._base_path, exist_ok=True)
        self._max_size = max_size
        self._lock = Lock()
        self._file_index = self._load_index()

    def _load_index(self):
        index_path = os.path.join(self._base_path, "_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_index(self):
        index_path = os.path.join(self._base_path, "_index.json")
        atomic_write_json(index_path, self._file_index)

    def _get_file_path(self, key):
        hash_val = hashlib.md5(key.encode("utf-8")).hexdigest()
        return os.path.join(self._base_path, "{}.json".format(hash_val))

    def get(self, key):
        with self._lock:
            if key not in self._file_index:
                return None
            file_path = self._get_file_path(key)
            if not os.path.exists(file_path):
                del self._file_index[key]
                self._save_index()
                return None
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                if data.get("expire_time", 0) > now:
                    return data.get("value")
                os.remove(file_path)
                del self._file_index[key]
                self._save_index()
                return None
            except Exception:
                return None

    def set(self, key, value, ttl=3600):
        with self._lock:
            expire_time = time.time() + ttl
            self._file_index[key] = {
                "expire_time": expire_time,
                "created_at": time.time()
            }
            file_path = self._get_file_path(key)
            data = {
                "key": key,
                "value": value,
                "expire_time": expire_time,
                "created_at": time.time()
            }
            atomic_write_json(file_path, data)
            self._prune()
            self._save_index()

    def _prune(self):
        if len(self._file_index) <= self._max_size:
            return
        sorted_keys = sorted(self._file_index.keys(), key=lambda k: self._file_index[k]["created_at"])
        to_remove = len(self._file_index) - self._max_size
        for k in sorted_keys[:to_remove]:
            file_path = self._get_file_path(k)
            try:
                os.remove(file_path)
            except Exception:
                pass
            del self._file_index[k]

    def clear(self):
        with self._lock:
            for key in list(self._file_index.keys()):
                file_path = self._get_file_path(key)
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            self._file_index.clear()
            self._save_index()

    def __contains__(self, key):
        return self.get(key) is not None

    def __len__(self):
        return len(self._file_index)


class TwoLevelCache:
    """二级缓存：内存缓存（快速）+ 磁盘缓存（持久化）"""

    def __init__(self, memory_ttl=300, memory_max_size=100, disk_max_size=1000):
        self._memory_cache = TTLCache(ttl=memory_ttl, max_size=memory_max_size)
        self._disk_cache = DiskCache(DATA_DIR, max_size=disk_max_size)
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            val = self._memory_cache.get(key)
            if val is not None:
                return val
            val = self._disk_cache.get(key)
            if val is not None:
                self._memory_cache.set(key, val)
            return val

    def set(self, key, value, ttl=3600):
        with self._lock:
            self._memory_cache.set(key, value)
            self._disk_cache.set(key, value, ttl)

    def clear(self):
        with self._lock:
            self._memory_cache.clear()
            self._disk_cache.clear()

    def __contains__(self, key):
        return self.get(key) is not None

    def __len__(self):
        return len(self._disk_cache)

_thread_pool = None
_thread_pool_lock = Lock()

def get_thread_pool(max_workers=5):
    global _thread_pool
    with _thread_pool_lock:
        if _thread_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="DBAuto")
        return _thread_pool

def shutdown_thread_pool():
    global _thread_pool
    with _thread_pool_lock:
        if _thread_pool is not None:
            _thread_pool.shutdown(wait=True)
            _thread_pool = None

def submit_task(func, *args, **kwargs):
    pool = get_thread_pool()
    return pool.submit(func, *args, **kwargs)

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
    import base64
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

log_lock = Lock()
LOG_PROGRESS_MAX = 500
log_progress = deque(maxlen=LOG_PROGRESS_MAX)
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
    sse_broadcast("log", {"line": msg})

def sse_broadcast(evt, data):
    payload = "event: {}\ndata: {}\n\n".format(evt, json.dumps(data, ensure_ascii=False))
    with sse_lock:
        dead = []
        for cid, q in sse_clients.items():
            try:
                q.put(payload)
            except Exception as e:
                log("SSE 客户端 {} 发送失败: {}".format(cid, e))
                dead.append(cid)
        for cid in dead:
            del sse_clients[cid]

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
