# scheduler.py — 定时任务调度、Cron 解析
import time, traceback
from datetime import datetime, timedelta, timezone
from threading import Thread, Lock
from config import load_settings
from douban import get_douban_list
from transfer import run_transfer, check_expired_tasks, is_in_qas
from storage import add_exec_record
from utils import log, http_post

TZ = timezone(timedelta(hours=8))
schedule_status = {"transfer_next": None, "expired_check_next": None, "dir_cleanup_next": None,
                   "last_transfer": None, "last_expired_check": None, "last_dir_cleanup": None}
schedule_lock = Lock()

def _now_local():
    return datetime.now(TZ)

def _next_cron_time(cron_str, now_dt=None):
    if not cron_str:
        return None
    if now_dt is None:
        now_dt = _now_local()
    parts = cron_str.strip().split()
    if len(parts) != 5:
        return None
    try:
        sm, sh, sdom, smo, sdow = parts
        dt = now_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(525600):
            if sm != "*" and dt.minute != int(sm):
                dt += timedelta(minutes=1); continue
            if sh != "*" and dt.hour != int(sh):
                dt = dt.replace(hour=int(sh), minute=0); continue
            if sdom != "*" and dt.day != int(sdom):
                dt += timedelta(days=1); dt = dt.replace(hour=0, minute=0); continue
            if smo != "*" and dt.month != int(smo):
                dt = dt.replace(year=dt.year + 1 if dt.month >= int(smo) else dt.year,
                                month=int(smo), day=1, hour=0, minute=0); continue
            if sdow != "*":
                target_wday = int(sdow)
                if dt.weekday() != target_wday:
                    days_ahead = (target_wday - dt.weekday()) % 7
                    if days_ahead == 0: days_ahead = 7
                    dt += timedelta(days=days_ahead)
                    dt = dt.replace(hour=0, minute=0); continue
            return dt
        return None
    except Exception:
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

def _run_scheduled_transfer():
    from config import OPENLIST_URL, OPENLIST_TOKEN
    settings = load_settings()
    t = settings.get("transfer", {})
    if not t.get("enabled"): return
    tasks = t.get("tasks", [])
    limit = t.get("limit", 5)
    if not tasks: return
    log("定时转存开始")
    all_t = []
    for tk in tasks:
        try:
            items = get_douban_list(tk["path"], tk["type"], 20)
            for i in items:
                all_t.append({"title": i["title"], "savepath": tk["savepath"],
                              "category": tk.get("category", "movie")})
        except Exception as e:
            log("获取错误: {}".format(e))
    seen = set()
    uniq = []
    for ti in all_t:
        if ti["title"] not in seen:
            seen.add(ti["title"]); uniq.append(ti)
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
    from config import OPENLIST_URL, OPENLIST_TOKEN
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
                    except Exception: pass
            time.sleep(1)
        except Exception as e:
            log("目录清理错误: {}".format(e))
    log("目录清理: {}/{} 已删除".format(removed, total))
    add_exec_record("dir_cleanup", "removed {}/{}".format(removed, total), "ok")
    with schedule_lock:
        schedule_status["last_dir_cleanup"] = _now_local().strftime("%Y-%m-%d %H:%M:%S")

def scheduler_loop():
    while True:
        try:
            settings = load_settings()
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
                time.sleep(30); continue
            upcoming.sort(key=lambda x: x[1])
            name, target = upcoming[0]
            wait = max(0, (target - now).total_seconds())
            if wait > 60:
                time.sleep(min(wait, 30)); continue
            time.sleep(max(0, wait))
            if name == "transfer": _run_scheduled_transfer()
            elif name == "expired_check": _run_scheduled_expired_check()
            elif name == "dir_cleanup": _run_scheduled_dir_cleanup()
            time.sleep(5)
        except Exception as e:
            log("调度错误: {}".format(e))
            traceback.print_exc()
            time.sleep(30)
