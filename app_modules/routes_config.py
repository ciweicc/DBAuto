# routes_config.py — 系统配置 & 调度管理 路由 Mixin
from config import load_config, save_config, load_settings, save_settings
from auth import hash_auth_password
from scheduler import (
    schedule_status, schedule_lock, _next_fire_time, _now_local,
    _run_scheduled_transfer, _run_scheduled_expired_check,
    notify_settings_changed,
)
from transfer import reset_qas_client
from utils import log, sse_broadcast
from storage import add_exec_record
from threading import Thread
from validator import validate_string, validate_url, validate_cron, validate_time, validate_positive_int, validate_list


def _format_next(time_str, cron_str, interval_hours=0, last_run=None):
    dt = _next_fire_time(time_str, cron_str, interval_hours, last_run)
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M")
    return None


class ConfigRouteMixin:
    """系统配置 & 调度管理相关路由"""

    def _handle_config_get(self, route):
        if route == "/api/config":
            cfg = load_config()
            masked = {}
            for k, v in cfg.items():
                if k in ("qas_token", "auth_pass", "douban_cookie", "tmdb_api_key"):
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
            result = dict(settings)
            # 脱敏豆瓣多账号 cookie
            dw = result.get("douban_wish", {})
            if dw and isinstance(dw.get("accounts"), list):
                masked_accounts = []
                for acc in dw["accounts"]:
                    ma = dict(acc)
                    if ma.get("cookie"):
                        ma["cookie"] = "***"
                    masked_accounts.append(ma)
                result["douban_wish"] = dict(dw)
                result["douban_wish"]["accounts"] = masked_accounts
            result["_status"] = status
            result["_next_runs"] = {
                "transfer": _format_next(t.get("time"), t.get("cron"), t.get("interval_hours", 0),
                                          status.get("last_transfer")) if t.get("enabled") else None,
                "expired_check": _format_next(e.get("time"), e.get("cron"), e.get("interval_hours", 0),
                                               status.get("last_expired_check")) if e.get("enabled") else None,
            }
            self._send_json(result)
            return True

        if route == "/api/refresh_douban":
            from douban import refresh_douban_cache
            refresh_douban_cache()
            self._send_json({"success": True, "message": "豆瓣缓存已刷新"})
            return True

        return False

    def _handle_config_post(self, route, body):
        if route == "/api/config":
            cfg = load_config()

            for k, v in body.items():
                if k not in cfg:
                    self._send_json({"success": False, "message": "unknown config key: {}".format(k)}, 400)
                    return True

                if k == "auth_pass":
                    if v and v != "***":
                        ok, msg = validate_string(v, min_len=1, max_len=100)
                        if not ok:
                            self._send_json({"success": False, "message": "auth_pass: {}".format(msg)}, 400)
                            return True
                        cfg[k] = hash_auth_password(v)
                elif k == "qas_token":
                    if v and v != "***":
                        ok, msg = validate_string(v, min_len=1, max_len=500)
                        if not ok:
                            self._send_json({"success": False, "message": "{}: {}".format(k, msg)}, 400)
                            return True
                        cfg[k] = v
                elif k in ("pansou", "qas"):
                    if v:
                        ok, msg = validate_url(v, required=False)
                        if not ok:
                            self._send_json({"success": False, "message": "{}: {}".format(k, msg)}, 400)
                            return True
                        cfg[k] = v
                elif k == "auth_user":
                    ok, msg = validate_string(v, min_len=1, max_len=50, allow_empty=False)
                    if not ok:
                        self._send_json({"success": False, "message": "auth_user: {}".format(msg)}, 400)
                        return True
                    cfg[k] = v
                elif k == "douban_uid":
                    if v:
                        ok, msg = validate_string(v, min_len=1, max_len=50)
                        if not ok:
                            self._send_json({"success": False, "message": "douban_uid: {}".format(msg)}, 400)
                            return True
                    cfg[k] = v
                elif k == "douban_cookie":
                    if v and v != "***":
                        ok, msg = validate_string(v, min_len=1, max_len=2000)
                        if not ok:
                            self._send_json({"success": False, "message": "douban_cookie: {}".format(msg)}, 400)
                            return True
                        cfg[k] = v
                elif k == "tmdb_api_key":
                    if v:
                        ok, msg = validate_string(v, min_len=1, max_len=100)
                        if not ok:
                            self._send_json({"success": False, "message": "tmdb_api_key: {}".format(msg)}, 400)
                            return True
                    cfg[k] = v
                else:
                    cfg[k] = v

            save_config(cfg)
            reset_qas_client()
            log("配置已更新")
            add_exec_record("config", "update config")
            sse_broadcast("config_update", {})
            self._send_json({"success": True})
            return True

        if route == "/api/schedule":
            settings = load_settings()
            action = body.get("action", "save")

            if action == "save":
                old_settings = dict(settings)
                for section in ("transfer", "expired_check", "douban_wish"):
                    if section in body:
                        section_data = body[section]
                        if section not in settings:
                            settings[section] = {}

                        if "time" in section_data:
                            ok, msg = validate_time(section_data["time"])
                            if not ok:
                                self._send_json({"success": False, "message": "{} time: {}".format(section, msg)}, 400)
                                return True

                        if "cron" in section_data:
                            ok, msg = validate_cron(section_data["cron"])
                            if not ok:
                                self._send_json({"success": False, "message": "{} cron: {}".format(section, msg)}, 400)
                                return True

                        if "interval_hours" in section_data:
                            val = section_data["interval_hours"]
                            if not isinstance(val, int) or val < 0 or val > 168:
                                self._send_json({"success": False, "message": "interval_hours must be 0-168"}, 400)
                                return True

                        if section == "transfer" and "limit" in section_data:
                            ok, msg = validate_positive_int(section_data["limit"], min_val=1, max_val=100)
                            if not ok:
                                self._send_json({"success": False, "message": "transfer limit: {}".format(msg)}, 400)
                                return True

                        if "directories" in section_data:
                            ok, msg = validate_list(section_data["directories"], max_len=100)
                            if not ok:
                                self._send_json({"success": False, "message": "{} directories: {}".format(section, msg)}, 400)
                                return True

                        # 豆瓣想看同步的保存路径和分类
                        if section == "douban_wish":
                            if "savepath" in section_data:
                                ok, msg = validate_string(section_data["savepath"], min_len=1, max_len=500)
                                if not ok:
                                    self._send_json({"success": False, "message": "savepath: {}".format(msg)}, 400)
                                    return True
                            if "category" in section_data:
                                cat = section_data["category"]
                                if isinstance(cat, list):
                                    ok, msg = validate_list(cat, max_len=10)
                                    if not ok:
                                        self._send_json({"success": False, "message": "category: {}".format(msg)}, 400)
                                        return True
                                    for c in cat:
                                        ok, msg = validate_string(c, min_len=1, max_len=50)
                                        if not ok:
                                            self._send_json({"success": False, "message": "category: {}".format(msg)}, 400)
                                            return True
                                elif isinstance(cat, str):
                                    ok, msg = validate_string(cat, min_len=1, max_len=50)
                                    if not ok:
                                        self._send_json({"success": False, "message": "category: {}".format(msg)}, 400)
                                        return True
                            if "accounts" in section_data:
                                accts = section_data["accounts"]
                                if not isinstance(accts, list):
                                    self._send_json({"success": False, "message": "accounts must be a list"}, 400)
                                    return True
                                if len(accts) > 20:
                                    self._send_json({"success": False, "message": "accounts: max 20 accounts"}, 400)
                                    return True
                                existing = settings.get(section, {}).get("accounts", [])
                                for ai, acc in enumerate(accts):
                                    if not isinstance(acc, dict):
                                        self._send_json({"success": False, "message": "accounts[{}] must be object".format(ai)}, 400)
                                        return True
                                    ok, msg = validate_string(acc.get("uid", ""), min_len=1, max_len=50)
                                    if not ok:
                                        self._send_json({"success": False, "message": "accounts[{}].uid: {}".format(ai, msg)}, 400)
                                        return True
                                    ck = acc.get("cookie", "")
                                    if ck == "***":
                                        # 保留已有 cookie（脱敏占位），找不到则置空
                                        acc["cookie"] = ""
                                        for ea in existing:
                                            if ea.get("uid") == acc.get("uid"):
                                                acc["cookie"] = ea.get("cookie", "")
                                                break
                                    else:
                                        ok, msg = validate_string(ck, min_len=1, max_len=2000)
                                        if not ok:
                                            self._send_json({"success": False, "message": "accounts[{}].cookie: {}".format(ai, msg)}, 400)
                                            return True
                                    nm = acc.get("name", "")
                                    if nm:
                                        ok, msg = validate_string(nm, min_len=1, max_len=50)
                                        if not ok:
                                            self._send_json({"success": False, "message": "accounts[{}].name: {}".format(ai, msg)}, 400)
                                            return True

                        settings[section].update(section_data)

                save_settings(settings)
                notify_settings_changed()
                log("调度配置已更新")
                import json
                if json.dumps(settings, sort_keys=True) != json.dumps(old_settings, sort_keys=True):
                    add_exec_record("schedule", "update schedule")
                sse_broadcast("schedule_update", {})
                self._send_json({"success": True})
                return True

            elif action == "toggle":
                section = body.get("section", "")

                ok, msg = validate_string(section, min_len=1, max_len=50, allow_empty=False)
                if not ok:
                    self._send_json({"success": False, "message": "section: {}".format(msg)}, 400)
                    return True

                if section not in settings:
                    self._send_json({"success": False, "message": "unknown section: {}".format(section)}, 400)
                    return True

                enabled = body.get("enabled", False)
                if not isinstance(enabled, bool):
                    self._send_json({"success": False, "message": "enabled must be boolean"}, 400)
                    return True

                settings[section]["enabled"] = enabled
                save_settings(settings)
                notify_settings_changed()
                log("调度 {}: {}".format(section, "启用" if enabled else "停用"))
                add_exec_record("schedule", "toggle {}".format(section))
                sse_broadcast("schedule_update", {})
                self._send_json({"success": True})
                return True

            elif action == "run_now":
                section = body.get("section", "")

                ok, msg = validate_string(section, min_len=1, max_len=50, allow_empty=False)
                if not ok:
                    self._send_json({"success": False, "message": "section: {}".format(msg)}, 400)
                    return True

                if section == "transfer":
                    Thread(target=_run_scheduled_transfer, daemon=True).start()
                    self._send_json({"success": True, "message": "transfer started"})
                    return True
                elif section == "expired_check":
                    Thread(target=_run_scheduled_expired_check, daemon=True).start()
                    self._send_json({"success": True, "message": "expired_check started"})
                    return True

                self._send_json({"success": False, "message": "unknown section: {}".format(section)})
                return True

            else:
                self._send_json({"success": False, "message": "unknown action: {}".format(action)})
                return True

        return False
