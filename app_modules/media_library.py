# media_library.py — 双向管理：QAS 任务列表 / 夸克网盘媒体库浏览 / 删除 + 历史同步
#
# 背景（issue #27）：
#   DBAuto 只做「正向」转存——把豆瓣/TMDB 条目经 PanSou 搜索后交给 QAS 转存。
#   但用户在夸克网盘里手工删掉某部影片后，QAS 的 tasklist 与 DBAuto 的转存历史
#   都不会变，于是①QAS 仍保留失效任务、②DBAuto 仍认为「已转存」而拒绝重新转存。
#
# 本模块补齐反向链路，能力分三层：
#   1. list_tasks()      —— 读出 QAS tasklist（供前端展示）
#   2. list_library()    —— 调 QAS /get_savepath_detail 列出网盘真实文件（媒体库）
#   3. delete_entries()  —— 调 QAS /delete_file 删除网盘文件，并按需联动清理
#                          DBAuto 转存历史（双向同步的核心）
#   4. delete_tasks()    —— 调 QAS /update 写回删减后的 tasklist，即「删除条目任务配置」
#                          （用户确认：QAS 为自部署，不存在改别人任务配置的问题）
#
# 设计约束：
#   - 所有 QAS 调用都复用 QASClient（token 注入 / 超时策略与其它链路一致）；
#   - 删除是破坏性操作，默认「先删网盘、再同步历史」，任一步失败都要如实回报，
#     不做静默降级，避免用户以为同步成功而实际只删了一半。
import threading
import time

import openlist
from api_client import QASClient
from config import ConfigManager
from storage import delete_history_item
from utils import log

# 网盘目录浏览默认路径（设置里未配置「媒体库根目录」时的兜底入口）
DEFAULT_LIBRARY_ROOT = "/"


def get_library_root():
    """读取设置里的「媒体库根目录」（savepaths.library_root）。

    返回规范化后的绝对路径：以 `/` 开头、去掉结尾多余的 `/`（根目录除外）。
    任何读取异常/非法值都回落到 DEFAULT_LIBRARY_ROOT，保证浏览入口永远可用。
    """
    try:
        settings = ConfigManager.get_instance().get_settings()
    except Exception:
        return DEFAULT_LIBRARY_ROOT
    raw = (settings or {}).get("savepaths", {}).get("library_root", DEFAULT_LIBRARY_ROOT)
    if not isinstance(raw, str):
        return DEFAULT_LIBRARY_ROOT
    root = raw.strip()
    if not root or not root.startswith("/") or ".." in root:
        return DEFAULT_LIBRARY_ROOT
    if len(root) > 1:
        root = root.rstrip("/") or DEFAULT_LIBRARY_ROOT
    return root

# 单次删除的条目上限，防止误操作/脚本化批量删库
MAX_DELETE_BATCH = 50

# 单次删除的 QAS 任务条数上限。任务配置比网盘文件更「不可再生」
# （shareurl / pattern / replace 全靠手工配），上限取更保守的值。
MAX_TASK_DELETE_BATCH = 20

# 读取类接口（任务列表 / 目录浏览）的单次尝试超时预算。
#
# 为什么单独给读接口收窄超时（issue #27「网盘文件模块访问有点慢」）：
#   QASClient 默认 timeout=15，而 utils.http_get 对网络层错误会重试 3 次，
#   单次目录请求的最坏等待因此达到 15+1+5+5+5+1 ≈ 32s，前端一直停在「加载中」。
#   目录浏览是「点一下就该出结果」的交互，宁可快速失败并提示重试，
#   也不要让用户对着转圈等待半分钟。
# 删除是破坏性且需要确认结果的操作，保持原有 15s 预算不变。
LIBRARY_READ_TIMEOUT = 6

# ---- 读取总预算（issue #27 反馈「每次打开都要等几秒」） ----
#
# 上一轮只把「单次尝试」从 15s 收到 6s，但**重试次数没变**：
# utils.http_get 默认 retries=3、HTTP_RETRY_TOTAL=12s，于是单次请求最坏
#   attempt1 卡满 6s + 退避 ~0.5s + attempt2 卡满 6s > 12s 预算 → 放弃
# ≈ 12.5s 全部压给用户。打开媒体库时目录与任务**并发**发起，
# 而 requests 的 Session 连接池是线程安全的，两条链路互不拖累（实测并发≈串行），
# 所以「打开要等几秒」正是这两条 12s 预算的链路在同时空转。
#
# 对策：
#   1. 读接口的**总预算**由调用方显式给出（read_budget），超过就放弃重试、
#      如实抛出超时，让前端立刻进「读取失败 + 重试」；
#   2. 总预算必须 > 单次尝试超时，否则第一次尝试就被掐断、
#      真实是「服务慢」的场景会被误报成「QAS 不可达」。
LIBRARY_READ_BUDGET = 7.0

