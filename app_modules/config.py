# config.py — 环境变量、默认配置、持久化加载/保存
import os, json, time
from threading import Lock

DATA_DIR = "/data/douban-history"
HISTORY_FILE = os.path.join(DATA_DIR, "transfer_history.json")
EXEC_HISTORY_FILE = os.path.join(DATA_DIR, "exec_history.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PORT = int(os.environ.get("PORT", "3001"))

PANSOU = os.environ.get("PANSOU", "http://192.168.1.1:8080")
QAS = os.environ.get("QAS", "http://192.168.1.1:5005")
QAS_TOKEN = os.environ.get("QAS_TOKEN", "")
OPENLIST_URL = os.environ.get("OPENLIST_URL", "http://192.168.1.1:5244")
OPENLIST_TOKEN = os.environ.get("OPENLIST_TOKEN", "")
OPENLIST_BASE_PATH = os.environ.get("OPENLIST_BASE_PATH", "")
AUTH_USER = os.environ.get("AUTH_USER", "root")
AUTH_PASS = os.environ.get("AUTH_PASS", "")

DEFAULT_CONFIG = {
    "pansou": PANSOU, "qas": QAS, "qas_token": QAS_TOKEN,
    "openlist_url": OPENLIST_URL, "openlist_token": OPENLIST_TOKEN,
    "openlist_base_path": OPENLIST_BASE_PATH,
    "auth_user": AUTH_USER, "auth_pass": AUTH_PASS,
}

DEFAULT_SETTINGS = {
    "transfer": {"enabled": False, "time": "02:00", "cron": "", "limit": 5, "tasks": []},
    "expired_check": {"enabled": False, "time": "03:00", "cron": ""},
    "dir_cleanup": {"enabled": False, "time": "06:00", "cron": "", "directories": []},
}

CATEGORIES = {
    "movie": {"name": "电影", "icon": "🎬", "subs": {
        "热门电影": {"path": "movie/hot", "types": ["全部","华语","欧美","韩国","日本"]},
        "最新电影": {"path": "movie/latest", "types": ["全部","华语","欧美","韩国","日本"]},
        "豆瓣高分": {"path": "movie/top", "types": ["全部","华语","欧美","韩国","日本"]},
        "冷门佳片": {"path": "movie/underrated", "types": ["全部","华语","欧美","韩国","日本"]},
    }},
    "tv": {"name": "电视剧", "icon": "📺", "subs": {
        "热门剧集": {"path": "tv/drama", "types": ["综合","国产剧","欧美剧","日剧","韩剧","动画","纪录片"]},
    }},
    "variety": {"name": "综艺", "icon": "🎭", "subs": {
        "热门综艺": {"path": "tv/variety", "types": ["综合","国内","国外"]},
    }},
}

_config_cache = None
_config_lock = Lock()

def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_config():
    global _config_cache, PANSOU, QAS, QAS_TOKEN, OPENLIST_URL, OPENLIST_TOKEN, OPENLIST_BASE_PATH, AUTH_USER, AUTH_PASS
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
        _ensure_dir()
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            _config_cache = cfg
        else:
            _config_cache = dict(DEFAULT_CONFIG)
        PANSOU = _config_cache.get("pansou", PANSOU)
        QAS = _config_cache.get("qas", QAS)
        QAS_TOKEN = _config_cache.get("qas_token", QAS_TOKEN)
        OPENLIST_URL = _config_cache.get("openlist_url", OPENLIST_URL)
        OPENLIST_TOKEN = _config_cache.get("openlist_token", OPENLIST_TOKEN)
        OPENLIST_BASE_PATH = _config_cache.get("openlist_base_path", OPENLIST_BASE_PATH)
        AUTH_USER = _config_cache.get("auth_user", AUTH_USER)
        AUTH_PASS = _config_cache.get("auth_pass", AUTH_PASS)
        return _config_cache

def save_config(cfg):
    global _config_cache, PANSOU, QAS, QAS_TOKEN, OPENLIST_URL, OPENLIST_TOKEN, OPENLIST_BASE_PATH, AUTH_USER, AUTH_PASS
    data = {k: v for k, v in cfg.items() if k in DEFAULT_CONFIG}
    with _config_lock:
        _config_cache = data
        _ensure_dir()
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    PANSOU = data.get("pansou", PANSOU)
    QAS = data.get("qas", QAS)
    QAS_TOKEN = data.get("qas_token", QAS_TOKEN)
    OPENLIST_URL = data.get("openlist_url", OPENLIST_URL)
    OPENLIST_TOKEN = data.get("openlist_token", OPENLIST_TOKEN)
    OPENLIST_BASE_PATH = data.get("openlist_base_path", OPENLIST_BASE_PATH)
    AUTH_USER = data.get("auth_user", AUTH_USER)
    AUTH_PASS = data.get("auth_pass", AUTH_PASS)

_settings_cache = None
_settings_lock = Lock()

def load_settings():
    global _settings_cache
    with _settings_lock:
        if _settings_cache is not None:
            return _settings_cache
        _ensure_dir()
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                _settings_cache = json.load(f)
        else:
            _settings_cache = dict(DEFAULT_SETTINGS)
        for k, v in DEFAULT_SETTINGS.items():
            if k not in _settings_cache:
                _settings_cache[k] = dict(v)
        return _settings_cache

def save_settings(settings):
    global _settings_cache
    with _settings_lock:
        _settings_cache = settings
        _ensure_dir()
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
