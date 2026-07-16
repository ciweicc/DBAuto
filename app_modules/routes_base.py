# routes_base.py — 路由基类 + 路由注册机制
import json
from http.server import BaseHTTPRequestHandler

MAX_BODY_SIZE = 1024 * 1024  # 请求体最大 1MB


class BaseRouteHandler(BaseHTTPRequestHandler):
    """路由处理基类，提供通用工具方法和路由分发"""

    # HTTP/1.1 + keep-alive：Cloudflare Tunnel / 现代浏览器必需
    protocol_version = "HTTP/1.1"
    # keep-alive 空闲超时：30s 无新请求则关闭连接
    timeout = 30

    def parse_request(self):
        """修复 Python stdlib：HTTP/1.1 默认 keep-alive"""
        result = BaseHTTPRequestHandler.parse_request(self)
        if result and self.protocol_version >= "HTTP/1.1":
            conntype = self.headers.get('Connection', '')
            if conntype.lower() != 'close':
                self.close_connection = False
        return result

    # ---------- 反代友好 ----------

    def _get_client_ip(self):
        """获取真实客户端 IP（支持反代）"""
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        xri = self.headers.get("X-Real-IP", "")
        if xri:
            return xri.strip()
        return self.client_address[0] if self.client_address else ""

    def _is_https(self):
        """判断是否 HTTPS（反代场景）"""
        xfp = self.headers.get("X-Forwarded-Proto", "")
        if xfp:
            return xfp.lower() == "https"
        return bool(self.headers.get("X-Forwarded-Ssl", "").lower() in ("on", "https"))

    def _get_base_url(self):
        """获取外部可访问的基础 URL（反代场景）"""
        host = self.headers.get("X-Forwarded-Host", "") or self.headers.get("Host", "")
        scheme = "https" if self._is_https() else "http"
        return "{}://{}".format(scheme, host)

    # ---------- 响应工具 ----------

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
