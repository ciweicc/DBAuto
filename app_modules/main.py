# main.py — 入口、启动、信号处理
import os, signal, atexit, time
from threading import Thread
from config import PORT, load_config
from transfer import init_qas_cache
from scheduler import scheduler_loop
from server import ThreadedHTTPServer
from routes import H
from utils import log
import link_check

_startup_time = time.time()
_shutdown_server = None

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
