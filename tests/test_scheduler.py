import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from scheduler import _next_fire_time, _now_local
from datetime import timedelta


class TestNextFireTime:
    def test_interval_adds_hours_to_last_run(self):
        # 间隔模式：last_run + interval_hours
        nxt = _next_fire_time(None, None, 6, "2026-01-01 00:00:00")
        assert nxt.year == 2026 and nxt.month == 1 and nxt.day == 1 and nxt.hour == 6

    def test_time_str_same_day_when_future(self):
        now = _now_local()
        future = now + timedelta(hours=2)
        ts = "{}:{:02d}".format(future.hour, future.minute)
        # 按 _next_fire_time 的真实语义构造预期值：今天该时刻若已过则滚到明天，
        # 使断言与测试运行时刻无关（避免 22:00 后 now+2h 跨日导致的 flaky）。
        hh, mm = future.hour, future.minute
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        nxt = _next_fire_time(ts, None, 0, None)
        assert nxt is not None
        assert nxt == candidate

    def test_time_str_next_day_when_passed(self):
        now = _now_local()
        past = now - timedelta(hours=1)
        ts = "{}:{:02d}".format(past.hour, past.minute)
        nxt = _next_fire_time(ts, None, 0, None)
        assert nxt is not None
        assert nxt > now
        # 过去的时间点应滚动到明天
        assert (nxt - now).days >= 1 or (nxt.date() > now.date())

    def test_no_schedule_returns_none(self):
        assert _next_fire_time(None, None, 0, None) is None
