# server.py — ThreadedHTTPServer（HTTP/1.1 + keep-alive 反代友好）
import socket
import socketserver
from http.server import HTTPServer
from routes import H


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # 请求队列：允许更多并发连接排队（CF Tunnel 会复用连接）
    request_queue_size = 128
    # 连接超时：keep-alive 空闲连接 30s 后自动断开
    timeout = 30

    def server_bind(self):
        # 允许快速重绑定（TIME_WAIT 状态下也能 bind）
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (OSError, socket.error):
                pass
        HTTPServer.server_bind(self)
