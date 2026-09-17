# routes_library.py — 双向管理路由 Mixin（QAS 任务 / 网盘媒体库 / 删除同步）
#
# 对外接口（均需登录认证，走统一速率限制）：
#   GET  /api/qas/tasks            —— QAS tasklist 只读列表
#   GET  /api/qas/library?path=/   —— 夸克网盘目录浏览（媒体库）
#   POST /api/qas/delete           —— 删除网盘条目 + 可选同步清理 DBAuto 历史
from media_library import list_tasks, list_library, delete_entries
from validator import validate_string, validate_list


class LibraryRouteMixin:
    """QAS 任务与网盘媒体库管理路由"""

    def _handle_library_get(self, route):
        if route == "/api/qas/tasks":
            res = list_tasks()
            self._send_json(res, 200 if res.get("success") else 502)
            return True

        if route == "/api/qas/library":
            params = self._get_query_params()
            path = params.get("path", "/").strip() or "/"
            ok, msg = validate_string(path, min_len=1, max_len=500, allow_empty=False)
            if not ok:
                self._send_json({"error": "path: {}".format(msg)}, 400)
                return True
            # 路径安全：禁止 `..` 越界（QAS 侧按字面解析，这里做防御性拦截）
            if ".." in path:
                self._send_json({"error": "path 不允许包含 .."}, 400)
                return True
            res = list_library(path=path)
            self._send_json(res, 200 if res.get("success") else 502)
            return True

        return False

    def _handle_library_post(self, route, body):
        if route != "/api/qas/delete":
            return False

        entries = body.get("entries")
        ok, msg = validate_list(entries, min_len=1)
        if not ok:
            self._send_json({"error": "entries: {}".format(msg)}, 400)
            return True

        # 每条必须带 fid 或 path，否则 QAS 无法定位文件
        cleaned = []
        for it in entries:
            if not isinstance(it, dict):
                self._send_json({"error": "entries 每项必须是对象"}, 400)
                return True
            if it.get("fid") in (None, "") and not it.get("path"):
                self._send_json({"error": "entries 每项必须包含 fid 或 path"}, 400)
                return True
            cleaned.append(it)

        sync_history = body.get("sync_history", True)
        res = delete_entries(cleaned, sync_history=bool(sync_history))
        self._send_json(res, 200 if res.get("success") else 502)
        return True
