# lifecycle.py — 优雅关闭：信号处理 + 任务等待 + 资源清理
import signal
import time
from utils import log

_shutdown_requested = False
_cleanup_handlers = []


def is_shutting_down():
    """是否已收到关闭信号"""
    return _shutdown_requested


def on_shutdown(handler):
    """注册关闭时的清理函数（后注册先执行，栈顺序）"""
    _cleanup_handlers.append(handler)


def request_shutdown():
    """请求关闭，通知所有模块停止接受新任务"""
    global _shutdown_requested
    _shutdown_requested = True


def graceful_shutdown(timeout=30):
    """执行优雅关闭，最多等待 timeout 秒"""
    log("开始优雅关闭...")

    # 1. 通知所有模块停止
    request_shutdown()

    # 2. 等待当前转存任务完成
    try:
        from transfer import is_transfer_running, transfer_status
        if is_transfer_running():
            log("等待当前转存任务完成...")
            transfer_status["stop"] = True
            for _ in range(timeout):
                if not is_transfer_running():
                    break
                time.sleep(1)
            if is_transfer_running():
                log("转存任务仍在运行，强制退出")
    except Exception as e:
        log("等待转存任务异常: {}".format(e))

    # 3. 执行注册的清理函数（栈顺序：后注册先执行）
    for handler in reversed(_cleanup_handlers):
        try:
            handler()
        except Exception as e:
            log("清理函数执行失败: {}".format(e))

    # 4. 关闭数据库
    try:
        from storage import close_db
        close_db()
    except Exception as e:
        log("关闭数据库异常: {}".format(e))

    log("优雅关闭完成")


def install_signal_handlers(shutdown_server_func):
    """安装信号处理器，集成到现有 main.py 的同步架构"""
    def _handler(sig, frame):
        log("收到信号 {}".format(sig))
        graceful_shutdown()
        shutdown_server_func()

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)
