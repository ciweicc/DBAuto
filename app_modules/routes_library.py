# routes_library.py — 双向管理路由 Mixin（QAS 任务 / 网盘媒体库 / 删除同步）
#
# 对外接口（均需登录认证，走统一速率限制）：
#   GET  /api/qas/tasks            —— QAS tasklist 列表
#   GET  /api/qas/library?path=/   —— 夸克网盘目录浏览（媒体库）
#   POST /api/qas/delete           —— 删除网盘条目 + 可选同步清理 DBAuto 历史
#   POST /api/qas/delete_tasks     —— 删除 QAS 条目任务配置（整表写回）
#
# OpenList 数据源（issue #27 追问）：目录浏览可改由 OpenList 回答，
#   GET  /api/library/source       —— 当前数据源 / 各源可用性自检
#   GET  /api/openlist/tasks/audit —— QAS 任务 ↔ 网盘内容 对账（只读）
#
# 注意：浏览与删除是**同源**的一组接口 —— 浏览器看到的条目由谁列出来，
# 删除就发给谁（OpenList 用条目名、QAS 用 fid）。跨源拼路径会误删，见 media_library。
import openlist
import media_library
from media_library import LIBRARY_SOURCE_OPENLIST, LIBRARY_SOURCE_QAS
from validator import validate_string, validate_list


class LibraryRouteMixin:
    """QAS 任务与网盘媒体库管理路由"""

    # 读接口的缓存开关：前端「刷新」按钮带 ?refresh=1 显式绕过缓存
    # （普通打开媒体库页 / 切标签命中缓存，重复打开不再等网络）。
    _NO_CACHE_VALUES = ("1", "true", "yes", "on")

    def _cache_disabled(self, params):
        raw = params.get("refresh")
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        return str(raw).strip().lower() in self._NO_CACHE_VALUES

    def _handle_library_get(self, route):
        if route == "/api/qas/tasks":
            res = media_library.list_tasks()
            self._send_json(res, 200 if res.get("success") else 502)
            return True

        if route == "/api/qas/library":
            params = self._get_query_params()
            # 未显式指定 path 时，落到设置里的「媒体库根目录」，
            # 这样媒体库页打开就在用户指定的路径，而非网盘根（issue #27）。
            raw_path = params.get("path")
            if raw_path is None or not str(raw_path).strip():
                raw_path = media_library.get_library_root()
            path = str(raw_path).strip() or "/"
            ok, msg = validate_string(path, min_len=1, max_len=500, allow_empty=False)
            if not ok:
                self._send_json({"error": "path: {}".format(msg)}, 400)
                return True
            # 路径安全：禁止 `..` 越界（QAS 侧按字面解析，这里做防御性拦截）
            if ".." in path:
                self._send_json({"error": "path 不允许包含 .."}, 400)
                return True
            # 数据源由设置决定：配了 OpenList 就走 OpenList（更快，见 media_library），
            # 未配或不可达时自动回落 QAS，并把「谁回答的 / 是否回落」如实带给前端。
            # refresh=1（前端「刷新」按钮）同时绕过 OpenList 缓存与 QAS 目录短命缓存；
            # 不带 refresh（普通打开 / 切标签）走缓存，重复打开不再等一次网络往返。
            res = media_library.list_library_entries(
                path=path, refresh=self._cache_disabled(params))
            self._send_json(res, 200 if res.get("success") else 502)
            return True

        if route == "/api/library/source":
            # 数据源自检：让设置页能一键验证 OpenList 配置是否真的可用，
            # 而不是等用户翻媒体库时才发现配错了。
            src = media_library.library_source()
            info = {"source": src, "openlist": openlist.is_configured()}
            if info["openlist"]:
                # 鉴权方式（令牌 / 账号密码 / 游客）来自配置，不依赖可达性 ——
                # 用户问「我现在到底在用哪种鉴权」时不该因为网络不通就答不上来。
                info["openlist_auth"] = openlist.auth_status()
                ping = openlist.ping()
                info["openlist_ok"] = bool(ping.get("success"))
                info["openlist_user"] = ping.get("user", "")
                info["openlist_message"] = ping.get("message", "")
                # 「配置里写的」与「服务端实际接受的」可能不同（例如令牌写错、
                # 账号密码被 OpenList 侧改过），两者都带给前端，让提示能落地。
                info["openlist_auth_mode"] = info["openlist_auth"].get("mode", "")
                info["openlist_auth_source"] = ping.get("mode", "")
                info["openlist_auth_label"] = ping.get("mode_label", "") or \
                    info["openlist_auth"].get("mode_label", "")
            self._send_json({"success": True, **info})
            return True

        if route == "/api/openlist/tasks/audit":
            # 任务对账（只读）：QAS 任务在网盘上是否还找得到对应内容。
            # 刻意不做任何写操作 —— 结论用于提示，删不删由用户在既有链路里决定。
            params = self._get_query_params()
            root = params.get("path")
            if root is not None:
                root = str(root).strip()
                if root and ".." in root:
                    self._send_json({"error": "path 不允许包含 .."}, 400)
                    return True
            res = media_library.reconcile_tasks(root=root or None)
            self._send_json(res, 200 if res.get("success") else 502)
            return True

        return False

    def _handle_library_post(self, route, body):
        if route == "/api/qas/delete_tasks":
            return self._handle_task_delete(body)

        if route != "/api/qas/delete":
            return False

        entries = body.get("entries")
        ok, msg = validate_list(entries, min_len=1)
        if not ok:
            self._send_json({"error": "entries: {}".format(msg)}, 400)
            return True

        # 数据源：OpenList 侧用条目名定位（与浏览同源），QAS 侧用 fid / path。
        # 默认取当前数据源，允许请求显式指定（前端按条目的 _src 传，防止
        # 「OpenList 浏览、QAS 删除」这种跨源错配）。
        source = body.get("source") or media_library.library_source()
        if source not in (LIBRARY_SOURCE_OPENLIST, LIBRARY_SOURCE_QAS):
            self._send_json({"error": "source 必须是 openlist 或 qas"}, 400)
            return True

        cleaned = []
        for it in entries:
            if not isinstance(it, dict):
                self._send_json({"error": "entries 每项必须是对象"}, 400)
                return True
            if source == LIBRARY_SOURCE_OPENLIST:
                if not str(it.get("name") or "").strip():
                    self._send_json({"error": "entries 每项必须包含 name"}, 400)
                    return True
            elif it.get("fid") in (None, "") and not it.get("path"):
                self._send_json({"error": "entries 每项必须包含 fid 或 path"}, 400)
                return True
            cleaned.append(it)

        sync_history = body.get("sync_history", True)
        res = media_library.delete_entries_by_source(
            cleaned, sync_history=bool(sync_history), source=source)
        self._send_json(res, 200 if res.get("success") else 502)
        return True

    def _handle_task_delete(self, body):
        """删除 QAS 任务条目。body: {"tasks": [{"taskname","savepath"} | {"index": n}]}"""
        tasks = body.get("tasks")
        ok, msg = validate_list(tasks, min_len=1)
        if not ok:
            self._send_json({"error": "tasks: {}".format(msg)}, 400)
            return True
        for it in tasks:
            if not isinstance(it, dict):
                self._send_json({"error": "tasks 每项必须是对象"}, 400)
                return True
            has_index = it.get("index") is not None
            if not has_index and not it.get("taskname"):
                self._send_json({"error": "tasks 每项必须包含 taskname 或 index"}, 400)
                return True
        res = media_library.delete_tasks(tasks)
        self._send_json(res, 200 if res.get("success") else 502)
        return True
