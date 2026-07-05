# routes_base.py — 路由基类 + 路由注册机制
import json
from http.server import BaseHTTPRequestHandler

MAX_BODY_SIZE = 1024 * 1024  # 请求体最大 1MB


class BaseRouteHandler(BaseHTTPRequestHandler):
    """路由处理基类，提供通用工具方法和路由分发"""

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._send_json({"error": "invalid Content-Length"}, 400)
            return None
        if length == 0:
            return {}
        if length > MAX_BODY_SIZE:
            self._send_json({"error": "request body too large (max {}KB)".format(MAX_BODY_SIZE // 1024)}, 413)
            return None
        try:
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw)
        except Exception:
            return {}

    def _get_query_params(self):
        import urllib.parse
        if "?" not in self.path:
            return {}
        try:
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            return {k: v[0] for k, v in qs.items()}
        except Exception:
            return {}

    def _route_path(self):
        path = self.path
        if "?" in path:
            path = path.split("?", 1)[0]
        return path

    def log_message(self, format, *args):
        pass
