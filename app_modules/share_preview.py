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
# 子目录浏览（issue #88 四次反馈「还是看不到第二层目录的文件名称」）：
#   很多盘搜结果的根目录里**只有一个文件夹**（如 `恶搞之家/`），真正的视频文件
#   在那个文件夹里。QAS `/get_share_detail` 只列**当前层**（分享根），所以不做
#   「进目录」，用户看到的就是「共 1 个条目 · 1 个目录 / 恶搞之家 目录」——
#   文件名当然看不到。要看到第二层，必须能进目录。
#   好在 QAS 的 `extract_url()` 支持 `/<pwd_id>/<fid>-<name>` 形式的分享链接：
#   它会把 32 位 fid 段解析成 `pdir_fid`，从而列出该子目录。于是本模块支持
#   「带目录的分享链接」——每条条目的 fid 与 stoken 本来就随响应回来了，
#   缺的只是「把这个 fid 传回去」这一步。
#
# 曾经的 `episodes`（集号解析 + 缺集缺口）已被**移除**（issue #88 第五轮）：
# 用户原话「在日志里报出候选集数还是太麻烦了，因为有识别不准的情况」——
# 盘搜/分享的文件名命名千奇百怪（`01.mp4` / `S01E01` / `第01集` / 整季打包 /
# 年份 / 分辨率），任何自动集号推断都会误报，而误报比不报更贵。
import re

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
                "total": 0, "video_count": 0, "dir_count": 0, "truncated": False,
                "stoken": "", "path": []}

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
                "total": 0, "video_count": 0, "dir_count": 0, "truncated": False,
                "stoken": "", "path": []}

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
        # 子目录浏览所需的两件东西（issue #88 四次反馈）：
        #   - stoken：同一个分享内翻目录要复用同一枚 stoken（QAS 会原样透传）；
        #   - path  ：面包屑（从分享根到当前目录的 fid/名称链）。
        # 前端「进目录 / 回上级」都靠它们；取不到就退化成只读根目录。
        "stoken": str(data.get("stoken", "") or ""),
        "path": _normalize_path(data),
    }


def _normalize_path(data):
    """把 QAS 的 `data.paths` / `data.full_path` 归一化成面包屑列表。

    形状：[{"fid": "...", "name": "恶搞之家"}, ...]，从分享根到当前目录。
    取不到就返回 []（分享根本身没有面包屑，前端不渲染）。
    """
    src = data.get("paths")
    if not isinstance(src, list):
        src = data.get("full_path")
    if not isinstance(src, list):
        return []
    out = []
    for item in src:
        if not isinstance(item, dict):
            continue
        fid = item.get("fid")
        name = item.get("name") or item.get("file_name")
        if fid and name:
            out.append({"fid": str(fid), "name": str(name)})
    return out


def build_dir_url(url, dir_fid, dir_name=""):
    """把分享链接拼成「指向某个子目录」的链接。

    QAS 的 `extract_url()` 用 `re.findall(r"/(\\w{32})-?([^/]+)?", url)` 从 URL 路径里
    解析 32 位 fid 段，最后一段即 `pdir_fid`。所以要在分享里翻到某个子目录，
    只需把它的 fid（可选带名称）作为路径段追加到原分享链接后：

        https://pan.quark.cn/s/<pwd_id>  ->  https://pan.quark.cn/s/<pwd_id>/<fid>-<name>

    `dir_name` 只为可读（QAS 会 url-decode 回来），传空也能工作。
    非法 fid / 空 fid 时原样返回 url，避免拼出坏链接（点了没反应）。
    """
    u = (url or "").strip()
    fid = (dir_fid or "").strip()
    if not u or not fid:
        return u
    if not re.fullmatch(r"[A-Za-z0-9]{32}", fid):
        return u
    # 同名目录反复进入时不要叠加路径段（保证幂等）
    if "/" + fid in u:
        return u
    name = (dir_name or "").strip()
    seg = fid + ("-" + name if name else "")
    return u.rstrip("/") + "/" + seg


def _empty_result(message="", url="", path=None):
    return {"ok": False, "message": message, "files": [],
            "total": 0, "video_count": 0, "dir_count": 0, "truncated": False,
            "stoken": "", "path": path or [], "url": url}


def preview_share(url, use_cache=True, dir_fid="", dir_name=""):
    """读取并归一化一个分享链接（或其中某个子目录）的文件清单。

    `dir_fid` 非空时表示「进入该子目录」：内部把分享链接拼成
    `<url>/<fid>-<name>` 再交给 QAS —— QAS 会把它解析成 `pdir_fid` 并列出该层。
    这是 issue #88 四次反馈「还是看不到第二层目录的文件名称」的修法：
    盘搜结果的根目录常常只有一个文件夹，不进去就永远只有一行目录名。

    上游异常（QAS 不可达 / 未配置）也会被捕获并归一化成 `{ok: False, message}`，
    不向路由层抛异常 —— 搜索下拉的展开不该因为 QAS 抖动而炸出 500。
    """
    u = (url or "").strip()
    if not u:
        return _empty_result("缺少分享链接")
    target = build_dir_url(u, dir_fid, dir_name)
    # 缓存键用**实际请求的 URL**：根目录与每个子目录各自缓存，互不串味
    key = target
    if use_cache:
        cached = _preview_cache.get(key)
        if cached is not None:
            return cached
    try:
        from transfer import _get_qas_client
        raw = _get_qas_client().get_share_detail(target)
    except Exception as e:
        log("分享预览失败: {}".format(target), detail=str(e))
        return _empty_result("读取分享失败: {}".format(e), url=target)
    result = normalize_share_detail(raw)
    # 回填「这次实际请求的链接」，前端进子目录后据此再往下钻
    result["url"] = target
    if not result.get("path") and dir_fid and target != u:
        # QAS 未给面包屑时，用 dir_fid/dir_name 至少补一层，前端仍能显示「回上级」
        result["path"] = [{"fid": (dir_fid or "").strip(),
                          "name": (dir_name or "").strip() or "子目录"}]
    # 只缓存成功的预览：失败多半是 QAS 抖动，缓存下来会让「重试」也拿到同一个失败
    if use_cache and result.get("ok"):
        _preview_cache.set(key, result)
    return result
