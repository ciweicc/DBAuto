# routes_history.py — 历史记录管理路由 Mixin
from storage import load_history, save_history, load_exec_history, add_exec_record
from utils import log, sse_broadcast


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
            self._send_json({"total": len(data), "items": data})
            return True

        return False

    def _handle_history_post(self, route, body):
        if route == "/api/history/manage":
            action = body.get("action", "")
            history = load_history()
            changed = False

            if action == "delete":
                titles = body.get("titles", [])
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
                if title and shareurl:
                    history[title] = {
                        "shareurl": shareurl,
                        "category": category,
                        "date": body.get("date", ""),
                    }
                    changed = True
                    log("添加历史记录: {}".format(title))

            elif action == "update":
                title = body.get("title", "")
                if title in history:
                    for k in ("shareurl", "category", "date"):
                        if k in body:
                            history[title][k] = body[k]
                    changed = True
                    log("更新历史记录: {}".format(title))

            if changed:
                save_history(history)
                add_exec_record("history", "{} action".format(action))
                sse_broadcast("history_update", {"action": action})

            self._send_json({"success": changed})
            return True

        return False
