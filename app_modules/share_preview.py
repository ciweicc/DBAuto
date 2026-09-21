# share_preview.py — 分享链接「文件清单」预览（issue #88）
#
# 背景（issue #88）：
#   用户原话「搜索资源的功能搜出来点进去不能看集数全不全」。搜索结果只给了一行
#   标题 + 来源 + 链接是否有效的徽标 —— 用户无法判断这条资源到底是「全集」，
#   还是只到第 8 集。想确认就只能先转存、再去媒体库翻，试错成本极高。
#
#   可用的信息其实一直在：QAS 的 `/get_share_detail` 已经把分享根目录的文件列表
#   拉回来了（分页合并后的 `data.list`），只是过去只被用来判「链接是否有效」，
#   列表本身被丢掉了。本模块把这块数据归一化出来，供搜索结果展开查看。
#
# 归一化契约（纯函数，便于单测）：
#   normalize_share_detail(raw) -> {
#       "ok": bool,
#       "message": str,               # 失败原因 / 提示
#       "title": str,                 # 分享标题（可能为空）
#       "files": [{"name","dir","size","fid","type"}],
#       "total": int,                 # 文件条目总数（含目录）
#       "video_count": int,           # 视频文件数（用户自己据此判断有多少集）
#       "dir_count": int,
#       "truncated": bool,            # 是否因为上限被截断
#   }
#
# 「集数全不全」不做任何自动判定：只把**原始清单**（目录 / 文件名 / 体积）
# 与可数的计数（条目数 / 目录数 / 视频数）原样给出来，判断权交回用户。
#
# 曾经的 `episodes`（集号解析 + 缺集缺口）已被**移除**（issue #88 第五轮）：
# 用户原话「在日志里报出候选集数还是太麻烦了，因为有识别不准的情况」——
# 盘搜/分享的文件名命名千奇百怪（`01.mp4` / `S01E01` / `第01集` / 整季打包 /
# 年份 / 分辨率），任何自动集号推断都会误报，而误报比不报更贵。
from utils import log, TTLCache

# 单次预览最多返回的文件条目数。分享里塞几百个文件时，前 200 条足够看清
# 结构；再多只会把响应体和 DOM 撑大（走查脚本也要度量块高）。
MAX_PREVIEW_FILES = 200

# 视频扩展名：用于统计「视频文件数」。这是**可数**的事实（用户口中的
# 「有多少集」就是它），不是对「是否全集」的推断 —— 只报数，不下结论。
VIDEO_EXTS = (
    ".mp4", ".mkv", ".avi", ".ts", ".rmvb", ".flv", ".mov",
    ".wmv", ".m4v", ".mpg", ".mpeg", ".webm", ".iso",
)

# 预览结果缓存：同一分享在搜索下拉里反复展开时不再打 QAS（QAS 每次都要拿
# Cookie 向夸克实查，慢）。TTL 取短一些，避免用户刚转存完看到过期清单。
_preview_cache = TTLCache(ttl=180, max_size=100)


def _is_video(name):
    low = (name or "").lower()
    return low.endswith(VIDEO_EXTS)


def _size_of(item):
    """把上游 size 归一化成 int（缺失/非法按 0）。"""
    raw = item.get("size")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return 0


def normalize_share_detail(raw):
    """把 QAS `/get_share_detail` 的原始响应归一化成前端可直接渲染的形状。

    兼容两种成功形态：
      - `{"success": True, "data": {"list": [...], "share": {"title": ...}}}`
      - 旧版/桩服务直接给 `{"list": [...]}`（无 success 字段时按 list 是否存在判定）

    失败形态（QAS 返回 `success=False` 且把原因放在 `data.error` 或 `message`）：
    统一成 `{ok: False, message: <原因>}`，让前端把真实原因显示出来 ——
    「预览不了」和「预览为空」是两件事，静默变成后者会让人以为分享是空的。
    """
    if not isinstance(raw, dict):
        return {"ok": False, "message": "分享详情返回异常", "files": [],
                "total": 0, "video_count": 0, "dir_count": 0, "truncated": False}

    success = raw.get("success")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    lst = data.get("list")
    if lst is None and isinstance(raw.get("list"), list):
        # 兼容无 data 包裹的桩/旧响应
        data = raw
        lst = raw.get("list")

    if success is False or not isinstance(lst, list):
        msg = ""
        inner = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        for candidate in (inner.get("error"), raw.get("message"), raw.get("error")):
            if candidate:
                msg = str(candidate)
                break
        return {"ok": False, "message": msg or "无法读取分享文件列表", "files": [],
                "total": 0, "video_count": 0, "dir_count": 0, "truncated": False}

    share = data.get("share") if isinstance(data.get("share"), dict) else {}
    title = share.get("title") or data.get("title") or ""

    all_files, video_count, dir_count = [], 0, 0
    for item in lst:
        if not isinstance(item, dict):
            continue
        name = item.get("file_name") or item.get("name") or ""
        if not name:
            continue
        is_dir = bool(item.get("dir"))
        if is_dir:
            dir_count += 1
        elif _is_video(name):
            video_count += 1
        all_files.append({
            "name": name,
            "dir": is_dir,
            "size": _size_of(item),
            "fid": item.get("fid", ""),
            "type": item.get("obj_category", "") or ("dir" if is_dir else ""),
        })

    total = len(all_files)
    truncated = total > MAX_PREVIEW_FILES
    files = all_files[:MAX_PREVIEW_FILES] if truncated else all_files
    return {
        "ok": True,
        "message": "",
        "title": str(title),
        "files": files,
        "total": total,
        "video_count": video_count,
        "dir_count": dir_count,
        "truncated": truncated,
    }


def preview_share(url, use_cache=True):
    """读取并归一化一个分享链接的文件清单。

    上游异常（QAS 不可达 / 未配置）也会被捕获并归一化成 `{ok: False, message}`，
    不向路由层抛异常 —— 搜索下拉的展开不该因为 QAS 抖动而炸出 500。
    """
    u = (url or "").strip()
    if not u:
        return {"ok": False, "message": "缺少分享链接", "files": [],
                "total": 0, "video_count": 0, "dir_count": 0, "truncated": False}
    if use_cache:
        cached = _preview_cache.get(u)
        if cached is not None:
            return cached
    try:
        from transfer import _get_qas_client
        raw = _get_qas_client().get_share_detail(u)
    except Exception as e:
        log("分享预览失败: {}".format(u), detail=str(e))
        result = {"ok": False, "message": "读取分享失败: {}".format(e), "files": [],
                  "total": 0, "video_count": 0, "dir_count": 0, "truncated": False}
        return result
    result = normalize_share_detail(raw)
    # 只缓存成功的预览：失败多半是 QAS 抖动，缓存下来会让「重试」也拿到同一个失败
    if use_cache and result.get("ok"):
        _preview_cache.set(u, result)
    return result