# 目录浏览的**短命缓存**（issue #27）。
#
# 目的不是「少一次网络」，而是让**重复打开**媒体库页（切标签 / 刷新页面 / 从子目录
# 退回来）不走网络：QAS 侧每次 /get_savepath_detail 都要拿 Cookie 向夸克实查目录，
# 单次 0.5~3s 是常态，缓存直接把这部分等待抹掉。
#
# 语义（宁可慢也不要错）：
#   - 只缓存**成功**结果，失败不缓存（失败要能真的重试）；
#   - TTL 取 30s：媒体库是「看一眼 → 决定删哪个」的交互，
#     在这个尺度上数据不会显著过期，而删除后由 delete_entries/delete_tasks
#     显式 invalidate（delete 路径本身也走 force 刷新，双保险）；
#   - 缓存的是**已经归一化**的响应字典，调用方拿到的结构与实时请求完全一致，
#     因此「缓存命中的渲染路径」与「网络返回的渲染路径」不会产生第二套语义。
LIBRARY_CACHE_TTL = 30.0
LIBRARY_CACHE_MAX = 64


def _client(timeout=15, retries=None):
    """构造 QAS 客户端。

    retries 显式下传到底层 http_get/http_post（见 utils.http_get 的 retries 参数）：
    读接口用 1 次尝试，不让「内部重试 × 外层重试」相乘放大等待。
    """
    cfg = ConfigManager.get_instance()
    client = QASClient(cfg.qas, cfg.qas_token, timeout=timeout)
    if retries is not None:
        client.retries = retries
    return client


def _cache_key(path, fid=None):
    """目录缓存的键：优先 fid（重命名/移动后仍稳定），否则用规范化的 path。"""
    if fid not in (None, ""):
        return "fid:{}".format(fid)
    p = path if path not in (None, "") else DEFAULT_LIBRARY_ROOT
    p = str(p).strip() or DEFAULT_LIBRARY_ROOT
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    return "path:{}".format(p)


def _read_client(budget=None):
    """读接口客户端：单次尝试 LIBRARY_READ_TIMEOUT，**不做**底层重试。

    底层 http_get 的重试对「QAS 卡住/不可达」毫无帮助（重试的仍是同一个卡住的
    服务），只会把等待翻倍。失败快速返回给前端，由用户点「重试」决定是否再来一次。
    """
    budget = LIBRARY_READ_BUDGET if budget is None else budget
    return _client(min(LIBRARY_READ_TIMEOUT, max(1.0, float(budget))), retries=1)


# ---------- 目录短命缓存 ----------

_lib_cache = {}            # path -> {"ts": float, "data": dict}
_lib_cache_lock = threading.Lock()


def _lib_cache_get(path):
    """取缓存（顺带惰性清理过期项）。返回响应字典的**浅拷贝**，避免调用方改写缓存。"""
    now = time.time()
    with _lib_cache_lock:
        item = _lib_cache.get(path)
        if not item:
            return None
        if now - item["ts"] >= LIBRARY_CACHE_TTL:
            _lib_cache.pop(path, None)
            return None
        return dict(item["data"])


def _lib_cache_put(path, data):
    with _lib_cache_lock:
        if len(_lib_cache) >= LIBRARY_CACHE_MAX:
            # 简单淘汰：丢掉最旧的一条（目录浏览的访问局部性很强，这足够）
            oldest = min(_lib_cache.items(), key=lambda kv: kv[1]["ts"])[0]
            _lib_cache.pop(oldest, None)
        _lib_cache[path] = {"ts": time.time(), "data": dict(data)}


def invalidate_library_cache(path=None):
    """作废目录缓存。

    path 为 None 时清空全部（删除操作可能同时影响父目录与当前目录，
    而父目录条目数/大小也会变，所以删除后走全清更稳）。
    """
    with _lib_cache_lock:
        if path is None:
            _lib_cache.clear()
        else:
            _lib_cache.pop(path, None)


def _resp_ok(res):
    return isinstance(res, dict) and bool(res.get("success"))


def list_tasks(budget=None):
    """读取 QAS 的 tasklist（只读）。

    返回 {"success": bool, "tasks": [...], "message": str}。
    每个 task 原样透传 QAS 字段（taskname / shareurl / savepath / pattern 等）。
    预算/重试口径见模块头部「读取总预算」：不做底层重试，失败快速返回。
    """
    try:
        res = _read_client(budget).get_data()
    except Exception as e:
        log("读取 QAS 任务失败: {}".format(e))
        return {"success": False, "tasks": [], "message": "读取 QAS 任务失败: {}".format(e)}
    if not _resp_ok(res):
        return {"success": False, "tasks": [],
                "message": (res or {}).get("message", "QAS 返回异常")}
    data = res.get("data", {}) or {}
    tasks = data.get("tasklist", []) or []
    return {"success": True, "tasks": tasks, "message": ""}


