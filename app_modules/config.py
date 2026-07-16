# config.py — 配置管理（ConfigManager 类 + 兼容层函数）
import os, json, time
from datetime import timezone, timedelta
from threading import Lock

DATA_DIR = os.environ.get("DATA_DIR", "/data/douban-history")
HISTORY_FILE = os.path.join(DATA_DIR, "transfer_history.json")
EXEC_HISTORY_FILE = os.path.join(DATA_DIR, "exec_history.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PORT = int(os.environ.get("PORT", "3001"))

# 时区：从 TZ 环境变量推断偏移量，默认东八区
_TZ_NAME = os.environ.get("TZ", "Asia/Shanghai")
_TZ_OFFSETS = {
    "Asia/Shanghai": 8, "Asia/Hong_Kong": 8, "Asia/Taipei": 8, "Asia/Singapore": 8,
    "Asia/Tokyo": 9, "Asia/Seoul": 9, "Asia/Bangkok": 7, "Asia/Jakarta": 7,
    "UTC": 0, "Europe/London": 0, "Europe/Paris": 1, "Europe/Berlin": 1,
    "US/Eastern": -5, "US/Central": -6, "US/Mountain": -7, "US/Pacific": -8,
}
_TZ_OFFSET = _TZ_OFFSETS.get(_TZ_NAME, 8)
LOCAL_TZ = timezone(timedelta(hours=_TZ_OFFSET))

_SENSITIVE_FIELDS = {"qas_token", "auth_pass", "douban_cookie"}


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
    "auth_user": os.environ.get("AUTH_USER", "root"),
    "auth_pass": os.environ.get("AUTH_PASS", ""),
    "douban_uid": os.environ.get("DOUBAN_UID", ""),
    "douban_cookie": os.environ.get("DOUBAN_COOKIE", ""),
}

DEFAULT_SETTINGS = {
    "transfer": {"enabled": False, "time": "02:00", "cron": "", "interval_hours": 0, "limit": 5, "tasks": [],
                 "filters": {"min_rating": 0, "sort_by": "rating", "year_from": 0, "year_to": 0,
                             "exclude_keywords": [], "genre": ""}},
    "expired_check": {"enabled": False, "time": "03:00", "cron": "", "interval_hours": 0,
                      "directories": [], "auto_fix": False},
    "douban_wish": {"enabled": False, "savepath": "/批量转存/想看", "category": "movie", "accounts": []},
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
    def auth_user(self):
        return self.get_config().get("auth_user", DEFAULT_CONFIG["auth_user"])

    @property
    def auth_pass(self):
        return self.get_config().get("auth_pass", DEFAULT_CONFIG["auth_pass"])

    @property
    def douban_uid(self):
        return self.get_config().get("douban_uid", DEFAULT_CONFIG["douban_uid"])

    @property
    def douban_cookie(self):
        return self.get_config().get("douban_cookie", DEFAULT_CONFIG["douban_cookie"])

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
                # 直接写入文件，不调用 set_config 以避免死锁（Lock 不可重入）
                file_data = dict(self._config)
                for k in _SENSITIVE_FIELDS:
                    if k in file_data:
                        file_data[k] = _encrypt_if_needed(k, file_data[k])
                from utils import atomic_write_json
                atomic_write_json(CONFIG_FILE, file_data)
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


# ===== 模块级函数接口（供其他模块使用）=====

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
