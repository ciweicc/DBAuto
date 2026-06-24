# scheduler.py — 定时任务调度、Cron 解析
import time, traceback
from datetime import datetime, timedelta, timezone
from threading import Thread, Lock
from config import load_settings, OPENLIST_URL, OPENLIST_TOKEN
from douban import get_douban_list
from transfer import run_transfer, check_expired_tasks, is_in_qas, build_transfer_tasks
from storage import add_exec_record
from utils import log, http_post

TZ = timezone(timedelta(hours=8))
schedule_status = {"transfer_next": None, "expired_check_next": None, "dir_cleanup_next": None,
                   "last_transfer": None, "last_expired_check": None, "last_dir_cleanup": None}
schedule_lock = Lock()

_cron_cache = {}
_cron_cache_lock = Lock()

def _now_local():
    return datetime.now(TZ)

def _parse_cron_field(field, min_val, max_val):
    if field == "*":
        return set(range(min_val, max_val + 1))
    result = set()
    for part in field.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            if base == "*":
                start = min_val
            elif "-" in base:
                s, e = base.split("-", 1)
                start, end = int(s), int(e)
                result.update(range(start, end + 1, step))
                continue
            else:
                start = int(base)
            result.update(range(start, max_val + 1, step))
        elif "-" in part:
            s, e = part.split("-", 1)
            result.update(range(int(s), int(e) + 1))
        else:
            result.add(int(part))
    return {v for v in result if min_val <= v <= max_val}

def _get_cron_fields(cron_str):
    with _cron_cache_lock:
        if cron_str in _cron_cache:
            return _cron_cache[cron_str]
    parts = cron_str.strip().split()
    if len(parts) != 5:
        return None
    try:
        sm, sh, sdom, smo, sdow = parts
        minutes = _parse_cron_field(sm, 0, 59)
        hours = _parse_cron_field(sh, 0, 23)
        days_of_month = _parse_cron_field(sdom, 1, 31)
        months = _parse_cron_field(smo, 1, 12)
        days_of_week = _parse_cron_field(sdow, 0, 6)
        result = (minutes, hours, days_of_month, months, days_of_week)
        with _cron_cache_lock:
            _cron_cache[cron_str] = result
        return result
    except Exception:
        return None

def invalidate_cron_cache():
    with _cron_cache_lock:
        _cron_cache.clear()

def _next_cron_time(cron_str, now_dt=None):
    if not cron_str:
        return None
    if now_dt is None:
        now_dt = _now_local()
    fields = _get_cron_fields(cron_str)
    if not fields:
        return None
    minutes, hours, days_of_month, months, days_of_week = fields
    dt = now_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if dt.month not in months:
            if dt.month >= 12:
                dt = dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                dt = dt.replace(month=dt.month + 1, day=1, hour=0, minute=0)
            continue
        if dt.day not in days_of_month or dt.weekday() not in days_of_week:
            dt += timedelta(days=1)
            dt = dt.replace(hour=0, minute=0)
            continue
        if dt.hour not in hours:
            if dt.hour >= 23:
                dt += timedelta(days=1)
                dt = dt.replace(hour=0, minute=0)
            else:
                dt = dt.replace(hour=dt.hour + 1, minute=0)
            continue
        if dt.minute not in minutes:
            dt += timedelta(minutes=1)
            if dt.minute == 0:
                continue
            continue
        return dt
    return None

def _next_fire_time(time_str, cron_str):
    if cron_str:
        return _next_cron_time(cron_str)
    if time_str and ":" in time_str:
        parts = time_str.split(":")
        now = _now_local()
        target = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return None

_douban_warmed_up_for = None

def _warmup_douban(tasks):
    global _douban_warmed_up_for
    task_key = tuple(sorted((t.get("path", ""), t.get("type", "")) for t in tasks))
    if _douban_warmed_up_for == task_key:
        return
    log("预热豆瓣数据...")
    for tk in tasks:
        try:
            get_douban_list(tk.get("path", ""), tk.get("type", ""), 20)
        except Exception as e:
            log("豆瓣预热失败 {}: {}".format(tk.get("path", ""), e))
    _douban_warmed_up_for = task_key
    log("豆瓣数据预热完成")

def _run_scheduled_transfer():
    settings = load_settings()
    t = settings.get("transfer", {})
    if not t.get("enabled"): return
    tasks = t.get("tasks", [])
    limit = t.get("limit", 5)
    if not tasks: return
    log("定时转存开始")
    uniq = build_transfer_tasks(tasks)
    log("定时转存: {} 条".format(len(uniq)))
    with schedule_lock:
        schedule_status["last_transfer"] = _now_local().strftime("%Y-%m-%d %H:%M:%S")
    Thread(target=run_transfer, args=(uniq, limit), daemon=True).start()
    add_exec_record("transfer", "transfer {} items".format(len(uniq)))

