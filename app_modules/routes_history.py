# routes_history.py — 历史记录管理路由 Mixin
from storage import load_history, save_history, load_exec_history, add_exec_record, clear_exec_history
from utils import log, sse_broadcast
from validator import validate_string, validate_list


class HistoryRouteMixin:
    """历史记录相关路由"""

    def _handle_history_get(self, route):
        if route == "/api/history":
            history = load_history()
            self._send_json({"total": len(history), "items": history})
            return True

        if route == "/api/history/manage":
            history = load_history()
            categories = {}
            for title, info in history.items():
                cat = info.get("category", "unknown")
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append({"title": title, **info})
            self._send_json({"categories": categories, "total": len(history)})
            return True

        if route == "/api/history/export":
            history = load_history()
            lines = []
            for title, info in history.items():
                line = "{} | {} | {}".format(
                    title, info.get("shareurl", ""), info.get("date", "")
                )
                lines.append(line)
            text = "\n".join(lines)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=history.txt")
            self.send_header("Content-Length", str(len(text.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))
            return True

        if route == "/api/exec_history":
            data = load_exec_history()
            params = self._get_query_params()
            try:
                limit = int(params.get("limit", 50))
            except (ValueError, TypeError):
                limit = 50
            try:
                page = int(params.get("page", 1))
            except (ValueError, TypeError):
                page = 1
            if limit < 1:
                limit = 50
            if page < 1:
                page = 1
            start = (page - 1) * limit
            end = start + limit
            items = data[start:end]
            self._send_json({"total": len(data), "items": items})
            return True

        return False

    def _handle_history_post(self, route, body):
        if route == "/api/history/manage":
            action = body.get("action", "")

            ok, msg = validate_string(action, min_len=1, max_len=50, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "message": "action: {}".format(msg)}, 400)
                return True

            history = load_history()
            changed = False

            if action == "delete":
                titles = body.get("titles", [])
                ok, msg = validate_list(titles, max_len=100)
                if not ok:
                    self._send_json({"success": False, "message": "titles: {}".format(msg)}, 400)
                    return True
                for t in titles:
                    if t in history:
                        del history[t]
                        changed = True
                log("删除历史记录: {} 条".format(len(titles)))

            elif action == "clear":
                history.clear()
                changed = True
                log("清空历史记录")

            elif action == "add":
                title = body.get("title", "").strip()
                shareurl = body.get("shareurl", "").strip()
                category = body.get("category", "movie")

                ok, msg = validate_string(title, min_len=1, max_len=200, allow_empty=False)
                if not ok:
                    self._send_json({"success": False, "message": "title: {}".format(msg)}, 400)
                    return True

                ok, msg = validate_string(shareurl, min_len=1, max_len=500, allow_empty=False)
                if not ok:
                    self._send_json({"success": False, "message": "shareurl: {}".format(msg)}, 400)
                    return True

                ok, msg = validate_string(category, min_len=1, max_len=50)
                if not ok:
                    self._send_json({"success": False, "message": "category: {}".format(msg)}, 400)
                    return True

                history[title] = {
                    "shareurl": shareurl,
                    "category": category,
                    "date": body.get("date", ""),
                }
                changed = True
                log("添加历史记录: {}".format(title))

            elif action == "update":
                title = body.get("title", "")

                ok, msg = validate_string(title, min_len=1, max_len=200, allow_empty=False)
                if not ok:
                    self._send_json({"success": False, "message": "title: {}".format(msg)}, 400)
                    return True

                if title not in history:
                    self._send_json({"success": False, "message": "title not found"}, 404)
                    return True

                for k in ("shareurl", "category", "date"):
                    if k in body:
                        if k == "shareurl":
                            ok, msg = validate_string(body[k], min_len=1, max_len=500)
                            if not ok:
                                self._send_json({"success": False, "message": "shareurl: {}".format(msg)}, 400)
                                return True
                        elif k == "category":
                            ok, msg = validate_string(body[k], min_len=1, max_len=50)
                            if not ok:
                                self._send_json({"success": False, "message": "category: {}".format(msg)}, 400)
                                return True
                        history[title][k] = body[k]
                changed = True
                log("更新历史记录: {}".format(title))

            else:
                self._send_json({"success": False, "message": "unknown action: {}".format(action)}, 400)
                return True

            if changed:
                save_history(history)
                add_exec_record("history", "{} action".format(action))
                sse_broadcast("history_update", {"action": action})

            self._send_json({"success": changed})
            return True

        if route == "/api/exec_history/manage":
            action = body.get("action", "")
            ok, msg = validate_string(action, min_len=1, max_len=50, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "message": "action: {}".format(msg)}, 400)
                return True

            if action == "clear":
                clear_exec_history()
                log("清空执行历史")
                sse_broadcast("exec_history_update", {"action": "clear"})
                self._send_json({"success": True})
                return True

            self._send_json({"success": False, "message": "unknown action: {}".format(action)}, 400)
            return True

        return False