def list_library(path=None, fid=None, budget=None, use_cache=True):
    """列出夸克网盘指定目录下的真实文件（媒体库视图）。

    返回 {"success": bool, "path": str, "fid": ..., "entries": [...], "message": str}。
    entries 由 QAS 原样返回，含 fid / file_name / dir / size / updated_at。

    use_cache=True 时命中目录短命缓存（LIBRARY_CACHE_TTL）直接返回，不再打网络；
    缓存只存成功结果，删除/写回后由 invalidate_library_cache() 显式作废
    （见文件头「目录短命缓存」）。
    """
    key = _cache_key(path, fid)
    if use_cache:
        cached = _lib_cache_get(key)
        if cached is not None:
            return cached
    try:
        res = _read_client(budget).get_savepath_detail(path=path, fid=fid)
    except Exception as e:
        log("读取网盘目录失败: {}".format(e))
        return {"success": False, "path": path or DEFAULT_LIBRARY_ROOT,
                "entries": [], "message": "读取网盘目录失败: {}".format(e)}
    if not _resp_ok(res):
        return {"success": False, "path": path or DEFAULT_LIBRARY_ROOT, "entries": [],
                "message": (res or {}).get("message", "QAS 返回异常")}
    data = res.get("data", {}) or {}
    entries = data.get("list", []) or []
    # 归一化字段：QAS 用 file_name，前端统一用 name；dir 为 True 表示目录
    normalized = []
    for it in entries:
        if not isinstance(it, dict):
            continue
        normalized.append({
            "fid": it.get("fid"),
            "name": it.get("file_name", ""),
            "dir": bool(it.get("dir")),
            "size": it.get("size", 0),
            "updated_at": it.get("updated_at"),
        })
    payload = {
        "success": True,
        "path": data.get("paths") and path or (path or DEFAULT_LIBRARY_ROOT),
        "fid": data.get("fid"),
        "paths": data.get("paths", []),
        "entries": normalized,
        "message": "",
    }
    if use_cache:
        _lib_cache_put(key, payload)
    return payload


def _sync_history_for(name, sync_history):
    """按需把删除结果同步到 DBAuto 转存历史。

    网盘里的文件名通常带后缀/清晰度（如 `复仇者联盟4.2019.2160p.mkv`），
    故历史命中用「显式传入 title」优先，其次用文件名直接匹配；
    不做模糊匹配，避免误删历史（宁可不删，也不误删）。
    """
    if not sync_history or not name:
        return False
    try:
        return delete_history_item(name)
    except Exception as e:
        log("同步清理历史失败 {}: {}".format(name, e))
        return False


def delete_entries(entries, sync_history=True):
    """删除网盘条目，并按需同步清理 DBAuto 历史。

    entries: [{"fid": "...", "path": "...", "name": "...", "title": "..."}]
             fid 优先，其次 path（由 QAS 反查 fid）；title 可选，用于精确同步历史。
    返回 {"success": bool, "deleted": n, "failed": n, "history_synced": n, "results": [...]}

    语义：只要没有任何「网盘删除成功」，success 即 False；部分成功时返回 success=False
    并在 results 里逐条说明，避免调用方误判为全量成功。
    """
    entries = entries or []
    if not isinstance(entries, list):
        return {"success": False, "deleted": 0, "failed": 0, "history_synced": 0,
                "results": [], "message": "entries 必须是列表"}
    if len(entries) > MAX_DELETE_BATCH:
        return {"success": False, "deleted": 0, "failed": 0, "history_synced": 0,
                "results": [],
                "message": "单次最多删除 {} 条".format(MAX_DELETE_BATCH)}

    results = []
    deleted = failed = synced = 0
    client = _client()
    for item in entries:
        if not isinstance(item, dict):
            continue
        fid = item.get("fid")
        path = item.get("path")
        name = item.get("name") or item.get("title") or path or ""
        if fid in (None, "") and not path:
            results.append({"name": name, "ok": False, "message": "缺少 fid 与 path"})
            failed += 1
            continue
        try:
            res = client.delete_file(path=path, fid=fid)
        except Exception as e:
            log("删除网盘文件失败 {}: {}".format(name, e))
            results.append({"name": name, "ok": False, "message": "删除请求异常: {}".format(e)})
            failed += 1
            continue
        if _resp_ok(res):
            deleted += 1
            hist_title = item.get("title") or name
            if _sync_history_for(hist_title, sync_history):
                synced += 1
                results.append({"name": name, "ok": True, "message": "已删除", "history": "已同步清理"})
            else:
                results.append({"name": name, "ok": True, "message": "已删除", "history": "历史无匹配"})
        else:
            failed += 1
            results.append({"name": name, "ok": False,
                            "message": (res or {}).get("message", "删除失败")})

    if deleted:
        # 目录内容已变（条目数 / 大小 / 修改时间都会变），全量作废缓存，
        # 否则用户删完再进同一目录还会看到已删的条目（issue #27「双向管理」的底线）。
        invalidate_library_cache()
    return {
        "success": deleted > 0 and failed == 0,
        "deleted": deleted,
        "failed": failed,
        "history_synced": synced,
        "results": results,
        "message": "" if deleted else "没有条目被删除",
    }


