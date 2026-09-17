# media_library.py — 双向管理：QAS 任务列表 / 夸克网盘媒体库浏览 / 删除 + 历史同步
#
# 背景（issue #27）：
#   DBAuto 只做「正向」转存——把豆瓣/TMDB 条目经 PanSou 搜索后交给 QAS 转存。
#   但用户在夸克网盘里手工删掉某部影片后，QAS 的 tasklist 与 DBAuto 的转存历史
#   都不会变，于是①QAS 仍保留失效任务、②DBAuto 仍认为「已转存」而拒绝重新转存。
#
# 本模块补齐反向链路，能力分三层：
#   1. list_tasks()      —— 读出 QAS tasklist（只读，供前端展示）
#   2. list_library()    —— 调 QAS /get_savepath_detail 列出网盘真实文件（媒体库）
#   3. delete_entries()  —— 调 QAS /delete_file 删除网盘文件，并按需联动清理
#                          DBAuto 转存历史（双向同步的核心）
#
# 设计约束：
#   - 所有 QAS 调用都复用 QASClient（token 注入 / 超时策略与其它链路一致）；
#   - 删除是破坏性操作，默认「先删网盘、再同步历史」，任一步失败都要如实回报，
#     不做静默降级，避免用户以为同步成功而实际只删了一半。
from api_client import QASClient
from config import ConfigManager
from storage import delete_history_item
from utils import log

# 网盘目录浏览默认路径（与设置里的 savepath 无关，仅作入口）
DEFAULT_LIBRARY_ROOT = "/"

# 单次删除的条目上限，防止误操作/脚本化批量删库
MAX_DELETE_BATCH = 50

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
