# routes_config.py — 系统配置 & 调度管理 路由 Mixin
from config import load_config, save_config, load_settings, save_settings
from auth import hash_auth_password
from scheduler import (
    schedule_status, schedule_lock, _next_fire_time, _now_local,
    _run_scheduled_transfer, _run_scheduled_expired_check, _run_scheduled_dir_cleanup,
    invalidate_cron_cache,
)
from utils import log, sse_broadcast
from storage import add_exec_record
from threading import Thread


class ConfigRouteMixin:
    """系统配置 & 调度管理相关路由"""

    def _handle_config_get(self, route):
        if route == "/api/config":
            cfg = load_config()
            masked = {}
            for k, v in cfg.items():
                if k in ("qas_token", "openlist_token", "auth_pass"):
                    masked[k] = "***" if v else ""
                else:
                    masked[k] = v
            self._send_json(masked)
            return True

        if route == "/api/schedule":
            settings = load_settings()
            with schedule_lock:
                status = dict(schedule_status)
            t = settings.get("transfer", {})
            e = settings.get("expired_check", {})
            d = settings.get("dir_cleanup", {})
            result = dict(settings)
            result["_status"] = status
            result["_next_runs"] = {
                "transfer": _format_next(t.get("time"), t.get("cron")) if t.get("enabled") else None,
                "expired_check": _format_next(e.get("time"), e.get("cron")) if e.get("enabled") else None,
                "dir_cleanup": _format_next(d.get("time"), d.get("cron")) if d.get("enabled") else None,
            }
            self._send_json(result)
            return True

        if route == "/api/dir_cleanup":
            params = self._get_query_params()
            manual = params.get("manual", "0") == "1"
            if manual:
                Thread(target=_run_scheduled_dir_cleanup, daemon=True).start()
                self._send_json({"success": True, "message": "started"})
            else:
                self._send_json({"success": True, "status": "ok"})
            return True

        return False

    def _handle_config_post(self, route, body):
        if route == "/api/config":
            cfg = load_config()
            for k, v in body.items():
                if k in cfg:
                    if k == "auth_pass":
                        if v and v != "***":
                            cfg[k] = hash_auth_password(v)
                    elif k in ("qas_token", "openlist_token"):
                        if v and v != "***":
                            cfg[k] = v
                    else:
                        cfg[k] = v
            save_config(cfg)
            log("配置已更新")
            add_exec_record("config", "update config")
            sse_broadcast("config_update", {})
            self._send_json({"success": True})
            return True

        if route == "/api/schedule":
            settings = load_settings()
            action = body.get("action", "save")

            if action == "save":
                for section in ("transfer", "expired_check", "dir_cleanup"):
                    if section in body:
                        if section not in settings:
                            settings[section] = {}
                        settings[section].update(body[section])
                save_settings(settings)
                invalidate_cron_cache()
                log("调度配置已更新")
                add_exec_record("schedule", "update schedule")
                sse_broadcast("schedule_update", {})
                self._send_json({"success": True})
                return True

            elif action == "toggle":
                section = body.get("section", "")
                enabled = body.get("enabled", False)
                if section in settings:
                    settings[section]["enabled"] = enabled
                    save_settings(settings)
                    invalidate_cron_cache()
                    log("调度 {}: {}".format(section, "启用" if enabled else "停用"))
                    add_exec_record("schedule", "toggle {}".format(section))
                    sse_broadcast("schedule_update", {})
                self._send_json({"success": True})
                return True

            elif action == "run_now":
                section = body.get("section", "")
                if section == "transfer":
                    Thread(target=_run_scheduled_transfer, daemon=True).start()
                    self._send_json({"success": True, "message": "transfer started"})
                    return True
                elif section == "expired_check":
                    Thread(target=_run_scheduled_expired_check, daemon=True).start()
                    self._send_json({"success": True, "message": "expired_check started"})
                    return True
                elif section == "dir_cleanup":
                    Thread(target=_run_scheduled_dir_cleanup, daemon=True).start()
                    self._send_json({"success": True, "message": "dir_cleanup started"})
                    return True

            self._send_json({"success": False, "message": "unknown action"})
            return True

        return False


def _format_next(time_str, cron_str):
    dt = _next_fire_time(time_str, cron_str)
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M")
    return None