# ---------- QAS 任务条目删除（issue #27 跟进） ----------
#
# 背景：用户在夸克网盘删片后，QAS 的 tasklist 里那条订阅任务仍会每天跑一次
# （每次都「没有新的转存任务」，或对失效链接反复告警），需要在 DBAuto 里直接删掉。
# 用户确认 QAS 为自部署服务，因此「改任务配置」不存在影响他人的顾虑。
#
# 为什么不用「读-改-写」里的「只发被删项」：QAS `/update` 是整表替换，
# 少发一条就等于删掉其余全部任务。这里的做法是：读出全量 tasklist → 剔除目标
# → 整体写回，且在拼装阶段就校验「写回条目数 == 原条目数 - 删除数」，不成立则拒绝提交。
# 唯一允许的裁剪是精确命中的那一条。

def _task_key(task):
    """任务列表项的定位键（QAS 不保证 taskname 唯一，故按 (taskname, savepath) 定位）。

    savepath 缺失时退化为 None，仅按 taskname 匹配——由 QAS 的
    `/api/add_task` 保证「添加即写盘」的条目一定有 taskname，同名同路径的
    重复任务在定位语义上等价，删哪一条都不影响其它任务。
    """
    if not isinstance(task, dict):
        return None
    return (str(task.get("taskname", "")), str(task.get("savepath", "")))


def _parse_task_key(item):
    """把前端传入的任务定位参数归一化成 (taskname, savepath)。"""
    if not isinstance(item, dict):
        return None
    if "index" in item and item.get("index") is not None:
        try:
            return ("__index__", int(item["index"]))
        except (TypeError, ValueError):
            return None
    name = item.get("taskname")
    if name in (None, ""):
        return None
    return (str(name), str(item.get("savepath", "")))


# ---------- 同名任务联动删除（issue #57） ----------
#
# 用户诉求原文：「给 OpenList 模块增加一个开关用于删除 OpenList 指定文件夹下的
# 资源库文件时同步删除同名自动夸克转存任务」。
#
# 为什么这条联动是必要的：网盘里删了文件，QAS 的订阅任务不会自己消失，
# 它第二天照跑一次并把同一作品重新转存回来 —— 用户于是永远在做两步
# （删文件、再删任务），而且经常漏掉第二步。把两步合成一步是可选项，
# **默认关**（删任务不可逆）。
#
# 匹配规则刻意保守，且与前端提示一一对应：
#   1. 任务名与所删条目名**互相包含**（≥2 字）才算同名 —— 这是 QAS 的实际形态
#      决定的：taskname 常见「[已完结]作品名」「作品名 全12集」，而条目名是
#      「作品名」「作品名.2024.2160p.mkv」。详见 openlist._match_task_dir。
#   2. 绝不把「条目名去扩展名后去匹配 savepath」当成匹配依据：路径相似不代表
#      是同一部作品（`/影视/电影` 下什么都对得上）。
#   3. 范围必须收窄：调用方给了路径过滤（用户在界面里的筛选/当前目录）时，
#      只动 savepath 命中该范围的任务。不给范围 = 拿一个文件名去撞整表。
#
# 删任务复用 delete_tasks()（整表写回 + 一致性校验），不自己拼 /update ——
# QAS 的 /update 是整表替换语义，少发一条就等于删掉其余全部任务。
TASK_SYNC_MIN_LEN = 2


def _task_matches_entry(taskname, entry_name):
    """任务名与条目名是否「同名」（互相包含，≥2 字）。"""
    a = str(taskname or "").strip()
    b = str(entry_name or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= TASK_SYNC_MIN_LEN and len(b) >= TASK_SYNC_MIN_LEN:
        return a in b or b in a
    return False


def match_tasks_by_names(tasks, names, path_filter=""):
    """从 tasklist 里挑出「与给定条目名同名」的任务（供删除前的预览与断言）。

    返回 [{"taskname", "savepath", "matched_name"}]，顺序与原 tasklist 一致。
    """
    out = []
    scope = str(path_filter or "").strip()
    for t in (tasks or []):
        if not isinstance(t, dict):
            continue
        name = t.get("taskname")
        if not name:
            continue
        if scope and scope not in str(t.get("savepath") or ""):
            continue
        for n in (names or []):
            if _task_matches_entry(name, n):
                out.append({"taskname": name, "savepath": t.get("savepath", ""),
                            "matched_name": str(n)})
                break
    return out


def sync_tasks_by_names(names, path_filter="", tasks=None, max_delete=None):
    """按「同名」联动删除 QAS 任务（issue #57）。

    返回 delete_tasks() 的结果字典，外加：
      - "matched": 命中的任务数（0 表示网盘侧删了但没有任何任务与之同名，
        这是**正常结果**，不是错误 —— 调用方要如实告诉用户）；
      - "names"/"path_filter": 回显本次的匹配口径，便于前端与日志核对。
    """
    list_res = None
    if tasks is None:
        list_res = list_tasks()
        if not list_res.get("success"):
            return {"success": False, "deleted": 0, "failed": 0, "results": [],
                    "matched": 0, "names": list(names or []),
                    "path_filter": path_filter,
                    "message": list_res.get("message", "读取 QAS 任务失败")}
        tasks = list_res.get("tasks", [])
    hits = match_tasks_by_names(tasks, names, path_filter)
    if not hits:
        # 一条都没撞上：**不去写 QAS 任务表**（一次多余的整表写回是纯粹的
        # 不可逆风险），如实回报「未匹配到同名任务」。
        return {"success": True, "deleted": 0, "failed": 0, "results": [],
                "matched": 0, "names": list(names or []), "path_filter": path_filter,
                "message": "未匹配到同名任务"}
    if max_delete is not None and len(hits) > max_delete:
        # 撞上的条数超过批量上限：宁可整批不删，也不做「删一半」的不可逆操作。
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "matched": len(hits), "names": list(names or []),
                "path_filter": path_filter,
                "message": "同名任务 {} 条，超过单次上限 {}，未做任何改动".format(
                    len(hits), max_delete)}
    res = delete_tasks([{"taskname": h["taskname"], "savepath": h["savepath"]}
                        for h in hits])
    res["matched"] = len(hits)
    res["names"] = list(names or [])
    res["path_filter"] = path_filter
    res["matched_tasks"] = hits
    return res