def _run_scheduled_expired_check():
    settings = load_settings()
    if not settings.get("expired_check", {}).get("enabled"): return
    log("定时检测失效链接开始")
    expired = check_expired_tasks()
    with schedule_lock:
        schedule_status["last_expired_check"] = _now_local().strftime("%Y-%m-%d %H:%M:%S")
    if expired:
        add_exec_record("expired_check", "found {} expired".format(len(expired)))
    else:
        add_exec_record("expired_check", "all ok", "ok")

def _run_scheduled_dir_cleanup():
    settings = load_settings()
    if not settings.get("dir_cleanup", {}).get("enabled"): return
    dirs = settings["dir_cleanup"].get("directories", [])
    if not dirs: return
    log("定时目录清理开始")
    removed, total = 0, 0
    for d in dirs:
        d = d.strip()
        if not d: continue
        try:
            data = http_post("{}/api/fs/list".format(OPENLIST_URL),
                             {"path": d, "password": "", "page": 1, "per_page": 5000,
                              "refresh": False, "token": OPENLIST_TOKEN}, timeout=20)
            if data.get("code") != 200: continue
            content = data.get("data", {}).get("content", [])
            if not content: continue
            total += len(content)
            for item in content:
                if item.get("is_dir") and not is_in_qas(item.get("name", "")):
                    try:
                        http_post("{}/api/fs/remove".format(OPENLIST_URL),
                                  {"names": ["{}/{}".format(d, item["name"])], "dir": d, "token": OPENLIST_TOKEN}, timeout=20)
                        removed += 1
                    except Exception as e:
                        log("删除目录失败 {}/{}: {}".format(d, item["name"], e))
            time.sleep(1)
        except Exception as e:
            log("目录清理错误: {}".format(e))
    log("目录清理: {}/{} 已删除".format(removed, total))
    add_exec_record("dir_cleanup", "removed {}/{}".format(removed, total), "ok")
    with schedule_lock:
        schedule_status["last_dir_cleanup"] = _now_local().strftime("%Y-%m-%d %H:%M:%S")

_settings_mtime = None
_settings_cache = None

def _load_settings_cached():
    global _settings_mtime, _settings_cache
    from config import SETTINGS_FILE
    import os
    try:
        mtime = os.path.getmtime(SETTINGS_FILE)
    except OSError:
        mtime = 0
    if _settings_cache is not None and _settings_mtime == mtime:
        return _settings_cache
    _settings_cache = load_settings()
    _settings_mtime = mtime
    return _settings_cache

def scheduler_loop():
    while True:
        try:
            settings = _load_settings_cached()
            t = settings.get("transfer", {})
            e = settings.get("expired_check", {})
            d = settings.get("dir_cleanup", {})
            t_next = _next_fire_time(t.get("time"), t.get("cron"))
            e_next = _next_fire_time(e.get("time"), e.get("cron"))
            d_next = _next_fire_time(d.get("time"), d.get("cron"))
            now = _now_local()
            with schedule_lock:
                schedule_status["transfer_next"] = t_next.strftime("%Y-%m-%d %H:%M") if t_next else None
                schedule_status["expired_check_next"] = e_next.strftime("%Y-%m-%d %H:%M") if e_next else None
                schedule_status["dir_cleanup_next"] = d_next.strftime("%Y-%m-%d %H:%M") if d_next else None
            upcoming = []
            if t.get("enabled") and t_next: upcoming.append(("transfer", t_next))
            if e.get("enabled") and e_next: upcoming.append(("expired_check", e_next))
            if d.get("enabled") and d_next: upcoming.append(("dir_cleanup", d_next))
            if not upcoming:
                time.sleep(60)
                continue
            upcoming.sort(key=lambda x: x[1])
            name, target = upcoming[0]
            wait = max(0, (target - now).total_seconds())
            if wait > 120:
                if name == "transfer" and wait < 180:
                    tasks = t.get("tasks", [])
                    if tasks:
                        Thread(target=_warmup_douban, args=(tasks,), daemon=True).start()
                time.sleep(min(wait - 60, 60))
                continue
            elif wait > 5:
                if name == "transfer":
                    tasks = t.get("tasks", [])
                    if tasks:
                        Thread(target=_warmup_douban, args=(tasks,), daemon=True).start()
                time.sleep(wait - 2)
                continue
            time.sleep(max(0, wait))
            if name == "transfer":
                _run_scheduled_transfer()
            elif name == "expired_check":
                _run_scheduled_expired_check()
            elif name == "dir_cleanup":
                _run_scheduled_dir_cleanup()
            time.sleep(5)
        except Exception as e:
            log("调度错误: {}".format(e))
            traceback.print_exc()
            time.sleep(30)
