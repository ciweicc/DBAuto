# auth.py — 登录、token 管理、频率限制、认证检查
import secrets, time
from threading import Lock
from config import AUTH_USER, AUTH_PASS
from utils import log

auth_tokens = {}
auth_lock = Lock()
TOKEN_TTL = 86400
login_attempts = {}
login_lock = Lock()

def _gen_token():
    return secrets.token_hex(16)

def _check_auth(handler):
    token = handler.headers.get("X-Auth-Token", "")
    if not token:
        ah = handler.headers.get("Authorization", "")
        if ah.startswith("Bearer "):
            token = ah[7:]
    if not token and "?" in handler.path:
        import urllib.parse
        try:
            qs = urllib.parse.parse_qs(handler.path.split("?", 1)[1])
            token = qs.get("token", [""])[0]
        except Exception:
            pass
    now = time.time()
    with auth_lock:
        expired = [t for t, v in auth_tokens.items() if now - v["time"] > TOKEN_TTL]
        for t in expired:
            del auth_tokens[t]
        return token in auth_tokens

def _do_login(username, password):
    if username == AUTH_USER and password == AUTH_PASS:
        token = _gen_token()
        with auth_lock:
            auth_tokens[token] = {"user": username, "time": time.time()}
        log("登录成功: {}".format(username))
        return token
    log("登录失败: {}".format(username))
    return None

def _login_rate_check(ip):
    now = time.time()
    with login_lock:
        if ip not in login_attempts:
            login_attempts[ip] = {"count": 0, "first": now, "locked": 0}
        r = login_attempts[ip]
        if r["locked"] > now:
            return False, int(r["locked"] - now)
        if now - r["first"] > 60:
            r["count"] = 0
            r["first"] = now
        r["count"] += 1
        if r["count"] >= 5:
            r["locked"] = now + 300
            return False, 300
        return True, 0

def _client_ip(handler):
    fwd = handler.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return handler.client_address[0]
