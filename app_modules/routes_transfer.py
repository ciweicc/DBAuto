# routes_transfer.py — 转存、搜索、失效检测 路由 Mixin
from threading import Thread
from config import CATEGORIES
from douban import get_douban_list
from transfer import (
    transfer_status, transfer_lock, search_pansou, check_pansou_links,
    check_expired_tasks, update_expired_task, validate_share_link,
    run_transfer, add_and_run, VIDEO_SUB, TV_REPLACE, build_transfer_tasks,
    is_transfer_running,
)
from storage import load_history, save_history, add_exec_record
from utils import log, sse_broadcast


class TransferRouteMixin:
    """转存、搜索、失效检测相关路由"""

    def _handle_transfer_get(self, route):
        if route == "/api/categories":
            self._send_json(CATEGORIES)
            return True

        if route == "/api/transfer/status":
            with transfer_lock:
                self._send_json(dict(transfer_status))
            return True

        if route == "/api/search":
            params = self._get_query_params()
            keyword = params.get("keyword", "").strip()
            category = params.get("category", "movie")
            if not keyword:
                self._send_json([])
                return True
            try:
                results = search_pansou(keyword, category)
                self._send_json(results)
            except Exception as e:
                log("搜索失败: {}".format(e))
                self._send_json({"error": str(e)}, 500)
            return True

        if route == "/api/check_expired":
            params = self._get_query_params()
            limit = int(params.get("limit", 20))
            try:
                results = check_expired_tasks(limit)
                self._send_json({"total": len(results), "items": results})
            except Exception as e:
                log("失效检测失败: {}".format(e))
                self._send_json({"error": str(e)}, 500)
            return True

        return False

    def _handle_transfer_post(self, route, body):
        if route == "/api/transfer":
            if is_transfer_running():
                self._send_json({"success": False, "message": "busy", "conflict": True})
                return True
            tasks_input = body.get("tasks", [])
            limit = body.get("limit", 5)
            if not tasks_input:
                self._send_json({"success": False, "message": "no tasks"})
                return True
            uniq = build_transfer_tasks(tasks_input)
            Thread(target=run_transfer, args=(uniq, limit), daemon=True).start()
            self._send_json({"success": True, "message": "started {}".format(len(uniq))})
            return True

        if route == "/api/stop":
            with transfer_lock:
                transfer_status["stop"] = True
            self._send_json({"success": True, "message": "stopping"})
            return True

        if route == "/api/transfer_one":
            title = body.get("title", "").strip()
            savepath = body.get("savepath", "").strip()
            category = body.get("category", "movie")
            shareurl = body.get("shareurl", "")
            if not title:
                self._send_json({"success": False, "message": "title required"})
                return True
            if shareurl:
                pattern = body.get("pattern", "")
                replace = body.get("replace", "")
                Thread(target=add_and_run, args=(title, shareurl, savepath, pattern, replace), daemon=True).start()
            else:
                task = {"title": title, "savepath": savepath, "category": category}
                Thread(target=run_transfer, args=([task], 1), daemon=True).start()
            self._send_json({"success": True, "message": "added"})
            return True

        if route == "/api/search_replace":
            title = body.get("title", "").strip()
            shareurl = body.get("shareurl", "").strip()
            if not title or not shareurl:
                self._send_json({"success": False, "message": "title and shareurl required"})
                return True
            valid = validate_share_link(shareurl)
            if not valid:
                self._send_json({"success": False, "message": "invalid share link"})
                return True
            history = load_history()
            updated = False
            if title in history:
                history[title]["shareurl"] = shareurl
                updated = True
            if updated:
                save_history(history)
                add_exec_record("history", "update shareurl for {}".format(title))
                sse_broadcast("history_update", {"action": "update", "title": title})
            self._send_json({"success": True, "updated": updated})
            return True

        if route == "/api/update_expired":
            items = body.get("items", [])
            history = load_history()
            count = 0
            for item in items:
                title = item.get("title", "")
                new_url = item.get("shareurl", "")
                if title and new_url and title in history:
                    history[title]["shareurl"] = new_url
                    count += 1
            if count > 0:
                save_history(history)
                add_exec_record("history", "batch update {} items".format(count))
                sse_broadcast("history_update", {"action": "batch_update", "count": count})
            self._send_json({"success": True, "updated": count})
            return True

        return False
