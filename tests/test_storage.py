import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import storage
from storage import (
    load_history, save_history, add_exec_record, update_exec_record,
    clear_exec_history, load_exec_history, upsert_history_item,
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

    def test_upsert_history_item_preserves_tmdb_id_in_cache(self):
        """回归测试：upsert_history_item 后内存热缓存须保留 tmdb_id。

        冷路径（load_history 直读 DB）能正确返回 tmdb_id，但历史缓存曾被
        upsert 重建时漏写 tmdb_id，导致依赖 load_history() 缓存命中的
        TMDB 去重逻辑拿不到 tmdb_id。
        """
        import uuid
        # 唯一标题，避免与其他测试/真实数据冲突，无需额外清理
        title = "P3_TEST_TITLE_{}".format(uuid.uuid4().hex)
        # 重置模块缓存状态，保证测试确定性且不污染全局缓存
        saved_cache = storage._history_cache
        storage._history_cache = None
        try:
            # 预热缓存：从 DB 载入，使 _history_cache 不再是 None（启用缓存命中路径）
            load_history()
            assert storage._history_cache is not None
            # 增量写入一条带 tmdb_id 的历史
            upsert_history_item(
                title,
                {"date": "2026-08-06", "status": "done", "category": "movie", "tmdb_id": "123456"},
            )
            # 再次读取走缓存命中路径（dict(_history_cache)）
            loaded = load_history()
            assert loaded[title]["tmdb_id"] == "123456"
        finally:
            storage._history_cache = saved_cache


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
