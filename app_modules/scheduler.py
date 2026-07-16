import time, traceback
from datetime import datetime, timedelta
from threading import Thread, Lock, Event
from config import load_settings, ConfigManager, LOCAL_TZ
from douban import get_douban_list, get_douban_wishlist
from transfer import run_transfer, check_expired_tasks, fix_expired_tasks, is_in_qas, build_transfer_tasks, is_transfer_running
from storage import add_exec_record
from utils import log, http_post

try:
    from croniter import croniter as _croniter
    _has_croniter = True
except ImportError:
    _has_croniter = False

schedule_status = {"transfer_next": None, "expired_check_next": None,
                   "last_transfer": None, "last_expired_check": None}
schedule_lock = Lock()
_settings_changed = Event()

def _now_local():
    return datetime.now(LOCAL_TZ)

def _next_fire_time(time_str, cron_str, interval_hours=0, last_run=None):
    # 间隔模式：每 N 小时执行一次
    if interval_hours and interval_hours > 0:
        now = _now_local()
        if last_run:
            try:
                base = datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S")
                base = base.replace(tzinfo=LOCAL_TZ)
            except (ValueError, TypeError):
                base = now
        else:
            base = now
        return base + timedelta(hours=interval_hours)

    if cron_str:
        if not _has_croniter:
            log("croniter 未安装，无法解析 cron 表达式")
            return None
        try:
            ci = _croniter(cron_str, _now_local())
            return ci.get_next(datetime)
        except Exception as e:
            log("cron 解析错误: {}".format(e))
            return None
    if time_str and ":" in time_str:
        try:
            parts = time_str.split(":")
            now = _now_local()
            target = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        except ValueError as e:
            log("时间解析错误: {}".format(e))
            return None
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
    try:
        settings = load_settings()
        t = settings.get("transfer", {})
        if not t.get("enabled"): return
        tasks = list(t.get("tasks", []))
        limit = t.get("limit", 5)
        filters = t.get("filters", {})
        # 获取豆瓣想看列表作为额外任务来源
        wish_cfg = settings.get("douban_wish", {})
        if wish_cfg.get("enabled"):
            try:
                wish_items = get_douban_wishlist()
                if wish_items:
                    wish_savepath = wish_cfg.get("savepath", "/批量转存/想看")
                    wish_category = wish_cfg.get("category", ["movie"])
                    if isinstance(wish_category, str):
                        wish_category = [wish_category]
                    for item in wish_items:
                        item_cat = item.get("category", "movie")
                        if item_cat not in wish_category:
                            continue
                        tasks.append({
                            "path": "",
                            "type": "",
                            "savepath": wish_savepath,
                            "category": item_cat,
                            "title": item["title"],
                            "_wish": True
                        })
                    log("豆瓣想看列表已加载 {} 条".format(len(wish_items)))
            except Exception as e:
                log("豆瓣想看列表加载失败: {}".format(e))
        if not tasks: return
        if is_transfer_running():
            log("定时转存跳过：已有转存任务正在运行")
            return
        log("定时转存开始")
        uniq = build_transfer_tasks(tasks, filters)
        log("定时转存: {} 条".format(len(uniq)))
        with schedule_lock:
            schedule_status["last_transfer"] = _now_local().strftime("%Y-%m-%d %H:%M:%S")
        Thread(target=run_transfer, args=(uniq, limit), daemon=True).start()
    except Exception as e:
        log("定时转存执行错误: {}".format(e))
        traceback.print_exc()

def _run_scheduled_expired_check():
    try:
        settings = load_settings()
        ec = settings.get("expired_check", {})
        if not ec.get("enabled"): return
        if is_transfer_running():
            log("定时失效检测跳过：已有转存任务正在运行")
            return
        log("定时检测失效链接开始")
        expired = check_expired_tasks()
        with schedule_lock:
            schedule_status["last_expired_check"] = _now_local().strftime("%Y-%m-%d %H:%M:%S")
        if expired:
            add_exec_record("expired_check", "发现 {} 个失效链接".format(len(expired)), "fail",
                            data={"expired": [{"title": e.get("taskname", ""), "path": e.get("savepath", "")} for e in expired]})
            # 自动修复失效链接
            if ec.get("auto_fix"):
                log("自动修复失效链接开始")
                try:
                    fix_result = fix_expired_tasks()
                    add_exec_record("expired_check", "自动修复完成: 成功{} 失败{}".format(
                        fix_result.get("fixed", 0), fix_result.get("failed", 0)), "ok",
                        data=fix_result)
                except Exception as e:
                    log("自动修复失败: {}".format(e))
                    add_exec_record("expired_check", "自动修复失败: {}".format(e), "fail")
        else:
            add_exec_record("expired_check", "检测完成，无失效链接", "ok", data={"expired": []})
    except Exception as e:
        log("定时检测失效链接错误: {}".format(e))
        traceback.print_exc()

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

def notify_settings_changed():
    _settings_changed.set()

def scheduler_loop():
    while True:
        try:
            settings = _load_settings_cached()
            t = settings.get("transfer", {})
            e = settings.get("expired_check", {})
            t_next = _next_fire_time(t.get("time"), t.get("cron"), t.get("interval_hours", 0),
                                     schedule_status.get("last_transfer"))
            e_next = _next_fire_time(e.get("time"), e.get("cron"), e.get("interval_hours", 0),
                                     schedule_status.get("last_expired_check"))
            now = _now_local()
            with schedule_lock:
                schedule_status["transfer_next"] = t_next.strftime("%Y-%m-%d %H:%M") if t_next else None
                schedule_status["expired_check_next"] = e_next.strftime("%Y-%m-%d %H:%M") if e_next else None
            upcoming = []
            if t.get("enabled") and t_next: upcoming.append(("transfer", t_next))
            if e.get("enabled") and e_next: upcoming.append(("expired_check", e_next))
            if not upcoming:
                _settings_changed.wait(timeout=60)
                _settings_changed.clear()
                continue
            upcoming.sort(key=lambda x: x[1])
            name, target = upcoming[0]
            wait = max(0, (target - now).total_seconds())
            if wait > 120:
                if name == "transfer" and wait < 180:
                    tasks = t.get("tasks", [])
                    if tasks:
                        Thread(target=_warmup_douban, args=(tasks,), daemon=True).start()
                if _settings_changed.wait(timeout=min(wait - 60, 60)):
                    _settings_changed.clear()
                    continue
            elif wait > 5:
                if name == "transfer":
                    tasks = t.get("tasks", [])
                    if tasks:
                        Thread(target=_warmup_douban, args=(tasks,), daemon=True).start()
                if _settings_changed.wait(timeout=wait - 2):
                    _settings_changed.clear()
                    continue
            else:
                _settings_changed.wait(timeout=wait)
                _settings_changed.clear()
            now = _now_local()
            if target and now >= target - timedelta(seconds=2):
                if name == "transfer":
                    _run_scheduled_transfer()
                elif name == "expired_check":
                    _run_scheduled_expired_check()
                time.sleep(5)
        except Exception as e:
            log("调度错误: {}".format(e))
            traceback.print_exc()
            time.sleep(30)
