# auth.py — 认证管理
import os, secrets, time
from threading import Lock
from config import ConfigManager
from utils import log, verify_password, hash_password

TOKEN_TTL = 86400
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW = 60
LOGIN_LOCK_DURATION = 300

_TRUST_PROXY = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")


class AuthManager:
    """认证管理器：token 管理、登录频率限制、密码验证"""

    _instance = None
    _instance_lock = Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._config = ConfigManager.get_instance()
        self._tokens = {}
        self._token_lock = Lock()
        self._login_attempts = {}
        self._login_lock = Lock()

    def _gen_token(self):
        return secrets.token_hex(16)

    def extract_token(self, handler):
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
            except Exception as e:
                log("解析 token 查询参数失败: {}".format(e))
        return token

    def check_auth(self, handler):
        token = self.extract_token(handler)
        now = time.time()
        with self._token_lock:
            expired = [t for t, v in self._tokens.items() if now - v["time"] > TOKEN_TTL]
            for t in expired:
                del self._tokens[t]
            return token in self._tokens

    def login(self, username, password):
        if username == self._config.auth_user and self._verify_pass(password, self._config.auth_pass):
            token = self._gen_token()
            with self._token_lock:
                self._tokens[token] = {"user": username, "time": time.time()}
            log("登录成功: {}".format(username))
            return token
        log("登录失败: {}".format(username))
        return None

    def _verify_pass(self, password, stored):
        if not stored:
            return not password
        if stored.startswith("$pbkdf2$"):
            return verify_password(password, stored[8:])
        # 兼容旧版明文密码：验证后自动迁移为哈希格式
        if password == stored:
            self._migrate_password(password)
            return True
        return False

    def _migrate_password(self, password):
        """将旧版明文密码自动迁移为 PBKDF2 哈希"""
        try:
            cfg = self._config.get_config()
            cfg["auth_pass"] = self.hash_password(password)
            self._config.set_config(cfg)
            log("已自动将旧版明文密码迁移为哈希格式")
        except Exception as e:
            log("密码迁移失败: {}".format(e))

    @staticmethod
    def hash_password(password):
        if not password:
            return ""
        return "$pbkdf2$" + hash_password(password)

    def check_login_rate(self, ip):
        now = time.time()
        with self._login_lock:
            if ip not in self._login_attempts:
                self._login_attempts[ip] = {"count": 0, "first": now, "locked": 0}
            r = self._login_attempts[ip]
            if r["locked"] > now:
                return False, int(r["locked"] - now)
            if now - r["first"] > LOGIN_WINDOW:
                r["count"] = 0
                r["first"] = now
            r["count"] += 1
            if r["count"] >= LOGIN_MAX_ATTEMPTS:
                r["locked"] = now + LOGIN_LOCK_DURATION
                return False, LOGIN_LOCK_DURATION
            return True, 0

    def cleanup_expired_attempts(self):
        now = time.time()
        with self._login_lock:
            expired = [ip for ip, v in self._login_attempts.items()
                       if v["locked"] < now and now - v["first"] > LOGIN_WINDOW * 2]
            for ip in expired:
                del self._login_attempts[ip]

    @staticmethod
    def get_client_ip(handler):
        # 信任反向代理时从 X-Forwarded-For 获取真实 IP
        if _TRUST_PROXY:
            xff = handler.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[0].strip()
        return handler.client_address[0]


# ===== 模块级函数接口（供路由模块使用）=====

_auth_manager = None


def _get_auth_manager():
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager.get_instance()
    return _auth_manager


def _check_auth(handler):
    return _get_auth_manager().check_auth(handler)


def _do_login(username, password):
    return _get_auth_manager().login(username, password)


def hash_auth_password(password):
    return AuthManager.hash_password(password)


def _login_rate_check(ip):
    return _get_auth_manager().check_login_rate(ip)


def _client_ip(handler):
    return AuthManager.get_client_ip(handler)
