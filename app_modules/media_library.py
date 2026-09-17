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

# 读取类接口（任务列表 / 目录浏览）的超时预算。
#
# 为什么单独给读接口收窄超时（issue #27「网盘文件模块访问有点慢」）：
#   QASClient 默认 timeout=15，而 utils.http_get 对网络层错误会重试 3 次，
#   单次目录请求的最坏等待因此达到 15+1+5+5+5+1 ≈ 32s，前端一直停在「加载中」。
#   目录浏览是「点一下就该出结果」的交互，宁可快速失败并提示重试，
#   也不要让用户对着转圈等待半分钟。
# 删除是破坏性且需要确认结果的操作，保持原有 15s 预算不变。
LIBRARY_READ_TIMEOUT = 6


def _client(timeout=15):
    cfg = ConfigManager.get_instance()
    return QASClient(cfg.qas, cfg.qas_token, timeout=timeout)


def _read_client():
    return _client(LIBRARY_READ_TIMEOUT)


def _resp_ok(res):
    return isinstance(res, dict) and bool(res.get("success"))


def list_tasks():
    """读取 QAS 的 tasklist（只读）。

    返回 {"success": bool, "tasks": [...], "message": str}。
    每个 task 原样透传 QAS 字段（taskname / shareurl / savepath / pattern 等），
    额外补一个 `exists_in_history` 标记，便于前端区分「QAS 有任务但本地无记录」。
    """
    try:
        res = _read_client().get_data()
    except Exception as e:
        log("读取 QAS 任务失败: {}".format(e))
        return {"success": False, "tasks": [], "message": "读取 QAS 任务失败: {}".format(e)}
    if not _resp_ok(res):
        return {"success": False, "tasks": [],
                "message": (res or {}).get("message", "QAS 返回异常")}
    data = res.get("data", {}) or {}
    tasks = data.get("tasklist", []) or []
    return {"success": True, "tasks": tasks, "message": ""}


def list_library(path=None, fid=None):
    """列出夸克网盘指定目录下的真实文件（媒体库视图）。

    返回 {"success": bool, "path": str, "fid": ..., "entries": [...], "message": str}。
    entries 由 QAS 原样返回，含 fid / file_name / dir / size / updated_at。
    """
    try:
        res = _read_client().get_savepath_detail(path=path, fid=fid)
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
    return {
        "success": True,
        "path": data.get("paths") and path or (path or DEFAULT_LIBRARY_ROOT),
        "fid": data.get("fid"),
        "paths": data.get("paths", []),
        "entries": normalized,
        "message": "",
    }


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
    return {"success": True, "deleted": len(drop), "failed": 0,
            "results": results, "tasks": new_tasks, "message": ""}
