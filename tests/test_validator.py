import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

from validator import (
    validate_string, validate_url, validate_cron,
    validate_time, validate_positive_int, validate_list, validate_task
)

class TestValidator:
    def test_validate_string(self):
        assert validate_string("hello") == (True, "")
        assert validate_string("", allow_empty=True) == (True, "")
        assert validate_string("", allow_empty=False) == (False, "cannot be empty")
        assert validate_string(123) == (False, "must be string")
        assert validate_string("a", min_len=2) == (False, "too short (min 2)")
        assert validate_string("a" * 501) == (False, "too long (max 500)")

    def test_validate_url(self):
        assert validate_url("http://example.com") == (True, "")
        assert validate_url("https://example.com/path") == (True, "")
        assert validate_url("not-a-url") == (False, "invalid url format")
        assert validate_url(123) == (False, "url must be string")
        assert validate_url("") == (False, "url is required")
        assert validate_url("", required=False) == (True, "")

    def test_validate_cron(self):
        assert validate_cron("0 2 * * *") == (True, "")
        assert validate_cron("30 3 * * 0") == (True, "")
        assert validate_cron("* * * * *") == (True, "")
        assert validate_cron("0 2") == (False, "cron must have 5 fields")
        assert validate_cron("") == (True, "")
        assert validate_cron("", required=True) == (False, "cron is required")

    def test_validate_time(self):
        assert validate_time("02:00") == (True, "")
        assert validate_time("14:30") == (True, "")
        assert validate_time("25:00") == (False, "invalid time (HH:MM)")
        assert validate_time("00:60") == (False, "invalid time (HH:MM)")
        assert validate_time("02:0") == (False, "time must be HH:MM format")
        assert validate_time("") == (True, "")

    def test_validate_positive_int(self):
        assert validate_positive_int(5) == (True, "")
        assert validate_positive_int("5") == (True, "")
        assert validate_positive_int(0) == (False, "must be >= 1")
        assert validate_positive_int(-1) == (False, "must be >= 1")
        assert validate_positive_int(100, max_val=50) == (False, "must be <= 50")
        assert validate_positive_int("abc") == (False, "must be positive integer")
        assert validate_positive_int(None) == (False, "value is required")

    def test_validate_list(self):
        assert validate_list([1, 2, 3]) == (True, "")
        assert validate_list([]) == (True, "")
        assert validate_list([], min_len=1) == (False, "list too short (min 1)")
        assert validate_list([1] * 1001) == (False, "list too long (max 1000)")
        assert validate_list("not a list") == (False, "must be list")

    def test_validate_task(self):
        task = {"path": "movie/hot", "type": "全部", "savepath": "/movies"}
        assert validate_task(task) == (True, "")
        task_missing = {"path": "movie/hot", "type": "全部"}
        assert validate_task(task_missing) == (False, "missing required key: savepath")
        task_empty = {"path": "", "type": "全部", "savepath": "/movies"}
        assert "path:" in validate_task(task_empty)[1]