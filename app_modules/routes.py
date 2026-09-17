# routes.py — HTTP 请求处理器（整合所有路由模块）
#
# 路由模块拆分说明：
#   routes_base.py      - 基类 + 通用工具方法
#   routes_static.py    - 静态文件 + SSE
#   routes_auth.py      - 认证相关（登录、状态）
#   routes_transfer.py  - 转存、搜索、失效检测
#   routes_history.py   - 历史记录管理
#   routes_config.py    - 系统配置 + 调度管理
#
# 新增路由时，在对应模块的 Mixin 类中添加 _handle_xxx_get/_handle_xxx_post 方法，
# 然后在下面的 H 类的 do_GET / do_POST 中调用即可。

import traceback

from routes_base import BaseRouteHandler, _check_rate_limit
from utils import log
from routes_static import StaticRouteMixin
from routes_auth import AuthRouteMixin
from routes_transfer import TransferRouteMixin
from routes_history import HistoryRouteMixin
from routes_config import ConfigRouteMixin
from routes_tmdb import TmdbRouteMixin
from routes_library import LibraryRouteMixin


class H(BaseRouteHandler,
        StaticRouteMixin,
        AuthRouteMixin,
        TransferRouteMixin,
        HistoryRouteMixin,
        ConfigRouteMixin,
        TmdbRouteMixin,
        LibraryRouteMixin):
    """主 HTTP 请求处理器，通过 Mixin 多继承整合所有路由模块"""

    def do_GET(self):
        try:
            self._do_GET(route=self._route_path())
        except Exception:
            # 兜底：任何未捕获异常都不得让客户端收到「空响应」。
            # 线程化 HTTP server 下空响应表现为请求悬停直到连接超时，
            # 前端只会看到「加载慢」；这里至少把它变成明确的 500（issue #27）。
            log("请求处理异常 {}:\n{}".format(self.path, traceback.format_exc()))
            try:
                self._send_json({"error": "internal server error"}, 500)
            except Exception:
                pass

    def _do_GET(self, route):
        # 1. 静态文件 & 健康检查（不需要认证）
        if StaticRouteMixin._handle_static_get(self, route):
            return

        # 2. 认证相关
        if AuthRouteMixin._handle_auth_get(self, route):
            return

        # 3. 需要认证的 API
        if not AuthRouteMixin._require_auth(self):
            return

        # 3.5 API 速率限制
        ok, retry_after = _check_rate_limit(self._get_client_ip())
        if not ok:
            self._send_json({"error": "rate limit exceeded", "retry_after": retry_after}, 429)
            return

        # 3.6 SSE 实时推送（需要认证）
        if route == "/api/sse":
            StaticRouteMixin._handle_sse(self)
            return

        # 4. 转存 & 搜索
        if TransferRouteMixin._handle_transfer_get(self, route):
            return

        # 5. 历史记录
        if HistoryRouteMixin._handle_history_get(self, route):
            return

        # 6. 配置 & 调度
        if ConfigRouteMixin._handle_config_get(self, route):
            return

        # 7. TMDB
        if TmdbRouteMixin._handle_tmdb_get(self, route):
            return

        # 8. QAS 任务 & 网盘媒体库（双向管理）
        if LibraryRouteMixin._handle_library_get(self, route):
            return

        # 404
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            self._do_POST(route=self._route_path())
        except Exception:
            log("请求处理异常 {}:\n{}".format(self.path, traceback.format_exc()))
            try:
                self._send_json({"error": "internal server error"}, 500)
            except Exception:
                pass

    def _do_POST(self, route):
        body = self._read_body()
        if body is None:
            return  # _read_body 已发送错误响应（413/400）

        # 1. 登录（不需要认证）
        if AuthRouteMixin._handle_auth_post(self, route, body):
            return

        # 2. 需要认证的 API
        if not AuthRouteMixin._require_auth(self):
            return

        # 2.5 API 速率限制
        ok, retry_after = _check_rate_limit(self._get_client_ip())
        if not ok:
            self._send_json({"error": "rate limit exceeded", "retry_after": retry_after}, 429)
            return

        # 3. 转存 & 搜索
        if TransferRouteMixin._handle_transfer_post(self, route, body):
            return

        # 4. 历史记录
        if HistoryRouteMixin._handle_history_post(self, route, body):
            return

        # 5. 配置 & 调度
        if ConfigRouteMixin._handle_config_post(self, route, body):
            return

        # 6. TMDB
        if TmdbRouteMixin._handle_tmdb_post(self, route, body):
            return

        # 7. QAS 任务 & 网盘媒体库（双向管理）
        if LibraryRouteMixin._handle_library_post(self, route, body):
            return

        # 404
        self._send_json({"error": "not found"}, 404)
