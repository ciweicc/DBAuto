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
from utils import log, sse_broadcast, log_progress
from validator import validate_string, validate_positive_int, validate_list, validate_task


class TransferRouteMixin:
    """转存、搜索、失效检测相关路由"""

    def _handle_transfer_get(self, route):
        if route == "/api/categories":
            self._send_json(CATEGORIES)
            return True

        if route == "/api/transfer/status":
            with transfer_lock:
                status = dict(transfer_status)
                status["progress"] = list(log_progress)
                self._send_json(status)
            return True

        if route == "/api/search":
            params = self._get_query_params()
            keyword = params.get("keyword", "").strip()
            category = params.get("category", "movie")

            ok, msg = validate_string(keyword, min_len=1, max_len=200, allow_empty=False)
            if not ok:
                self._send_json({"error": "keyword: {}".format(msg)}, 400)
                return True

            ok, msg = validate_string(category, min_len=1, max_len=50)
            if not ok:
                self._send_json({"error": "category: {}".format(msg)}, 400)
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
            limit = params.get("limit", 20)

            ok, msg = validate_positive_int(limit, min_val=1, max_val=500)
            if not ok:
                self._send_json({"error": "limit: {}".format(msg)}, 400)
                return True

            try:
                results = check_expired_tasks(int(limit))
                self._send_json({"expired": results})
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

            ok, msg = validate_list(tasks_input, min_len=1, max_len=100, item_validator=validate_task)
            if not ok:
                self._send_json({"success": False, "message": "tasks: {}".format(msg)}, 400)
                return True

            ok, msg = validate_positive_int(limit, min_val=1, max_val=100)
            if not ok:
                self._send_json({"success": False, "message": "limit: {}".format(msg)}, 400)
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

            ok, msg = validate_string(title, min_len=1, max_len=200, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "message": "title: {}".format(msg)}, 400)
                return True

            ok, msg = validate_string(savepath, min_len=1, max_len=500, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "message": "savepath: {}".format(msg)}, 400)
                return True

            ok, msg = validate_string(category, min_len=1, max_len=50)
            if not ok:
                self._send_json({"success": False, "message": "category: {}".format(msg)}, 400)
                return True

            if shareurl:
                ok, msg = validate_string(shareurl, min_len=1, max_len=500)
                if not ok:
                    self._send_json({"success": False, "message": "shareurl: {}".format(msg)}, 400)
                    return True
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

            ok, msg = validate_string(title, min_len=1, max_len=200, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "message": "title: {}".format(msg)}, 400)
                return True

            ok, msg = validate_string(shareurl, min_len=1, max_len=500, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "message": "shareurl: {}".format(msg)}, 400)
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

            ok, msg = validate_list(items, max_len=100)
            if not ok:
                self._send_json({"success": False, "message": "items: {}".format(msg)}, 400)
                return True

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