import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from datetime import datetime, timedelta
from scheduler import _next_fire_time, _now_local, LOCAL_TZ


class TestNextFireTime:
    def test_time_format(self):
        """测试 HH:MM 格式的下次触发时间"""
        dt = _next_fire_time("02:00", "")
        assert dt is not None
        assert dt.hour == 2
        assert dt.minute == 0

    def test_time_future(self):
        """如果今天的时间已过，应该返回明天"""
        now = _now_local()
        past_hour = (now - timedelta(hours=1)).strftime("%H:%M")
        dt = _next_fire_time(past_hour, "")
        assert dt is not None
        assert dt > now

    def test_empty_returns_none(self):
        """空时间和 cron 返回 None"""
        assert _next_fire_time("", "") is None

    def test_cron_expression(self):
        """测试 Cron 表达式"""
        try:
            from croniter import croniter
            dt = _next_fire_time("", "0 2 * * *")
            assert dt is not None
            assert dt.hour == 2
            assert dt.minute == 0
        except ImportError:
            pass  # croniter 未安装时跳过

    def test_invalid_time(self):
        """无效时间格式返回 None"""
        assert _next_fire_time("25:00", "") is None
        assert _next_fire_time("abc", "") is None


class TestNowLocal:
    def test_now_local(self):
        now = _now_local()
        assert now.tzinfo == LOCAL_TZ
