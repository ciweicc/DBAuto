import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import unittest
from unittest.mock import patch

import transfer
from transfer import _find_in_history

class TestTransfer:
    def test_find_in_history_exact_match(self):
        history = {"电影A": {"status": "ok"}}
        assert _find_in_history("电影A", history) == True

    def test_find_in_history_no_match(self):
        history = {"电影A": {"status": "ok"}}
        assert _find_in_history("电影B", history) == False

    def test_find_in_history_similar_titles(self):
        # P0 优化：续集应视为不同作品，避免"短名吞长名"误判
        history = {"电影A": {"status": "ok"}}
        assert _find_in_history("电影A续集", history) == False

    def test_find_in_history_partial_match_long(self):
        history = {"复仇者联盟4：终局之战": {"status": "ok"}}
        assert _find_in_history("复仇者联盟4", history) == True
        assert _find_in_history("终局之战", history) == True

    def test_find_in_history_partial_match_short(self):
        history = {"盗梦空间": {"status": "ok"}}
        assert _find_in_history("盗梦", history) == False
        assert _find_in_history("空间", history) == False

    def test_find_in_history_case_insensitive(self):
        history = {"Inception": {"status": "ok"}}
        assert _find_in_history("inception", history) == True

    def test_find_in_history_remove_special_chars(self):
        history = {"电影 A (2024)": {"status": "ok"}}
        assert _find_in_history("电影A2024", history) == True

    def test_find_in_history_sequel_number_not_matched(self):
        # P0 回归：片名 + 单个数字差异（不同电影）不应被判为已存在
        history = {"阿凡达2": {"status": "ok"}}
        assert _find_in_history("阿凡达", history) == False
        history2 = {"速度与激情9": {"status": "ok"}}
        assert _find_in_history("速度与激情", history2) == False

    def test_find_in_history_same_work_diff_resolution(self):
        # 同一作品、仅分辨率/年份写法不同，应判为已存在
        history = {"电影A2024": {"status": "ok"}}
        assert _find_in_history("电影 A (2024) 1080p", history) == True


class TestTransferWorker(unittest.TestCase):
    """针对 run_transfer → _do_transfer 的 limit 名额回退逻辑回归测试。

    通过 mock 掉 PanSou 搜索 / 链接校验 / QAS 转存等外部依赖，离线驱动
    run_transfer，锁定「未找到 / 链接失效」两类分支都必须回退 transferred
    名额——这正是新增失效链接过滤补丁的关键不变量，避免 limit 被空耗。
    """

    def _run_with_mocks(self, task_list, limit, search_map, valid_map, add_status="ok"):
        def fake_search(title):
            return search_map.get(title, [])
        def fake_validate(url, *a, **k):
            return valid_map.get(url, (True, ""))
        def fake_add(*a, **k):
            return {"status": add_status, "msg": "转存成功"}

        with patch.object(transfer, "search_pansou", side_effect=fake_search), \
             patch.object(transfer, "validate_share_link", side_effect=fake_validate), \
             patch.object(transfer, "add_and_run", side_effect=fake_add), \
             patch.object(transfer, "load_history", return_value={}), \
             patch.object(transfer, "_resolve_tmdb_id", return_value=None), \
             patch.object(transfer, "upsert_history_item"), \
             patch.object(transfer, "sse_broadcast"), \
             patch.object(transfer, "clear_progress"), \
             patch.object(transfer, "log"), \
             patch.object(transfer, "add_exec_record", return_value={"id": "x"}), \
             patch.object(transfer, "update_exec_record"), \
             patch.object(transfer, "time") as mtime:
            mtime.sleep = lambda *a, **k: None  # 跳过每任务 3s 休眠，加速测试
            transfer.run_transfer(task_list, limit)
        return transfer.transfer_status

    def _collect(self, st):
        return {r["title"]: r["status"] for r in st["summary"]["results"]}

    def test_do_transfer_not_found_is_skipped(self):
        st = self._run_with_mocks(
            [{"title": "没搜到", "savepath": "/电影", "category": "movie"}],
            limit=10, search_map={"没搜到": []}, valid_map={})
        results = self._collect(st)
        self.assertEqual(results["没搜到"], "not_found")
        self.assertEqual(st["stats"]["skipped"], 1)
        self.assertEqual(st["stats"]["failed"], 0)

    def test_do_transfer_expired_is_skipped(self):
        url = "https://pan.quark.cn/s/expired"
        st = self._run_with_mocks(
            [{"title": "失效片", "savepath": "/电影", "category": "movie"}],
            limit=10,
            search_map={"失效片": [{"title": "失效片", "url": url, "source": "夸克网盘"}]},
            valid_map={url: (False, "链接已失效")})
        results = self._collect(st)
        self.assertEqual(results["失效片"], "expired")
        self.assertEqual(st["stats"]["skipped"], 1)
        self.assertEqual(st["stats"]["failed"], 0)

    def test_do_transfer_valid_link_succeeds(self):
        url = "https://pan.quark.cn/s/ok"
        st = self._run_with_mocks(
            [{"title": "好片", "savepath": "/电影", "category": "movie"}],
            limit=10,
            search_map={"好片": [{"title": "好片", "url": url, "source": "夸克网盘"}]},
            valid_map={url: (True, "")})
        results = self._collect(st)
        self.assertIn(results["好片"], ("ok", "done"))
        self.assertEqual(st["stats"]["ok"], 1)

    def test_do_transfer_expired_frees_limit_for_next(self):
        # 关键回归：expired 分支必须回退 transferred 名额，否则 limit=1 时
        # 紧随其后的有效任务会被 limit 拦截（ok 计数变 0）。
        expired_url = "https://pan.quark.cn/s/expired"
        ok_url = "https://pan.quark.cn/s/ok"
        st = self._run_with_mocks(
            [
                {"title": "失效片", "savepath": "/电影", "category": "movie"},
                {"title": "好片", "savepath": "/电影", "category": "movie"},
            ],
            limit=1,
            search_map={
                "失效片": [{"title": "失效片", "url": expired_url, "source": "夸克网盘"}],
                "好片": [{"title": "好片", "url": ok_url, "source": "夸克网盘"}],
            },
            valid_map={expired_url: (False, "链接已失效"), ok_url: (True, "")})
        results = self._collect(st)
        self.assertEqual(results["失效片"], "expired")
        self.assertIn(results["好片"], ("ok", "done"))
        self.assertEqual(st["stats"]["ok"], 1)
        self.assertEqual(st["stats"]["skipped"], 1)
        self.assertEqual(st["stats"]["failed"], 0)