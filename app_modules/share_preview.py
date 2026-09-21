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
#       "video_count": int,           # 视频文件数（判断"集数"的关键）
#       "dir_count": int,
#       "truncated": bool,            # 是否因为上限被截断
#   }
#
# 「集数全不全」的判定刻意只做**可观测量**的统计（视频数 / 目录数 / 文件名列表），
# 不对「是否全集」下定论：盘搜结果的命名千奇百怪（`01.mp4` / `S01E01` /
# `第01集` / 整季打包），任何自动「缺集」判断都会误报，把判断权交给人。
import re

from utils import log, TTLCache

# 单次预览最多返回的文件条目数。分享里塞几百集时，前 200 条足够看出
# 是不是连续编号；再多只会把响应体和 DOM 撑大（走查脚本也要度量块高）。
MAX_PREVIEW_FILES = 200

# 视频扩展名：用于统计「视频文件数」，即用户口中的「集数」。
VIDEO_EXTS = (
    ".mp4", ".mkv", ".avi", ".ts", ".rmvb", ".flv", ".mov",
    ".wmv", ".m4v", ".mpg", ".mpeg", ".webm", ".iso",
)

# 预览结果缓存：同一分享在搜索下拉里反复展开时不再打 QAS（QAS 每次都要拿
# Cookie 向夸克实查，慢）。TTL 取短一些，避免用户刚转存完看到过期清单。
_preview_cache = TTLCache(ttl=180, max_size=100)


# 失败态共用的空集号摘要（形状必须与 summarize_episodes 一致，
# 否则前端读 ep.numbers 会炸）。
_EMPTY_EPISODES = {"numbers": [], "missing": [], "continuous": False}


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
                "episodes": _EMPTY_EPISODES}

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
                "episodes": _EMPTY_EPISODES}

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
        # 集号摘要必须一并给出（前端「缺 8、9」那句就靠它）。
        # 注意用**未截断的完整列表**算：只看前 MAX_PREVIEW_FILES 条会把
        # 后面的集号整段丢掉，算出「缺了大半」的假缺口。
        "episodes": summarize_episodes(all_files),
    }


def preview_share(url, use_cache=True):
    """读取并归一化一个分享链接的文件清单。

    上游异常（QAS 不可达 / 未配置）也会被捕获并归一化成 `{ok: False, message}`，
    不向路由层抛异常 —— 搜索下拉的展开不该因为 QAS 抖动而炸出 500。
    """
    u = (url or "").strip()
    if not u:
        return {"ok": False, "message": "缺少分享链接", "files": [],
                "total": 0, "video_count": 0, "dir_count": 0, "truncated": False,
                "episodes": _EMPTY_EPISODES}
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
                  "total": 0, "video_count": 0, "dir_count": 0, "truncated": False,
                  "episodes": _EMPTY_EPISODES}
        return result
    result = normalize_share_detail(raw)
    # 只缓存成功的预览：失败多半是 QAS 抖动，缓存下来会让「重试」也拿到同一个失败
    if use_cache and result.get("ok"):
        _preview_cache.set(u, result)
    return result


# 集号优先按「季集标记」认，其次才回落到裸数字：
#   S01E02 / s1e2 / E02   → 02（**不是** 01：`S01` 里的 1 是季号，不是集号）
#   第02集 / 第2话 / 02集  → 02
# 顺序很重要：先认标记、再认裸数字，否则 `S01E02` 会被裸数字规则读成 1 和 2。
_EP_MARK = re.compile(r"[Ee](\d{1,4})(?![0-9])")
_EP_CN = re.compile(r"第\s*(\d{1,4})\s*[集话話期]")
_EP_SUFFIX = re.compile(r"(\d{1,4})\s*[集话話期]")
_EP_BARE = re.compile(r"(?:^|[^0-9])(\d{1,4})(?![0-9])")


def _episode_number(stem):
    """从单个文件名（已去扩展名）里取集号，取不到返回 None。"""
    for pat in (_EP_MARK, _EP_CN, _EP_SUFFIX):
        m = pat.search(stem)
        if m:
            return int(m.group(1))
    # 裸数字回落：取**最后一个**数字段（`Show 2024 05` 里 05 才是集号）
    nums = [int(x) for x in _EP_BARE.findall(stem)]
    if nums:
        return nums[-1]
    return None


def summarize_episodes(files):
    """从文件名里提取集号，给出「连续到什么程度」的可观测摘要。

    刻意**不下「是否全集」的结论**：只有一条 `01.mp4` 可能是「只有第 1 集」，
    也可能是「整包只发了一集就够用了」。这里只回答两个可观测问题：
      - 从文件名里认出了哪些集号（去重排序）；
      - 它们是否**连续**（有断档就把缺口列出来）。

    返回 {"numbers": [...], "missing": [...], "continuous": bool}。
    认不出任何集号时返回空列表（不猜）。
    """
    nums = set()
    for f in files or []:
        if f.get("dir"):
            continue
        name = f.get("name") or ""
        stem = name.rsplit(".", 1)[0] if "." in name else name
        n = _episode_number(stem)
        if n is not None:
            nums.add(n)
    numbers = sorted(nums)
    if len(numbers) < 2:
        return {"numbers": numbers, "missing": [], "continuous": bool(numbers)}
    lo, hi = numbers[0], numbers[-1]
    present = set(numbers)
    missing = [n for n in range(lo, hi + 1) if n not in present]
    return {"numbers": numbers, "missing": missing, "continuous": not missing}
