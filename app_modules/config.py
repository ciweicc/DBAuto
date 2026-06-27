# config.py — 配置管理（ConfigManager 类 + 兼容层函数）
import os, json, time
from threading import Lock

DATA_DIR = os.environ.get("DATA_DIR", "/data/douban-history")
HISTORY_FILE = os.path.join(DATA_DIR, "transfer_history.json")
EXEC_HISTORY_FILE = os.path.join(DATA_DIR, "exec_history.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PORT = int(os.environ.get("PORT", "3001"))

_SENSITIVE_FIELDS = {"qas_token", "openlist_token", "auth_pass"}


def _is_encrypted(val):
    return isinstance(val, str) and val.startswith("$enc$")


def _encrypt_if_needed(key, val):
    if key in _SENSITIVE_FIELDS and val and not _is_encrypted(val):
        from utils import encrypt_secret
        return "$enc$" + encrypt_secret(val)
    return val


def _decrypt_if_needed(key, val):
    if key in _SENSITIVE_FIELDS and _is_encrypted(val):
        from utils import decrypt_secret
        return decrypt_secret(val[5:])
    return val


def _read_json(filepath, default):
    if not os.path.exists(filepath):
        return default
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return default


DEFAULT_CONFIG = {
    "pansou": os.environ.get("PANSOU", "http://192.168.1.1:8080"),
    "qas": os.environ.get("QAS", "http://192.168.1.1:5005"),
    "qas_token": os.environ.get("QAS_TOKEN", ""),
    "openlist_url": os.environ.get("OPENLIST_URL", "http://192.168.1.1:5244"),
    "openlist_token": os.environ.get("OPENLIST_TOKEN", ""),
    "openlist_base_path": os.environ.get("OPENLIST_BASE_PATH", ""),
    "auth_user": os.environ.get("AUTH_USER", "root"),
    "auth_pass": os.environ.get("AUTH_PASS", ""),
}

DEFAULT_SETTINGS = {
    "transfer": {"enabled": False, "time": "02:00", "cron": "", "limit": 5, "tasks": []},
    "expired_check": {"enabled": False, "time": "03:00", "cron": "", "directories": []},
    "dir_cleanup": {"enabled": False, "time": "06:00", "cron": "", "directories": []},
}

CATEGORIES = {
    "movie": {"name": "电影", "icon": "🎬", "subs": {
        "热门电影": {"path": "movie/hot", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
        "最新电影": {"path": "movie/latest", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
        "豆瓣高分": {"path": "movie/top", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
        "冷门佳片": {"path": "movie/underrated", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
    }},
    "tv": {"name": "电视剧", "icon": "📺", "subs": {
        "热门剧集": {"path": "tv/drama", "types": ["综合", "国产剧", "欧美剧", "日剧", "韩剧", "动画", "纪录片"]},
    }},
    "variety": {"name": "综艺", "icon": "🎭", "subs": {
        "热门综艺": {"path": "tv/variety", "types": ["综合", "国内", "国外"]},
    }},
}


class ConfigManager:
    """统一管理系统配置和调度设置，线程安全"""

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
        self._config = None
        self._settings = None
        self._config_lock = Lock()
        self._settings_lock = Lock()
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    @property
    def pansou(self):
        return self.get_config().get("pansou", DEFAULT_CONFIG["pansou"])

    @property
    def qas(self):
        return self.get_config().get("qas", DEFAULT_CONFIG["qas"])

    @property
    def qas_token(self):
        return self.get_config().get("qas_token", DEFAULT_CONFIG["qas_token"])

    @property
    def openlist_url(self):
        return self.get_config().get("openlist_url", DEFAULT_CONFIG["openlist_url"])

    @property
    def openlist_token(self):
        return self.get_config().get("openlist_token", DEFAULT_CONFIG["openlist_token"])

    @property
    def openlist_base_path(self):
        return self.get_config().get("openlist_base_path", DEFAULT_CONFIG["openlist_base_path"])

    @property
    def auth_user(self):
        return self.get_config().get("auth_user", DEFAULT_CONFIG["auth_user"])

    @property
    def auth_pass(self):
        return self.get_config().get("auth_pass", DEFAULT_CONFIG["auth_pass"])

    def get_config(self):
        with self._config_lock:
            if self._config is not None:
                return self._config
            self._ensure_data_dir()
            cfg = _read_json(CONFIG_FILE, None)
            if cfg is not None:
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
                for k in _SENSITIVE_FIELDS:
                    if k in cfg:
                        cfg[k] = _decrypt_if_needed(k, cfg[k])
                self._config = cfg
            else:
                self._config = dict(DEFAULT_CONFIG)
            if not os.path.exists(CONFIG_FILE):
                self.set_config(self._config)
            return self._config

    def set_config(self, cfg):
        data = {k: v for k, v in cfg.items() if k in DEFAULT_CONFIG}
        file_data = dict(data)
        for k in _SENSITIVE_FIELDS:
            if k in file_data:
                file_data[k] = _encrypt_if_needed(k, file_data[k])
        with self._config_lock:
            self._config = data
            self._ensure_data_dir()
            from utils import atomic_write_json
            atomic_write_json(CONFIG_FILE, file_data)

    def get_settings(self):
        with self._settings_lock:
            if self._settings is not None:
                return self._settings
            self._ensure_data_dir()
            self._settings = _read_json(SETTINGS_FILE, None)
            if self._settings is None:
                self._settings = dict(DEFAULT_SETTINGS)
            for k, v in DEFAULT_SETTINGS.items():
                if k not in self._settings:
                    self._settings[k] = dict(v)
            if not os.path.exists(SETTINGS_FILE):
                from utils import atomic_write_json
                atomic_write_json(SETTINGS_FILE, self._settings)
            return self._settings

    def set_settings(self, settings):
        with self._settings_lock:
            self._settings = settings
            self._ensure_data_dir()
            from utils import atomic_write_json
            atomic_write_json(SETTINGS_FILE, settings)

    def reload(self):
        with self._config_lock:
            self._config = None
        with self._settings_lock:
            self._settings = None


# ===== 兼容层：保持原有模块级变量和函数 =====

_config_manager = None


def _get_config_manager():
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager.get_instance()
    return _config_manager


def ensure_data_dir():
    _get_config_manager()._ensure_data_dir()


def load_config():
    return _get_config_manager().get_config()


def save_config(cfg):
    _get_config_manager().set_config(cfg)


def load_settings():
    return _get_config_manager().get_settings()


def save_settings(settings):
    _get_config_manager().set_settings(settings)


# 模块级属性（动态从 ConfigManager 获取，始终最新）
class _ConfigProxy:
    def __getattr__(self, name):
        if name in ("PANSOU", "pansou"):
            return _get_config_manager().pansou
        if name in ("QAS", "qas"):
            return _get_config_manager().qas
        if name in ("QAS_TOKEN", "qas_token"):
            return _get_config_manager().qas_token
        if name in ("OPENLIST_URL", "openlist_url"):
            return _get_config_manager().openlist_url
        if name in ("OPENLIST_TOKEN", "openlist_token"):
            return _get_config_manager().openlist_token
        if name in ("OPENLIST_BASE_PATH", "openlist_base_path"):
            return _get_config_manager().openlist_base_path
        if name in ("AUTH_USER", "auth_user"):
            return _get_config_manager().auth_user
        if name in ("AUTH_PASS", "auth_pass"):
            return _get_config_manager().auth_pass
        raise AttributeError(name)


PANSOU = DEFAULT_CONFIG["pansou"]
QAS = DEFAULT_CONFIG["qas"]
QAS_TOKEN = DEFAULT_CONFIG["qas_token"]
OPENLIST_URL = DEFAULT_CONFIG["openlist_url"]
OPENLIST_TOKEN = DEFAULT_CONFIG["openlist_token"]
OPENLIST_BASE_PATH = DEFAULT_CONFIG["openlist_base_path"]
AUTH_USER = DEFAULT_CONFIG["auth_user"]
AUTH_PASS = DEFAULT_CONFIG["auth_pass"]
