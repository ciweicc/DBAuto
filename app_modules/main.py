# main.py — 入口、启动、信号处理
import os, signal, atexit, time
from threading import Thread
from config import PORT, load_config
from transfer import init_qas_cache
from scheduler import scheduler_loop
from server import ThreadedHTTPServer
from routes import H
from utils import log, logger
import link_check

_startup_time = time.time()
_shutdown_server = None

def _log_single_instance_mode():
    """P2-12：启动期提示进程内单实例约束（不改变任何运行时行为）。

    本服务的认证 token、登录频率限制、调度器、SSE 客户端、
    各类内存缓存均为进程内单例，仅在「单 OS 进程」内
    （ThreadedHTTPServer 多线程共享进程内存）保持一致。
    多 worker / 多副本会各自持有独立内存，导致状态分裂。
    """
    workers = int(os.environ.get("WEB_CONCURRENCY", os.environ.get("GUNICORN_WORKERS", "1")))
    if workers > 1:
        logger.warning(
            "检测到多 worker 配置 (WEB_CONCURRENCY/GUNICORN_WORKERS=%s)：本服务为单进程单实例设计，"
            "多 worker 会导致 token/限速/调度/缓存等进程内状态分裂，请勿对多 worker 部署。",
            workers,
        )
    else:
        log("单实例模式：进程内状态（token / 限速 / 调度 / 缓存）仅在单 OS 进程内一致；"
            "请勿对多个副本/进程共用同一 DATA_DIR（详见 docs/SCALING.md）。")


def _shutdown(sig=None, frame=None):
    log("正在关闭...")
    if _shutdown_server:
        _shutdown_server.shutdown()

def start():
    global _shutdown_server
    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)
    atexit.register(lambda: log("已停止"))

    log("=== douban-transfer 启动 ===")
    log("端口: {}".format(PORT))
    _log_single_instance_mode()
    load_config()
    Thread(target=init_qas_cache, daemon=True).start()
    Thread(target=scheduler_loop, daemon=True).start()
    link_check.start_workers()  # 链接检测异步队列 worker 池随进程启动
    server = ThreadedHTTPServer(("0.0.0.0", PORT), H)
    _shutdown_server = server
    log("监听 :{}".format(PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    start()