def sync_library_entries_by_names(names, path_filter="", max_delete=MAX_DELETE_BATCH):
    """按「同名」联动删除网盘条目（issue #57 的反方向）。

    场景与 sync_tasks_by_names 对称：用户在 QAS 侧删掉任务后，网盘里那份已转存的
    内容就成了没人管的孤儿（任务对账里报的「orphans」）。这里把它一起清掉。

    安全性来自三条与正向完全一致的约束：
      1. **只匹配任务名/条目名互为包含**（≥2 字），不做模糊相似度；
      2. 范围由调用方给的 path_filter 收窄（当前**浏览目录**），
         未给范围时只扫媒体库根目录，绝不从网盘根开始全盘扫；
      3. 只删**目录条目**（文件/文件夹），且最多 max_delete 条 —— 宁可不动，
         也不做「删一半」的不可逆操作。
    """
    if not openlist.is_configured():
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "matched_names": [], "message": "未配置 OpenList 地址"}
    clean = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    if not clean:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "matched_names": [], "message": "未指定任务名"}
    root = str(path_filter or "").strip() or get_library_root()
    if ".." in root:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "matched_names": [], "message": "路径不允许包含 .."}
    try:
        scanned, _truncated = openlist.scan_dir_names(root)
    except Exception as e:
        log("扫描网盘目录失败: {}".format(e))
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "matched_names": [], "message": "扫描网盘目录失败: {}".format(e)}

    # 命中项：(所在目录, 条目名)。目录必须来自扫描结果 —— 猜父目录会删错东西。
    hits = []
    for d, names_in_dir in scanned.items():
        for n in names_in_dir:
            for want in clean:
                if openlist._match_task_dir({"taskname": want},
                                            {n})[0]:
                    hits.append({"dir": d, "name": n, "taskname": want})
                    break
    if not hits:
        return {"success": True, "deleted": 0, "failed": 0, "results": [],
                "matched_names": [], "scanned_dirs": len(scanned),
                "message": "网盘上未匹配到同名条目"}
    if len(hits) > max_delete:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "matched_names": [h["name"] for h in hits],
                "scanned_dirs": len(scanned),
                "message": "同名条目 {} 条，超过单次上限 {}，未做任何改动".format(
                    len(hits), max_delete)}

    # 按目录分组调用 openlist.remove（它一次只接受一个目录 + 一批条目名）
    by_dir = {}
    for h in hits:
        by_dir.setdefault(h["dir"], []).append(h["name"])
    deleted = failed = 0
    results = []
    for d, ns in by_dir.items():
        r = openlist.remove(ns, dir_path=d)
        if r.get("success"):
            deleted += len(ns)
            for n in ns:
                results.append({"name": n, "dir": d, "ok": True, "message": "已删除"})
        else:
            failed += len(ns)
            for n in ns:
                results.append({"name": n, "dir": d, "ok": False,
                                "message": r.get("message", "删除失败")})
    if deleted:
        invalidate_library_cache()
    return {"success": deleted > 0 and failed == 0, "deleted": deleted, "failed": failed,
            "results": results, "matched_names": [h["name"] for h in hits],
            "scanned_dirs": len(scanned),
            "message": "" if deleted else "没有条目被删除"}


