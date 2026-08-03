import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

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