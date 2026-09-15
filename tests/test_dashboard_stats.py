"""仪表盘统计口径测试（issue #8）。

回归背景：
  执行历史（exec_history）是混合流水 —— `transfer`（转存）、`config`（改配置）、
  `expired_check`（失效检测）都写进同一张表。原实现用「排除 expired_check」的
  黑名单方式统计，导致**每次保存设置产生的 config 记录都被算作一次转存**：

  - `today_count` 虚高 → 概览页「今日转存」把改配置算成转存成功；
  - `week_total` 虚高 → 「N 次/周」同步虚高；
  - `config` 记录没有 `data`，只走 last_status 分支，
    把「上次成功」覆盖成「上次无有效结果」，并在待办里误报。

  修复方式：改为白名单（TRANSFER_RECORD_TYPES），新增记录类型默认不参与转存统计。
"""
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

os.environ["DATA_DIR"] = tempfile.mkdtemp()

import storage  # noqa: E402
import routes_history  # noqa: E402


def _clear():
    storage.clear_exec_history()


def _today():
    return time.strftime("%Y-%m-%d")


class TestTransferScope:
    """is_transfer_record：白名单口径"""

    def test_transfer_is_transfer(self):
        assert routes_history.is_transfer_record({"type": "transfer"})

    def test_config_is_not_transfer(self):
        assert not routes_history.is_transfer_record({"type": "config"})

    def test_expired_check_is_not_transfer(self):
        assert not routes_history.is_transfer_record({"type": "expired_check"})

    def test_unknown_type_is_not_transfer(self):
        # 白名单语义：将来新增的记录类型默认不计入转存统计
        assert not routes_history.is_transfer_record({"type": "backup"})

    def test_missing_type_is_not_transfer(self):
        assert not routes_history.is_transfer_record({})

    def test_non_dict_is_not_transfer(self):
        assert not routes_history.is_transfer_record(None)
        assert not routes_history.is_transfer_record("transfer")


class TestDashboardStatsScope:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def _one_transfer(self):
        return storage.add_exec_record(
            "transfer", "转存完成 成功2 失败0 跳过1", "ok",
            data={"ok": 2, "failed": 0, "skipped": 1})

    def test_config_records_not_counted_as_today_transfer(self):
        """核心回归：改配置不得计入「今日转存」"""
        self._one_transfer()
        storage.add_exec_record("config", "update config")
        storage.add_exec_record("config", "update config")
        stats = routes_history.compute_dashboard_stats()
        assert stats["today_count"] == 1, stats

    def test_config_records_not_counted_in_week_total(self):
        self._one_transfer()
        for _ in range(3):
            storage.add_exec_record("config", "update config")
        stats = routes_history.compute_dashboard_stats()
        assert stats["week_total"] == 1, stats

    def test_config_records_not_counted_in_daily(self):
        self._one_transfer()
        storage.add_exec_record("config", "update config")
        stats = routes_history.compute_dashboard_stats()
        today = _today()[5:]
        rec = [d for d in stats["daily"] if d["date"] == today][0]
        assert rec["total"] == 1, rec
        assert rec["ok"] == 2, rec

    def test_config_records_do_not_override_last_status(self):
        """改配置后「上次转存」仍应是真实转存结论，而不是 none"""
        self._one_transfer()
        storage.add_exec_record("config", "update config")
        stats = routes_history.compute_dashboard_stats()
        assert stats["last_status"] == "success", stats

    def test_expired_check_still_excluded(self):
        """原有行为不得回归：失效检测不参与转存统计"""
        self._one_transfer()
        storage.add_exec_record("expired_check", "检测完成，无失效链接", "ok", data={"expired": []})
        stats = routes_history.compute_dashboard_stats()
        assert stats["today_count"] == 1, stats
        assert stats["week_total"] == 1, stats

    def test_only_config_records_yields_empty_stats(self):
        """只有配置记录时，转存统计必须为空（而不是 0 次成功 + N 次/周）"""
        for _ in range(5):
            storage.add_exec_record("config", "update config")
        stats = routes_history.compute_dashboard_stats()
        assert stats["today_count"] == 0, stats
        assert stats["week_total"] == 0, stats
        assert stats["week_ok"] == 0, stats
        assert stats["last_status"] == "-", stats
        assert daily_sum(stats) == 0, stats

    def test_non_transfer_records_do_not_raise_on_missing_data(self):
        """config 记录的 data 为 None，统计路径必须能安全跳过"""
        storage.add_exec_record("config", "update config")   # data=None
        storage.add_exec_record("config", "update config", status="ok")
        stats = routes_history.compute_dashboard_stats()
        assert stats["week_total"] == 0

    def test_failed_transfer_still_reported(self):
        """真实失败转存仍要能被统计到（避免修成「什么都不过滤」）"""
        storage.add_exec_record("transfer", "转存完成 成功0 失败2 跳过0", "fail",
                                data={"ok": 0, "failed": 2, "skipped": 0})
        stats = routes_history.compute_dashboard_stats()
        assert stats["today_count"] == 1
        assert stats["week_fail"] == 2
        assert stats["last_status"] == "fail"


def daily_sum(stats):
    return sum(d["total"] for d in stats["daily"])
