"""P1 任务状态子系统测试：task_id 隔离、类型感知、排队续跑、注册表快照。"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import transfer


def _reset_status():
    transfer.transfer_status = {
        "running": False, "summary": None, "start_time": None,
        "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0},
        "thread_id": None, "stop": False,
    }
    with transfer._tasks_lock:
        transfer.transfer_tasks.clear()
    with transfer._scheduled_queue_lock:
        transfer._scheduled_queue.clear()


class TestStatusSubsystem:
    def setup_method(self):
        _reset_status()

    def test_transfer_status_identity_stable(self):
        # 路由层按值导入 transfer_status，P1 绝不能重新赋值它
        original = transfer.transfer_status
        transfer.transfer_status["task_id"] = "abc"
        transfer.transfer_status["run_type"] = "transfer"
        transfer.transfer_status["running"] = True
        transfer._register_running_task("abc")
        assert transfer.transfer_status is original
        # 进行中任务引用同一对象（实时观测）
        assert transfer.transfer_tasks["abc"] is original

    def test_is_transfer_running_true_with_alive_thread(self):
        transfer.transfer_status["running"] = True
        ev = threading.Event()
        def worker():
            ev.wait(0.3)
        t = threading.Thread(target=worker)
        t.start()
        transfer.transfer_status["thread_id"] = t.ident
        transfer.transfer_status["run_type"] = "transfer"
        try:
            assert transfer.is_transfer_running() is True
            assert transfer.is_transfer_running("transfer") is True
            assert transfer.is_transfer_running("fix_expired") is False
        finally:
            ev.set()
            t.join()

    def test_is_transfer_running_false_when_dead_thread(self):
        transfer.transfer_status["running"] = True
        transfer.transfer_status["thread_id"] = 99999999  # 不存在
        assert transfer.is_transfer_running() is False
        # 自动重置
        assert transfer.transfer_status["running"] is False

    def test_is_transfer_running_false_when_not_running(self):
        transfer.transfer_status["running"] = False
        assert transfer.is_transfer_running() is False

    def test_register_and_snapshot_finish(self):
        tid = "t1"
        transfer.transfer_status["running"] = True
        transfer.transfer_status["task_id"] = tid
        transfer.transfer_status["run_type"] = "transfer"
        transfer._register_running_task(tid)
        transfer.transfer_status["stats"]["ok"] = 3
        transfer.transfer_status["running"] = False
        transfer._snapshot_finish(tid)
        snap = transfer.transfer_tasks[tid]
        assert snap["running"] is False
        assert snap["stats"]["ok"] == 3
        # 快照是副本，修改 live 不影响快照
        transfer.transfer_status["stats"]["ok"] = 99
        assert transfer.transfer_tasks[tid]["stats"]["ok"] == 3

    def test_get_recent_tasks_sorted_desc(self):
        for i, tid in enumerate(["a", "b", "c"]):
            transfer.transfer_status["running"] = True
            transfer.transfer_status["task_id"] = tid
            transfer.transfer_status["start_time"] = "2026-08-03 0%d:00:00" % i
            transfer._register_running_task(tid)
            transfer.transfer_status["running"] = False
            transfer._snapshot_finish(tid)
        recent = transfer.get_recent_tasks()
        assert len(recent) == 3
        assert recent[0]["task_id"] == "c"  # 倒序，最新在前

    def test_enqueue_when_idle_starts_immediately(self):
        _reset_status()
        called = {}
        def fake_run(uniq, limit):
            called["args"] = (uniq, limit)
        orig = transfer.run_transfer
        transfer.run_transfer = fake_run
        try:
            result = transfer.enqueue_scheduled_transfer(["x"], 5)
            for _ in range(50):
                if "args" in called:
                    break
                time.sleep(0.05)
        finally:
            transfer.run_transfer = orig
        assert result == "started"
        assert called.get("args") == (["x"], 5)

    def test_enqueue_when_busy_queues_and_drains(self):
        _reset_status()
        ev = threading.Event()
        def dummy():
            ev.wait(0.5)
        busy = threading.Thread(target=dummy)
        busy.start()
        transfer.transfer_status["running"] = True
        transfer.transfer_status["thread_id"] = busy.ident
        transfer.transfer_status["run_type"] = "transfer"

        called = {}
        def fake_run(uniq, limit):
            called["args"] = (uniq, limit)
        orig = transfer.run_transfer
        transfer.run_transfer = fake_run
        try:
            result = transfer.enqueue_scheduled_transfer(["q"], 7)
            assert result == "queued"
            with transfer._scheduled_queue_lock:
                assert len(transfer._scheduled_queue) == 1
            # 结束当前任务并触发 drain（模拟 run_transfer finally 中的调用）
            ev.set()
            busy.join()
            transfer.transfer_status["running"] = False
            transfer._drain_pending_scheduled()
            for _ in range(50):
                if "args" in called:
                    break
                time.sleep(0.05)
            assert called.get("args") == (["q"], 7)
            with transfer._scheduled_queue_lock:
                assert len(transfer._scheduled_queue) == 0
        finally:
            transfer.run_transfer = orig
