# main.py — 入口、启动、信号处理
import os, atexit, time
from threading import Thread
from config import PORT, load_config
from transfer import init_qas_cache
from scheduler import scheduler_loop
from server import ThreadedHTTPServer
from routes import H
from utils import log
from lifecycle import install_signal_handlers, graceful_shutdown

_startup_time = time.time()
_shutdown_server = None


def _do_shutdown():
    """关闭 HTTP 服务器"""
    if _shutdown_server:
        _shutdown_server.shutdown()


def start():
    global _shutdown_server
    install_signal_handlers(_do_shutdown)
    atexit.register(lambda: log("已停止"))

    log("=== douban-transfer 启动 ===")
    log("端口: {}".format(PORT))
    load_config()
    Thread(target=init_qas_cache, daemon=True).start()
    Thread(target=scheduler_loop, daemon=True).start()
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
