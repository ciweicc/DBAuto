"""基于 TMDB id 的去重测试（P3）。

验证：
- 同一 TMDB id（不同译名/续集）判为已存在；
- 不同 TMDB id（如阿凡达 vs 阿凡达2）判为不同作品；
- 无 tmdb_id / tmdb_id=None 时优雅降级到标题规范化匹配；
- 历史索引正确收集 tmdb_id；
- 解析器带缓存且对失败/无 key 返回 None；
- storage 层可正确存取 tmdb_id。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app_modules"))
import transfer
from transfer import _find_in_history, _resolve_tmdb_id, _build_history_index


class TestTmdbDedup:
    def test_same_tmdb_id_dedup_across_titles(self):
        history = {"阿凡达 Avatar": {"status": "ok", "tmdb_id": "19995"}}
        idx = _build_history_index(history)
        # 不同译名但同一 TMDB id → 判已存在（覆盖标题规范化盲区）
        assert _find_in_history("AVATAR 阿凡达", history, idx, tmdb_id="19995") is True

    def test_same_tmdb_id_dedup_no_index(self):
        history = {"阿凡达 Avatar": {"status": "ok", "tmdb_id": "19995"}}
        assert _find_in_history("AVATAR 阿凡达", history, tmdb_id="19995") is True

    def test_diff_tmdb_id_not_dedup(self):
        history = {"阿凡达": {"status": "ok", "tmdb_id": "19995"}}
        idx = _build_history_index(history)
        # 续集：不同作品、不同 id → 不判重复（消除"阿凡达吞阿凡达2"）
        assert _find_in_history("阿凡达2 水之道", history, idx, tmdb_id="76600") is False

    def test_tmdb_id_none_falls_back_to_title(self):
        history = {"阿凡达": {"status": "ok"}}
        idx = _build_history_index(history)
        assert _find_in_history("阿凡达", history, idx, tmdb_id=None) is True
        assert _find_in_history("阿凡达2", history, idx, tmdb_id=None) is False

    def test_history_without_tmdb_id_not_caught_by_id(self):
        # 历史无 tmdb_id，任务带 id 但标题也不同 → 标题通道不命中
        history = {"某旧译名": {"status": "ok"}}
        idx = _build_history_index(history)
        assert _find_in_history("另一译名", history, idx, tmdb_id="19995") is False

    def test_build_index_collects_tmdb_ids(self):
        history = {
            "阿凡达": {"status": "ok", "tmdb_id": "19995"},
            "无id电影": {"status": "ok"},
        }
        idx = _build_history_index(history)
        assert 19995 in idx["tmdb_ids"]
        assert idx["tmdb_map"][19995] == "阿凡达"

    def test_resolve_tmdb_id_caches_and_handles_none(self, monkeypatch):
        calls = {"n": 0}

        def fake(query, media_type="movie", year=0):
            calls["n"] += 1
            return 12345 if "abc" in query else None

        monkeypatch.setattr(transfer, "search_tmdb_id", fake)
        transfer._tmdb_id_cache.clear()
        assert _resolve_tmdb_id("abc电影", "movie") == 12345
        assert _resolve_tmdb_id("abc电影", "movie") == 12345  # 命中缓存
        assert calls["n"] == 1  # 仅实际请求一次
        assert _resolve_tmdb_id("xyz电影", "movie") is None


class TestTmdbStorageRoundTrip:
    def test_upsert_and_load_tmdb_id(self):
        import storage
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        os.remove(tmp.name)
        old_db = storage.DB_FILE
        old_conn = storage._db_conn
        old_cache = storage._history_cache
        storage.DB_FILE = tmp.name
        storage._db_conn = None
        storage._history_cache = None
        try:
            storage.upsert_history_item(
                "阿凡达",
                {"date": "2026-08-03", "status": "ok", "category": "movie", "tmdb_id": "19995"},
            )
            hist = storage.load_history()
            assert hist["阿凡达"]["tmdb_id"] == "19995"
            assert hist["阿凡达"]["status"] == "ok"
        finally:
            storage._db_conn = old_conn
            storage._history_cache = old_cache
            storage.DB_FILE = old_db
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(tmp.name + suffix)
                except OSError:
                    pass
