# storage.py — SQLite 存储（历史记录、执行历史），兼容原有 JSON 数据自动迁移
import os, json, uuid, sqlite3
from datetime import datetime, timezone, timedelta
from threading import Lock, RLock
from config import DATA_DIR, HISTORY_FILE, EXEC_HISTORY_FILE, ensure_data_dir

TZ = timezone(timedelta(hours=8))

DB_FILE = os.path.join(DATA_DIR, "app.db")

_db_conn = None
_db_lock = RLock()

_history_cache = None
_history_lock = Lock()
_exec_cache = None
_exec_lock = Lock()
_exec_limit = 200


def _get_db():
    global _db_conn
    with _db_lock:
        if _db_conn is None:
            ensure_data_dir()
            _db_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            _db_conn.row_factory = sqlite3.Row
            _init_db()
            _migrate_from_json()
        return _db_conn


def _init_db():
    conn = _db_conn
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transfer_history (
            title TEXT PRIMARY KEY,
            date TEXT,
            status TEXT,
            category TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exec_history (
            id TEXT PRIMARY KEY,
            type TEXT,
            detail TEXT,
            status TEXT,
            time TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_time ON exec_history(time DESC)")
    conn.commit()


def _read_json_file(filepath, default):
    if not os.path.exists(filepath):
        return default
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return default


def _migrate_from_json():
    conn = _db_conn

    if os.path.exists(HISTORY_FILE):
        try:
            data = _read_json_file(HISTORY_FILE, {})
            if isinstance(data, dict) and data:
                count = conn.execute("SELECT COUNT(*) FROM transfer_history").fetchone()[0]
                if count == 0:
                    for title, info in data.items():
                        conn.execute(
                            "INSERT OR REPLACE INTO transfer_history (title, date, status, category) VALUES (?, ?, ?, ?)",
                            (title, info.get("date", ""), info.get("status", ""), info.get("category", ""))
                        )
                    conn.commit()
                    from utils import log
                    log("已迁移转存历史: {} 条".format(len(data)))
        except Exception as e:
            from utils import log
            log("迁移转存历史失败: {}".format(e))

    if os.path.exists(EXEC_HISTORY_FILE):
        try:
            data = _read_json_file(EXEC_HISTORY_FILE, [])
            if isinstance(data, list) and data:
                count = conn.execute("SELECT COUNT(*) FROM exec_history").fetchone()[0]
                if count == 0:
                    for item in data:
                        conn.execute(
                            "INSERT OR REPLACE INTO exec_history (id, type, detail, status, time) VALUES (?, ?, ?, ?, ?)",
                            (item.get("id", uuid.uuid4().hex[:8]),
                             item.get("type", ""),
                             item.get("detail", ""),
                             item.get("status", "ok"),
                             item.get("time", ""))
                        )
                    conn.commit()
                    from utils import log
                    log("已迁移执行历史: {} 条".format(len(data)))
        except Exception as e:
            from utils import log
            log("迁移执行历史失败: {}".format(e))


def load_history():
    global _history_cache
    with _history_lock:
        if _history_cache is not None:
            return _history_cache
        with _db_lock:
            conn = _get_db()
            rows = conn.execute("SELECT title, date, status, category FROM transfer_history").fetchall()
            result = {}
            for row in rows:
                result[row["title"]] = {
                    "date": row["date"],
                    "status": row["status"],
                    "category": row["category"]
                }
            _history_cache = result
            return _history_cache


def save_history(h):
    global _history_cache
    with _history_lock:
        _history_cache = dict(h)
        with _db_lock:
            conn = _get_db()
            conn.execute("DELETE FROM transfer_history")
            for title, info in h.items():
                conn.execute(
                    "INSERT OR REPLACE INTO transfer_history (title, date, status, category) VALUES (?, ?, ?, ?)",
                    (title, info.get("date", ""), info.get("status", ""), info.get("category", ""))
                )
            conn.commit()


def load_exec_history():
    global _exec_cache
    with _exec_lock:
        if _exec_cache is not None:
            return _exec_cache
        with _db_lock:
            conn = _get_db()
            rows = conn.execute(
                "SELECT id, type, detail, status, time FROM exec_history ORDER BY time DESC LIMIT ?",
                (_exec_limit,)
            ).fetchall()
            result = []
            for row in rows:
                result.append({
                    "id": row["id"],
                    "type": row["type"],
                    "detail": row["detail"],
                    "status": row["status"],
                    "time": row["time"]
                })
            _exec_cache = result
            return _exec_cache


def save_exec_history(data):
    global _exec_cache
    with _exec_lock:
        trimmed = data[-_exec_limit:] if len(data) > _exec_limit else list(data)
        _exec_cache = trimmed
        with _db_lock:
            conn = _get_db()
            conn.execute("DELETE FROM exec_history")
            for item in trimmed:
                conn.execute(
                    "INSERT OR REPLACE INTO exec_history (id, type, detail, status, time) VALUES (?, ?, ?, ?, ?)",
                    (item.get("id", uuid.uuid4().hex[:8]),
                     item.get("type", ""),
                     item.get("detail", ""),
                     item.get("status", "ok"),
                     item.get("time", ""))
                )
            conn.commit()


def add_exec_record(typ, detail, status="ok"):
    global _exec_cache
    record = {
        "id": uuid.uuid4().hex[:8],
        "type": typ,
        "detail": detail,
        "status": status,
        "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    }
    with _exec_lock:
        with _db_lock:
            conn = _get_db()
            conn.execute(
                "INSERT INTO exec_history (id, type, detail, status, time) VALUES (?, ?, ?, ?, ?)",
                (record["id"], record["type"], record["detail"], record["status"], record["time"])
            )
            conn.commit()
        if _exec_cache is not None:
            _exec_cache.insert(0, record)
            if len(_exec_cache) > _exec_limit:
                _exec_cache = _exec_cache[:_exec_limit]