def delete_tasks(items):
    """删除 QAS tasklist 中的条目任务配置。

    items: [{"taskname": "xxx", "savepath": "/媒体库/xxx"}] 或 [{"index": 3}]
           （index 为 `/api/qas/tasks` 返回列表中的下标，用于容忍拖动排序后的陈旧视图）
    返回 {"success": bool, "deleted": n, "failed": n, "results": [...], "tasks": [...]}
      - success 仅在「目标全部命中并写回成功」时为 True；
      - tasks 是写回后的最新 tasklist，供前端直接重渲染，避免再打一次网络。

    失败语义（防误判）：只要有任一目标在 QAS 中找不到，就**整批不写回**并返回
    success=False。原因是一次写回会影响整张 tasklist，半途只删掉一部分、
    另一部分悄悄漏掉，比直接报错更危险。
    """
    items = items or []
    if not isinstance(items, list):
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "items 必须是列表"}
    if not items:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "未指定要删除的任务"}
    if len(items) > MAX_TASK_DELETE_BATCH:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "单次最多删除 {} 条任务".format(MAX_TASK_DELETE_BATCH)}

    try:
        res = _client().get_data()
    except Exception as e:
        log("读取 QAS 任务失败: {}".format(e))
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "读取 QAS 任务失败: {}".format(e)}
    if not _resp_ok(res):
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": (res or {}).get("message", "QAS 返回异常")}

    data = res.get("data", {}) or {}
    tasks = data.get("tasklist", []) or []
    if not isinstance(tasks, list):
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "QAS tasklist 格式异常"}

    # 1. 逐个定位目标（不做模糊匹配：删任务是不可逆的，宁可不删也不误删）
    keys = [_parse_task_key(it) for it in items]
    targets = []
    results = []
    missing = 0
    for item, key in zip(items, keys):
        label = (item.get("taskname") if isinstance(item, dict) else None) or "该任务"
        if key is None:
            results.append({"name": label, "ok": False,
                            "message": "缺少 taskname 或 index"})
            missing += 1
            continue
        idx = None
        if key[0] == "__index__":
            if 0 <= key[1] < len(tasks):
                idx = key[1]
        else:
            found = [i for i, t in enumerate(tasks) if _task_key(t) == key]
            # 同键多条时取第一条：它们在定位语义上等价，删除其中一条即可
            idx = found[0] if found else None
        if idx is None:
            results.append({"name": label, "ok": False,
                            "message": "QAS 中未找到该任务（可能已被删除）"})
            missing += 1
            continue
        if idx in targets:
            results.append({"name": label, "ok": False, "message": "重复的删除目标"})
            missing += 1
            continue
        targets.append(idx)
        results.append({"name": label, "ok": True, "message": "已从 QAS 移除"})

    if missing:
        return {"success": False, "deleted": 0, "failed": missing, "results": results,
                "message": "有 {} 条任务未能在 QAS 中定位，未做任何改动".format(missing)}

    # 2. 拼装写回列表并做一致性校验（防「误删其它任务」这类不可逆事故）
    drop = set(targets)
    new_tasks = [t for i, t in enumerate(tasks) if i not in drop]
    if len(new_tasks) != len(tasks) - len(drop):
        return {"success": False, "deleted": 0, "failed": len(drop), "results": results,
                "message": "写回列表条目数校验失败，已中止（未改动 QAS）"}

    # 3. 整体写回
    try:
        up = _client().update_tasklist(new_tasks)
    except Exception as e:
        log("写回 QAS 任务失败: {}".format(e))
        return {"success": False, "deleted": 0, "failed": len(drop), "results": results,
                "message": "写回 QAS 任务失败: {}".format(e)}
    if not _resp_ok(up):
        return {"success": False, "deleted": 0, "failed": len(drop), "results": results,
                "message": (up or {}).get("message", "QAS 写回失败")}

    # 4. 同步 DBAuto 的 QAS 任务缓存，避免「删完仍被判为已转存」
    from transfer import remove_from_qas
    for i in sorted(drop, reverse=True):
        t = tasks[i]
        name = t.get("taskname") if isinstance(t, dict) else None
        if name:
            try:
                remove_from_qas(name)
            except Exception as e:
                log("同步 QAS 任务缓存失败 {}: {}".format(name, e))

    log("已删除 QAS 任务 {} 条: {}".format(
        len(drop), [tasks[i].get("taskname") for i in sorted(drop)]))
    # 任务写回本身不改网盘目录，但用户随后往往会去删对应文件；
    # 这里只作废缓存不额外打网络，避免陈旧视图（代价是一次重查）。
    invalidate_library_cache()
    return {"success": True, "deleted": len(drop), "failed": 0,
            "results": results, "tasks": new_tasks, "message": ""}


# ---------- 媒体库数据源（issue #27 追问：用 OpenList 回答目录浏览） ----------
#
# 用户原话：「可以通过调用 openlist 进行管理吗，但我同时需要管理夸克自动转存的
# 自动任务条目的需求。当前通过夸克自动转存的媒体库访问速度太慢了」。
#
# 「慢」的链路是：浏览器 → DBAuto → QAS → 夸克开放接口，每一层都是实时往返，
# 且 QAS 的 /get_savepath_detail 没有目录缓存 —— 目录一大就要等好几秒。
# 引入 OpenList 后，目录浏览改由 OpenList 的文件系统视图回答
# （它对夸克驱动有列表缓存），token 也在进程内复用，因此首屏明显更快。
#
# 三条设计底线：
#   1. **不破坏可用性**：OpenList 未配置 / 不可达时自动回落到 QAS 直连，
#      媒体库不会因为「没配 OpenList」而打不开；
#   2. **不猜**：由谁回答必须如实告诉前端（source 字段），用户才知道
#      自己看到的是 OpenList 的缓存视图还是 QAS 的实时视图；
#   3. **删除仍走真实数据源**：OpenList 浏览出来的条目在 OpenList 侧删，
#      QAS 浏览出来的条目在 QAS 侧删；两边都不是「猜路径」，而是用真实条目名。

