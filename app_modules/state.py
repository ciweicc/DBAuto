# state.py — 全局转存状态管理（transfer_status + transfer_lock + 线程检测）
from threading import Lock, enumerate as enumerate_threads
from utils import log

# 全局状态字典：所有模块共享同一实例
transfer_status = {
    "running": False,
    "summary": None,
    "start_time": None,
    "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0},
    "thread_id": None,
}

# 全局锁：保护 transfer_status 的并发读写
transfer_lock = Lock()


def is_transfer_running():
    """检查转存是否正在运行，自动检测僵尸线程并重置"""
    with transfer_lock:
        if not transfer_status.get("running"):
            return False
        tid = transfer_status.get("thread_id")
        if tid is None:
            return False
        for t in enumerate_threads():
            if t.ident == tid and t.is_alive():
                return True
        transfer_status["running"] = False
        transfer_status["thread_id"] = None
        transfer_status["stop"] = False
        log("检测到转存线程已结束，自动重置状态")
        return False
