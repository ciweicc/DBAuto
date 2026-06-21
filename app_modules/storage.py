# storage.py — JSON 文件读写（历史记录、执行历史）
import os, json, uuid, time
from datetime import datetime, timezone, timedelta
from threading import Lock
from config import HISTORY_FILE, EXEC_HISTORY_FILE, _ensure_dir

TZ = timezone(timedelta(hours=8))
_history_cache = None
_history_lock = Lock()
_exec_cache = None
_exec_lock = Lock()

def load_history():
    global _history_cache
    with _history_lock:
        if _history_cache is not None:
            return _history_cache
        _ensure_dir()
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                _history_cache = json.load(f)
        else:
            _history_cache = {}
        return _history_cache

def save_history(h):
    global _history_cache
    with _history_lock:
        _history_cache = h
        _ensure_dir()
        with open(HISTORY_FILE, "w") as f:
            json.dump(h, f, ensure_ascii=False, indent=2)

def load_exec_history():
    global _exec_cache
    with _exec_lock:
        if _exec_cache is not None:
            return _exec_cache
        _ensure_dir()
        if os.path.exists(EXEC_HISTORY_FILE):
            with open(EXEC_HISTORY_FILE, "r") as f:
                _exec_cache = json.load(f)
        else:
            _exec_cache = []
        return _exec_cache

def save_exec_history(data):
    global _exec_cache
    with _exec_lock:
        _exec_cache = data[-200:]
        _ensure_dir()
        with open(EXEC_HISTORY_FILE, "w") as f:
            json.dump(_exec_cache, f, ensure_ascii=False, indent=2)

def add_exec_record(typ, detail, status="ok"):
    data = load_exec_history()
    data.append({"id": uuid.uuid4().hex[:8], "type": typ, "detail": detail,
                 "status": status, "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")})
    save_exec_history(data)