LIBRARY_SOURCE_QAS = "qas"
LIBRARY_SOURCE_OPENLIST = "openlist"


def library_source():
    """当前媒体库目录浏览使用的数据源。

    优先 OpenList（更快，且是本项目的「媒体库视图」需求），未配置时回落 QAS。
    """
    return LIBRARY_SOURCE_OPENLIST if openlist.is_configured() else LIBRARY_SOURCE_QAS


def _backoff_left(res):
    """退避窗口还剩多少秒（取整，至少 1）。

    窗口长度由 openlist 侧给出（backoff_seconds），记录时刻由 backoff_since 给出。
    任一缺失时返回 0，表示「不知道还剩多久」—— 前端会退化成「稍后自动重试」，
    而不是显示一个编造出来的倒计时。
    """
    try:
        since = float(res.get("backoff_since") or 0)
        window = float(res.get("backoff_seconds") or 0)
    except (TypeError, ValueError):
        return 0
    if since <= 0 or window <= 0:
        return 0
    left = window - (time.time() - since)
    if left <= 0:
        return 0
    return max(1, int(round(left)))


def list_library_entries(path=None, refresh=False):
    """按当前数据源列出网盘目录（统一入口，前端只认这一个）。

    返回在 list_library 的基础上增加：
      - source: 'openlist' | 'qas'（谁回答的）
      - fallback: 是否发生了回落（OpenList 配了但不可用 → 用了 QAS）

    refresh=True 时显式绕过两级的缓存：OpenList 侧不透传缓存，QAS 侧
    `use_cache=False` 不读**也不写**目录短命缓存 —— 否则下一次普通打开会拿到
    「刚刚强制刷新过」的旧数据（见文件头「目录短命缓存」）。

    失败时若 OpenList 已配置，会自动再试一次 QAS —— 但只在
    OpenList 侧**完全没拿到数据**时回落，避免「一半来自 A、一半来自 B」。
    """
    target = path or get_library_root()
    if openlist.is_configured():
        res = openlist.list_dir(target, refresh=refresh)
        if res.get("success"):
            return {"success": True, "path": res.get("path", target),
                    "entries": res.get("entries", []), "message": "",
                    "source": LIBRARY_SOURCE_OPENLIST, "fallback": False}
        ol_msg = res.get("message") or "OpenList 读取失败"
        # 记一条日志：否则用户只会看到「读取失败」，无从知道是 OpenList 的问题
        log("媒体库改用 QAS 兜底（OpenList 不可用）: {}".format(ol_msg))
        qas = list_library(path=target, use_cache=not refresh)
        qas["source"] = LIBRARY_SOURCE_QAS
        qas["fallback"] = True
        # 回落原因必须带给前端（issue #27 追问「配了 OpenList 却仍回落」）：
        # 只说「已回落」用户无从下手，要把 OpenList 侧的原始原因一起透出，
        # 才能区分「地址/密码错」和「路径前缀不对」这两类完全不同的成因。
        qas["fallback_reason"] = ol_msg
        qas["fallback_hint"] = res.get("fallback_hint", "")
        # 失败归类（issue #47）：只给一句话，前端没法判断该让用户做什么
        # （超时 → 重试；地址错 → 改地址；前缀错 → 改前缀）。
        qas["fallback_kind"] = res.get("kind", "")
        # 是否处于「失败退避」窗口（issue #47）：退避是**自动的、短暂的**，
        # 前端据此告诉用户「正在自动重试，不必改配置」，避免用户在
        # 20 秒的自动恢复期里就去翻设置、把好配置改坏。
        qas["fallback_backoff"] = bool(res.get("backoff"))
        # 退避窗口的剩余时长（issue #47 追问「上一级就回落」）：徽标要写
        # 「约 N 秒内自动回到 OpenList」，N 必须来自后端真实窗口，而不是前端
        # 硬编码一个数字 —— 两处一旦脱节，用户会按一个错的时间去重试。
        if res.get("backoff"):
            qas["fallback_backoff_seconds"] = res.get("backoff_seconds", 0)
            qas["fallback_backoff_left"] = _backoff_left(res)
        if qas.get("success"):
            qas["message"] = ""
            return qas
        # 两个源都失败：如实说明是哪个源、失败原因各自是什么
        return {"success": False, "path": target, "entries": [],
                "source": LIBRARY_SOURCE_QAS, "fallback": True,
                "message": "OpenList: {}；QAS: {}".format(ol_msg, qas.get("message", ""))}
    res = list_library(path=target, use_cache=not refresh)
    res["source"] = LIBRARY_SOURCE_QAS
    res["fallback"] = False
    return res


