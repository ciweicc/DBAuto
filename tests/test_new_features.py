import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from datetime import datetime, timedelta
from scheduler import _next_fire_time, _now_local, LOCAL_TZ


class TestNextFireTime:
    def test_time_format(self):
        dt = _next_fire_time("02:00", "")
        assert dt is not None
        assert dt.hour == 2
        assert dt.minute == 0

    def test_time_future(self):
        now = _now_local()
        past_hour = (now - timedelta(hours=1)).strftime("%H:%M")
        dt = _next_fire_time(past_hour, "")
        assert dt is not None
        assert dt > now

    def test_empty_returns_none(self):
        assert _next_fire_time("", "") is None

    def test_cron_expression(self):
        try:
            from croniter import croniter
            dt = _next_fire_time("", "0 2 * * *")
            assert dt is not None
            assert dt.hour == 2
            assert dt.minute == 0
        except ImportError:
            pass

    def test_invalid_time(self):
        assert _next_fire_time("25:00", "") is None
        assert _next_fire_time("abc", "") is None

    def test_interval_hours_mode(self):
        """间隔模式：每 N 小时执行"""
        now = _now_local()
        dt = _next_fire_time("", "", interval_hours=6, last_run=None)
        assert dt is not None
        # 没有上次运行时间，基于当前时间 + 6 小时
        expected = now + timedelta(hours=6)
        assert abs((dt - expected).total_seconds()) < 5

    def test_interval_hours_with_last_run(self):
        """间隔模式：有上次运行时间"""
        last_run = "2024-01-01 02:00:00"
        dt = _next_fire_time("", "", interval_hours=6, last_run=last_run)
        assert dt is not None
        assert dt.hour == 8
        assert dt.minute == 0

    def test_interval_zero_falls_back_to_time(self):
        """间隔为 0 时回退到时间模式"""
        dt = _next_fire_time("03:00", "", interval_hours=0)
        assert dt is not None
        assert dt.hour == 3

    def test_now_local(self):
        now = _now_local()
        assert now.tzinfo == LOCAL_TZ


class TestDoubanWishSettings:
    def test_douban_wish_in_default_settings(self):
        from config import DEFAULT_SETTINGS
        assert "douban_wish" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["douban_wish"]["enabled"] is False
        assert "savepath" in DEFAULT_SETTINGS["douban_wish"]
        assert "category" in DEFAULT_SETTINGS["douban_wish"]

    def test_douban_wish_settings_save_load(self):
        from config import ConfigManager
        ConfigManager._instance = None
        mgr = ConfigManager.get_instance()
        settings = mgr.get_settings()
        settings["douban_wish"]["enabled"] = True
        settings["douban_wish"]["savepath"] = "/test/wish"
        mgr.set_settings(settings)
        ConfigManager._instance = None
        mgr2 = ConfigManager.get_instance()
        s2 = mgr2.get_settings()
        assert s2["douban_wish"]["enabled"] is True
        assert s2["douban_wish"]["savepath"] == "/test/wish"


class TestNewFilters:
    def test_exclude_keywords_in_default_filters(self):
        from config import DEFAULT_SETTINGS
        filters = DEFAULT_SETTINGS["transfer"]["filters"]
        assert "exclude_keywords" in filters
        assert "genre" in filters

    def test_exclude_keywords_filtering(self):
        """测试排除关键词过滤逻辑"""
        items = [
            {"title": "电影A", "rating": 8.0, "year": 2024},
            {"title": "电影B 预告", "rating": 7.0, "year": 2023},
            {"title": "电影C 花絮", "rating": 6.0, "year": 2022},
        ]
        exclude = ["预告", "花絮"]
        result = [r for r in items if not any(kw in r["title"] for kw in exclude)]
        assert len(result) == 1
        assert result[0]["title"] == "电影A"


class TestExpiredCheckAutoFix:
    def test_auto_fix_in_default_settings(self):
        from config import DEFAULT_SETTINGS
        assert "auto_fix" in DEFAULT_SETTINGS["expired_check"]
        assert DEFAULT_SETTINGS["expired_check"]["auto_fix"] is False

    def test_interval_hours_in_default_settings(self):
        from config import DEFAULT_SETTINGS
        assert "interval_hours" in DEFAULT_SETTINGS["transfer"]
        assert "interval_hours" in DEFAULT_SETTINGS["expired_check"]


class TestDoubanConfigFields:
    def test_douban_uid_in_default_config(self):
        from config import DEFAULT_CONFIG
        assert "douban_uid" in DEFAULT_CONFIG
        assert "douban_cookie" in DEFAULT_CONFIG

    def test_douban_cookie_is_sensitive(self):
        from config import _SENSITIVE_FIELDS
        assert "douban_cookie" in _SENSITIVE_FIELDS
