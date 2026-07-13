import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from storage import (
    load_history, save_history, add_exec_record, update_exec_record,
    clear_exec_history, load_exec_history,
)


class TestStorageHistory:
    def test_save_and_load_history(self):
        history = {
            "电影A": {"date": "2024-01-01", "status": "ok", "category": "movie"},
            "电影B": {"date": "2024-01-02", "status": "failed", "category": "movie"},
        }
        save_history(history)
        loaded = load_history()
        assert loaded["电影A"]["status"] == "ok"
        assert loaded["电影B"]["status"] == "failed"

    def test_update_history(self):
        history = {"电影C": {"date": "2024-01-01", "status": "ok", "category": "movie"}}
        save_history(history)
        loaded = load_history()
        loaded["电影C"]["status"] = "failed"
        save_history(loaded)
        loaded2 = load_history()
        assert loaded2["电影C"]["status"] == "failed"

    def test_delete_history_by_save(self):
        history = {
            "电影D": {"date": "2024-01-01", "status": "ok", "category": "movie"},
            "电影E": {"date": "2024-01-02", "status": "ok", "category": "movie"},
        }
        save_history(history)
        del history["电影D"]
        save_history(history)
        loaded = load_history()
        assert "电影D" not in loaded
        assert "电影E" in loaded


class TestStorageExecHistory:
    def test_add_and_load_exec_record(self):
        rec = add_exec_record("transfer", "test transfer", "running")
        assert rec["id"]
        assert rec["type"] == "transfer"
        assert rec["status"] == "running"
        data = load_exec_history()
        assert any(r["id"] == rec["id"] for r in data)

    def test_update_exec_record(self):
        rec = add_exec_record("transfer", "testing", "running")
        update_exec_record(rec["id"], detail="done", status="ok", data={"ok": 5})
        data = load_exec_history()
        found = [r for r in data if r["id"] == rec["id"]]
        assert found
        assert found[0]["detail"] == "done"
        assert found[0]["status"] == "ok"

    def test_clear_exec_history(self):
        add_exec_record("transfer", "temp", "ok")
        clear_exec_history()
        data = load_exec_history()
        assert len(data) == 0