def delete_entries_by_source(entries, sync_history=True, source=None,
                            sync_tasks=False, sync_tasks_path_filter=""):
    """按数据源删除网盘条目（OpenList 侧用条目名删，QAS 侧用 fid 删）。

    为什么不在两种源之间互相转换（把 OpenList 的条目名当成 QAS 的 path 用）：
    QAS 的 /delete_file 需要 fid 或**完整 path**，而 OpenList 的条目名不含
    父目录，转换需要猜父目录 —— 猜错就是删掉别的东西。宁可要求
    「谁列出来的谁删」，也不做可能误删的路径拼接。

    sync_tasks（issue #57）：网盘侧删除成功后，是否**联动删除同名的 QAS 任务**。
      默认 False —— 删任务不可逆（QAS 里没有回收站），只有调用方明确要求才做。
    sync_tasks_path_filter：把联动范围收窄到 savepath 含该字符串的任务
      （网盘侧就是「当前浏览目录」），避免拿一个文件名去撞整张任务表。
    """
    src = source or LIBRARY_SOURCE_OPENLIST
    if src == LIBRARY_SOURCE_OPENLIST:
        if not openlist.is_configured():
            return {"success": False, "deleted": 0, "failed": 0, "history_synced": 0,
                    "results": [], "source": src,
                    "message": "未配置 OpenList 地址，无法在 OpenList 侧删除"}
        dir_path = (entries[0].get("dir") if entries and isinstance(entries[0], dict)
                    else None) or get_library_root()
        names = [e.get("name") for e in entries if isinstance(e, dict) and e.get("name")]
        res = openlist.remove(names, dir_path=dir_path)
        if not res.get("success"):
            return {"success": False, "deleted": 0, "failed": res.get("failed", 0),
                    "history_synced": 0, "results": res.get("results", []),
                    "source": src, "message": res.get("message", "删除失败")}
        # 历史同步与 QAS 侧同一口径：精确匹配，宁可不删也不误删
        synced = 0
        results = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = e.get("name") or ""
            title = e.get("title") or name
            if _sync_history_for(title, sync_history):
                synced += 1
            results.append({"name": name, "ok": True, "message": "已删除",
                            "history": "已同步清理" if sync_history else "未同步"})
        out = {"success": True, "deleted": res.get("deleted", 0), "failed": 0,
               "history_synced": synced, "results": results, "source": src,
               "message": ""}
        if sync_tasks:
            # 联动失败**绝不能**把网盘侧已经删成功这个事实改写成失败：
            # 文件确实已经删了，报 success=False 会让用户重复删除（第二次必然是
            # 「文件不存在」的失败）。因此联动结果单独挂在 tasks_result 下，前端
            # 分开复述（「已删除 N 项」+「任务同步未命中/失败原因」）。
            try:
                out["tasks_result"] = sync_tasks_by_names(
                    names, path_filter=sync_tasks_path_filter,
                    max_delete=MAX_TASK_DELETE_BATCH)
            except Exception as e:
                log("网盘删除后同步删除同名任务异常: {}".format(e))
                out["tasks_result"] = {"success": False, "deleted": 0, "failed": 0,
                                       "results": [], "matched": 0,
                                       "names": list(names),
                                       "path_filter": sync_tasks_path_filter,
                                       "message": "同步删除同名任务异常: {}".format(e)}
        return out
    res = delete_entries(entries, sync_history=sync_history)
    res["source"] = LIBRARY_SOURCE_QAS
    if sync_tasks and res.get("deleted"):
        # 与 OpenList 侧同口径：联动结果单独挂，不改变网盘删除的成败结论。
        names = [e.get("name") or e.get("title") for e in entries
                 if isinstance(e, dict) and (e.get("name") or e.get("title"))]
        try:
            res["tasks_result"] = sync_tasks_by_names(
                names, path_filter=sync_tasks_path_filter,
                max_delete=MAX_TASK_DELETE_BATCH)
        except Exception as e:
            log("网盘删除后同步删除同名任务异常: {}".format(e))
            res["tasks_result"] = {"success": False, "deleted": 0, "failed": 0,
                                   "results": [], "matched": 0, "names": names,
                                   "path_filter": sync_tasks_path_filter,
                                   "message": "同步删除同名任务异常: {}".format(e)}
    return res


def reconcile_tasks(root=None):
    """QAS 任务 ↔ 网盘实际内容 对账（issue #27 追问的「同时管理任务条目」）。

    这是「引入 OpenList 之后顺手能拿到」的能力：任务条目仍在 QAS 侧管理
    （增删都是 QAS 的 tasklist），但「这条任务在网盘上还有没有对应内容」
    只有文件侧说得清 —— 之前没有反查能力，所以任务失效了也看不出来。
    """
    res = list_tasks()
    if not res.get("success"):
        return {"success": False, "checked": 0, "missing": [], "unverified": [],
                "orphans": [], "scanned_dirs": 0, "truncated": False,
                "message": res.get("message", "读取 QAS 任务失败")}
    return openlist.reconcile_tasks(res.get("tasks", []),
                                    root=root or get_library_root())
