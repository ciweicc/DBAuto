# routes_auth.py — 认证相关路由 Mixin
from auth import _do_login, _login_rate_check, _check_auth, _client_ip
from utils import log, sse_broadcast


class AuthRouteMixin:
    """认证路由：登录、状态检查、SSE"""

    def _handle_auth_get(self, route):
        if route == "/api/status":
            self._send_json({"auth": _check_auth(self)})
            return True

        if route == "/api/sse":
            from routes_static import StaticRouteMixin
            return StaticRouteMixin._handle_sse(self)

        return False

    def _handle_auth_post(self, route, body):
        if route == "/api/login":
            ip = _client_ip(self)
            ok, wait = _login_rate_check(ip)
            if not ok:
                self._send_json({"success": False, "message": "too many attempts", "wait": wait}, 429)
                return True
            username = body.get("username", "")
            password = body.get("password", "")
            token = _do_login(username, password)
            if token:
                self._send_json({"success": True, "token": token})
                sse_broadcast("auth", {"status": "login", "user": username})
            else:
                self._send_json({"success": False, "message": "invalid credentials"}, 401)
            return True

        return False

    def _require_auth(self):
        if not _check_auth(self):
            self._send_json({"error": "unauthorized"}, 401)
            return False
        return True
