# routes_static.py — 静态文件 & SSE 路由 Mixin
import os, time, uuid, queue, gzip
from threading import Lock as ThreadLock
from utils import sse_clients, sse_lock, SSE_MAX

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css"}
_GZIP_EXTS = {".html", ".js", ".css", ".json", ".svg", ".xml", ".txt"}
_static_cache = {}
_static_cache_lock = ThreadLock()
_STATIC_CACHE_MAX = 50


def _prune_static_cache():
    if len(_static_cache) <= _STATIC_CACHE_MAX:
        return
    sorted_keys = sorted(_static_cache.keys(), key=lambda k: _static_cache[k][0])
    to_remove = len(_static_cache) - _STATIC_CACHE_MAX
    for k in sorted_keys[:to_remove]:
        del _static_cache[k]


def _gzip_compress(data):
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6) as f:
        f.write(data)
    return buf.getvalue()


def _get_static_file(relpath):
    fp = os.path.join(STATIC_DIR, relpath)
    if not os.path.isfile(fp):
        return None, None, None
    try:
        mtime = os.path.getmtime(fp)
    except OSError:
        return None, None, None
    with _static_cache_lock:
        cached = _static_cache.get(fp)
        if cached and cached[0] == mtime:
            return cached[1], fp, mtime
    ext = os.path.splitext(fp)[1].lower()
    ctype = MIME.get(ext, "application/octet-stream")
    try:
        with open(fp, "rb") as f:
            raw = f.read()
    except Exception:
        return None, None, None
    gzipped = None
    if ext in _GZIP_EXTS and len(raw) > 1024:
        gzipped = _gzip_compress(raw)
    data = (ctype, raw, gzipped)
    with _static_cache_lock:
        _static_cache[fp] = (mtime, data)
        _prune_static_cache()
    return data, fp, mtime


class StaticRouteMixin:
    """静态文件 & SSE 路由"""

    def _send_static_file(self, data, cache_control=None, mtime=None):
        ctype, body, gzipped = data
        accept_encoding = self.headers.get("Accept-Encoding", "")
        use_gzip = gzipped is not None and "gzip" in accept_encoding
        resp_body = gzipped if use_gzip else body
        
        if mtime:
            etag = '"{}"'.format(int(mtime))
            if_none_match = self.headers.get("If-None-Match", "")
            if if_none_match == etag:
                self.send_response(304)
                self.end_headers()
                return
        
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(resp_body)))
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        if mtime:
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(mtime)))
        self.end_headers()
        self.wfile.write(resp_body)

    def _handle_static_get(self, route):
        if route == "/" or route == "/index.html":
            data, _, mtime = _get_static_file("index_new.html")
            if data:
                self._send_static_file(data, mtime=mtime)
                return True
            self._send_json({"error": "not found"}, 404)
            return True

        if route == "/login.html":
            data, _, mtime = _get_static_file("login_new.html")
            if data:
                self._send_static_file(data, mtime=mtime)
                return True
            self._send_json({"error": "not found"}, 404)
            return True

        if route.startswith("/static/"):
            rel = route[len("/static/"):]
            if ".." in rel or rel.startswith("/"):
                self._send_json({"error": "invalid path"}, 400)
                return True
            data, _, mtime = _get_static_file(rel)
            if data:
                self._send_static_file(data, cache_control="public, max-age=86400", mtime=mtime)
            else:
                self._send_json({"error": "not found"}, 404)
            return True

        if route == "/health":
            self._send_json({"status": "ok", "time": time.time()})
            return True

        if route == "/version":
            version = "1.0.0"
            version_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
            if os.path.isfile(version_path):
                try:
                    with open(version_path, "r") as f:
                        version = f.read().strip()
                except Exception:
                    pass
            self._send_json({"version": version})
            return True

        return False

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        cid = uuid.uuid4().hex[:8]
        q = queue.Queue(maxsize=100)
        with sse_lock:
            if len(sse_clients) >= SSE_MAX:
                oldest = sorted(sse_clients.keys(), key=lambda k: sse_clients[k]["time"])[0]
                del sse_clients[oldest]
            sse_clients[cid] = {"queue": q, "time": time.time()}
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                    with sse_lock:
                        if cid in sse_clients:
                            sse_clients[cid]["time"] = time.time()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except Exception:
            pass
        finally:
            with sse_lock:
                sse_clients.pop(cid, None)
        return True
