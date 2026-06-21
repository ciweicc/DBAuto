# server.py — ThreadedHTTPServer
import socketserver
from http.server import HTTPServer
from routes import H

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
