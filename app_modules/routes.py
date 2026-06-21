# routes.py — 所有 API 路由（GET + POST）+ 静态文件 + SSE
import os, json, time, uuid, urllib.parse
from http.server import BaseHTTPRequestHandler
from threading import Thread
from config import CATEGORIES, load_settings, save_settings, load_config, save_config, DEFAULT_CONFIG
from auth import _check_auth, _do_login, _login_rate_check, _client_ip
from utils import log, sse_clients, sse_lock, SSE_MAX, log_progress
from storage import load_history, save_history, load_exec_history, add_exec_record
from douban import get_douban_list
from transfer import (transfer_status, transfer_lock, search_pansou, check_pansou_links, check_expired_tasks,
                      update_expired_task, validate_share_link, run_transfer, add_and_run,
                      VIDEO_SUB, TV_REPLACE)
from scheduler import schedule_status, schedule_lock, _run_scheduled_dir_cleanup

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css"}
_static_cache = {}
_static_cache_lock = __import__('threading').Lock()

def _get_static_file(fp):
    try:
        mtime = os.path.getmtime(fp)
    except Exception:
        return None
    with _static_cache_lock:
        if fp in _static_cache:
            cm, cd = _static_cache[fp]
            if cm == mtime:
                return cd
    try:
        with open(fp, "rb") as f:
            data = f.read()
        with _static_cache_lock:
            _static_cache[fp] = (mtime, data)
        return data
    except Exception:
        return None

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        path = self.path.split("?")[0]
        route = path

        if path in ("/", "/index.html"):
            path = "/index.html"
        if path.startswith("/static/"):
            path = path[7:]
        if path.startswith("/"):
            path = path[1:]

        if path and "." in path:
            ext = os.path.splitext(path)[1]
            fp = os.path.join(STATIC_DIR, path.lstrip("/"))
            data = _get_static_file(fp)
            if data is not None:
                cc = "no-cache, no-store, must-revalidate" if ext == ".html" else "public, max-age=60"
                self.send_response(200)
                self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
                self.send_header("Cache-Control", cc)
                self.end_headers()
                self.wfile.write(data)
                return

        if route == "/health":
            self._send_json({"status": "ok", "running": transfer_status["running"],
                             "port": 3001, "sse_clients": len(sse_clients)})
            return

        need_auth = not (route == "/api/status" or route.startswith("/api/sse"))
        if need_auth and not _check_auth(self):
            return self._send_json({"success": False, "message": "unauthorized"}, 401)

        if route == "/api/categories":
            self._send_json(CATEGORIES)
        elif route == "/api/status":
            with transfer_lock:
                snap = {"running": transfer_status["running"],
                        "progress": list(log_progress),
                        "summary": transfer_status["summary"],
                        "start_time": transfer_status["start_time"],
                        "stats": dict(transfer_status["stats"])}
            self._send_json(snap)
        elif route == "/api/sse":
            with sse_lock:
                if len(sse_clients) >= SSE_MAX:
                    return self._send_json({"success": False, "message": "too many sse"}, 503)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            cid = uuid.uuid4().hex[:8]
            q = __import__('queue').Queue(maxsize=100)
            with sse_lock:
                sse_clients[cid] = q
            try:
                while True:
                    try:
                        msg = q.get(timeout=30)
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                    except __import__('queue').Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with sse_lock:
                    sse_clients.pop(cid, None)
        elif route == "/api/check_expired":
            expired = check_expired_tasks()
            add_exec_record("expired_check", "found {} expired".format(len(expired)) if expired else "all ok")
            self._send_json({"success": True, "expired": expired})
        elif route == "/api/dir_cleanup":
            Thread(target=_run_scheduled_dir_cleanup, daemon=True).start()
            self._send_json({"success": True, "message": "started"})
        elif route == "/api/history":
            self._send_json(load_history())
        elif route == "/api/history/manage":
            history = load_history()
            result = []
            for k, v in history.items():
                cat = v.get("category", "")
                if not cat:
                    sp = v.get("savepath", "")
                    if "电视剧" in sp: cat = "tv"
                    elif "综艺" in sp: cat = "variety"
                    elif sp: cat = "movie"
                result.append({"title": k, "date": v.get("date", ""),
                               "status": v.get("status", ""), "category": cat})
            self._send_json(result)
        elif route == "/api/history/export":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", "attachment; filename=transfer_history.json")
            self.end_headers()
            self.wfile.write(json.dumps(load_history(), ensure_ascii=False, indent=2).encode())
        elif route == "/api/exec_history":
            limit = 50
            if "?" in self.path:
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                limit = int(qs.get("limit", ["50"])[0])
            data = load_exec_history()
            self._send_json(data[-limit:])
        elif route == "/api/schedule":
            settings = load_settings()
            with schedule_lock:
                settings["_status"] = dict(schedule_status)
            self._send_json(settings)
        elif route == "/api/config":
            cfg = load_config()
            safe = {}
            for k, v in cfg.items():
                safe[k] = "***" if "token" in k or "pass" in k else v
            self._send_json(safe)
        elif route.startswith("/api/search"):
            query = ""
            validate = False
            if "?" in self.path:
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                query = qs.get("q", [""])[0]
                validate = qs.get("validate", ["0"])[0] == "1"
            if not query:
                self._send_json({"success": False, "message": "missing query"})
                return
            results = search_pansou(query)
            # 链接有效性检查（仅 validate=1 时）
            if validate:
                urls = [r.get("url", "") for r in results[:20] if r.get("url")]
                valid_urls = check_pansou_links(urls)
                results = [r for r in results if r.get("url", "") in valid_urls]
            items = [{"title": r.get("note", query), "url": r.get("url", ""),
                      "source": r.get("source", "")} for r in results[:20]]
            self._send_json({"success": True, "results": items})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        try:
            raw = self.rfile.read(ln) if ln else b"{}"
            body = json.loads(raw) if ln else {}
        except Exception:
            body = {}
        route = self.path.split("?")[0]
        cip = _client_ip(self)

        if route == "/api/login":
            ok, remaining = _login_rate_check(cip)
            if not ok:
                return self._send_json({"success": False,
                                        "message": "too many attempts, wait {}s".format(remaining)}, 429)
            token = _do_login(body.get("username", ""), body.get("password", ""))
            if token:
                return self._send_json({"success": True, "token": token})
            return self._send_json({"success": False, "message": "wrong credentials"}, 401)

        if not _check_auth(self):
            return self._send_json({"success": False, "message": "unauthorized"}, 401)

        if route == "/api/stop":
            with transfer_lock:
                transfer_status["running"] = False
            self._send_json({"success": True})
        elif route == "/api/search_replace":
            taskname = body.get("taskname", "")
            if taskname:
                results = search_pansou(taskname)
                new_url = ""
                for r in results[:5]:
                    url = r.get("url", "")
                    if not url: continue
                    valid, _ = validate_share_link(url)
                    if valid:
                        new_url = url; break
                self._send_json({"success": bool(new_url), "new_url": new_url})
            else:
                self._send_json({"success": False})
        elif route == "/api/update_expired":
            task = body.get("task")
            new_url = body.get("new_url")
            if task and new_url:
                self._send_json({"success": update_expired_task(task, new_url)})
            else:
                self._send_json({"success": False, "message": "missing params"})
        elif route == "/api/transfer":
            with transfer_lock:
                if transfer_status["running"]:
                    return self._send_json({"success": False, "message": "busy", "conflict": True})
            tasks_input = body.get("tasks", [])
            limit = body.get("limit", 5)
            if not tasks_input:
                return self._send_json({"success": False, "message": "no tasks"})
            all_t = []
            for t in tasks_input:
                try:
                    items = get_douban_list(t["path"], t["type"], 20)
                    for i in items:
                        all_t.append({"title": i["title"], "savepath": t["savepath"],
                                      "category": t.get("category", "movie")})
                except Exception as e:
                    log("获取错误: {}".format(e))
            seen = set()
            uniq = []
            for ti in all_t:
                if ti["title"] not in seen:
                    seen.add(ti["title"]); uniq.append(ti)
            log("共获取 {} 条".format(len(uniq)))
            Thread(target=run_transfer, args=(uniq, limit), daemon=True).start()
            self._send_json({"success": True, "message": "started {}".format(len(uniq))})
        elif route == "/api/transfer_one":
            title = body.get("title", "").strip()
            url = body.get("url", "").strip()
            savepath = body.get("savepath", "/批量转存/手动搜索存").strip()
            if not title or not url:
                return self._send_json({"success": False, "message": "missing title or url"})
            log("手动转存: {} → {}".format(title, savepath))
            fullpath = "{}/{}".format(savepath, title) if savepath else title
            result = add_and_run(title, url, fullpath, pattern=VIDEO_SUB, replace=TV_REPLACE)
            status = result.get("status", "error")
            if status in ("ok", "done"):
                self._send_json({"success": True, "message": "转存成功"})
            elif status == "exists":
                self._send_json({"success": True, "message": "已存在，无需重复转存"})
            else:
                self._send_json({"success": False, "message": result.get("msg", "转存失败")})
        elif route == "/api/history/manage":
            action = body.get("action", "")
            if action == "delete":
                title = body.get("title", "")
                if title:
                    h = load_history()
                    if title in h:
                        del h[title]; save_history(h)
                    self._send_json({"success": True, "message": "deleted"})
                else:
                    self._send_json({"success": False, "message": "missing title"})
            elif action == "delete_category":
                cat = body.get("category", "")
                if cat:
                    h = load_history()
                    to_del = [k for k, v in h.items() if v.get("category") == cat]
                    for k in to_del: del h[k]
                    save_history(h)
                    self._send_json({"success": True, "message": "deleted {}".format(len(to_del))})
                else:
                    self._send_json({"success": False, "message": "missing category"})
            elif action == "delete_all":
                save_history({})
                self._send_json({"success": True, "message": "cleared"})
            else:
                self._send_json({"success": False, "message": "unknown action"})
        elif route == "/api/schedule":
            settings = load_settings()
            if "transfer" in body:
                t = body["transfer"]
                settings["transfer"]["enabled"] = bool(t.get("enabled", False))
                if t.get("time"): settings["transfer"]["time"] = t["time"]
                if "cron" in t: settings["transfer"]["cron"] = t.get("cron", "")
                if t.get("limit"): settings["transfer"]["limit"] = int(t["limit"])
                if "tasks" in t: settings["transfer"]["tasks"] = t["tasks"]
            if "expired_check" in body:
                e = body["expired_check"]
                settings["expired_check"]["enabled"] = bool(e.get("enabled", False))
                if e.get("time"): settings["expired_check"]["time"] = e["time"]
                if "cron" in e: settings["expired_check"]["cron"] = e.get("cron", "")
            if "dir_cleanup" in body:
                dc = body["dir_cleanup"]
                settings["dir_cleanup"]["enabled"] = bool(dc.get("enabled", False))
                if dc.get("time"): settings["dir_cleanup"]["time"] = dc["time"]
                if "cron" in dc: settings["dir_cleanup"]["cron"] = dc.get("cron", "")
                if "directories" in dc: settings["dir_cleanup"]["directories"] = dc["directories"]
            save_settings(settings)
            log("调度已更新")
            with schedule_lock:
                resp = {"success": True, "_status": dict(schedule_status)}
            self._send_json(resp)
        elif route == "/api/config":
            allowed = set(DEFAULT_CONFIG.keys())
            current = load_config()
            for k, v in body.items():
                if k not in allowed: continue
                if k in ("auth_pass", "qas_token", "openlist_token"):
                    if not v: continue
                current[k] = v
            save_config(current)
            self._send_json({"success": True, "message": "saved"})
        else:
            self.send_response(404)
            self.end_headers()
