# douban.py — 豆瓣榜单：直接调用豆瓣移动端 API
import re, time, urllib.parse
from threading import Lock
from utils import http_get, log
from config import ConfigManager

DOUBAN_BASE = "https://m.douban.com/rexxar/api/v2"

# path → (api_type, category)
_PATH_MAP = {
    "movie/hot":        ("movie", "热门"),
    "movie/latest":     ("movie", "最新"),
    "movie/top":        ("movie", "豆瓣高分"),
    "movie/underrated": ("movie", "冷门佳片"),
    "tv/drama":         ("tv", "tv"),
    "tv/variety":       ("tv", "show"),
}

# sub_type → douban type param for TV
_TV_TYPE_MAP = {
    "综合": "tv", "国产剧": "tv_domestic", "欧美剧": "tv_american",
    "日剧": "tv_japanese", "韩剧": "tv_korean", "动画": "tv_animation",
    "纪录片": "tv_documentary",
    # variety
    "国内": "show_domestic", "国外": "show_foreign",
}

_douban_cache = {}
_douban_lock = Lock()
_DOUBAN_TTL = 3600
_DOUBAN_CACHE_MAX = 100

def _prune_cache():
    if len(_douban_cache) <= _DOUBAN_CACHE_MAX:
        return
    sorted_keys = sorted(_douban_cache.keys(), key=lambda k: _douban_cache[k][0])
    to_remove = len(_douban_cache) - _DOUBAN_CACHE_MAX
    for k in sorted_keys[:to_remove]:
        del _douban_cache[k]

def _parse_year(year_str):
    if not year_str:
        return 0
    try:
        if isinstance(year_str, int):
            return year_str
        s = str(year_str).strip()
        match = re.search(r'(\d{4})', s)
        if match:
            return int(match.group(1))
        return 0
    except:
        return 0

def get_douban_list(path, sub_type, limit=20, min_rating=0, sort_by="rating", year_from=0, year_to=0,
                     exclude_keywords=None, genre=""):
    """直接调用豆瓣移动端 API 获取榜单"""
    exclude_keywords = exclude_keywords or []
    cache_key = "{}/{}/{}/{}/{}/{}/{}/{}".format(path, sub_type, limit, min_rating, sort_by, year_from, year_to,
                                               "/".join(exclude_keywords))
    now = time.time()
    with _douban_lock:
        if cache_key in _douban_cache:
            ct, cd = _douban_cache[cache_key]
            if now - ct < _DOUBAN_TTL:
                return cd

    if path not in _PATH_MAP:
        log("douban: 未知路径 {}".format(path))
        return []
    api_type, category = _PATH_MAP[path]

    # Map sub_type
    if api_type == "movie":
        douban_type = sub_type  # direct: 全部/华语/欧美/韩国/日本
    else:
        # TV: map Chinese names to douban params
        douban_type = _TV_TYPE_MAP.get(sub_type, "tv")
        if sub_type == "综合" and category == "show":
            douban_type = "show"

    params = {
        "category": category,
        "type": douban_type,
        "start": 0,
        "limit": limit * 3,
    }
    encoded = urllib.parse.urlencode(params)
    url = "{}/subject/recent_hot/{}?{}".format(DOUBAN_BASE, api_type, encoded)

    try:
        data = http_get(url, timeout=15, referer="https://m.douban.com/")
        items = data.get("items") or data.get("subjects") or []
        result = [{
            "title": i.get("title", ""),
            "rating": i.get("rating", {}).get("value", 0),
            "year": _parse_year(i.get("year", ""))
        } for i in items if i.get("title")]

        if min_rating > 0:
            result = [r for r in result if r["rating"] >= min_rating]

        if year_from > 0:
            result = [r for r in result if r["year"] >= year_from]
        if year_to > 0:
            result = [r for r in result if r["year"] <= year_to]

        # 排除关键词过滤（标题包含关键词的跳过）
        if exclude_keywords:
            result = [r for r in result if not any(kw in r["title"] for kw in exclude_keywords)]

        # 类型过滤（豆瓣 API 返回的 items 可能包含 genres 字段）
        if genre:
            result = [r for r in result if genre in ",".join(i.get("genres", []) or []) for i in [r]]

        if sort_by == "rating":
            result.sort(key=lambda x: x["rating"], reverse=True)
        elif sort_by == "year":
            result.sort(key=lambda x: x["year"], reverse=True)

        result = result[:limit]

        with _douban_lock:
            _douban_cache[cache_key] = (now, result)
            _prune_cache()
        log("douban: {}/{} → {} 条 (筛选后)".format(path, sub_type, len(result)))
        return result
    except Exception as e:
        log("douban 获取错误: {}".format(e))
        return []


def refresh_douban_cache():
    """手动清空豆瓣数据缓存"""
    with _douban_lock:
        _douban_cache.clear()
    log("豆瓣数据缓存已清空")


def get_douban_wishlist(uid=None, cookie=None, limit=50):
    """获取豆瓣“想看”列表\n\n    需要提供豆瓣用户 ID 和有效的登录 Cookie。\n    如果未提供，则从系统配置中获取。
    """
    cfg = ConfigManager.get_instance()
    uid = uid or cfg.douban_uid
    cookie = cookie or cfg.douban_cookie
    if not uid:
        log("豆瓣想看同步：未配置 douban_uid")
        return []
    if not cookie:
        log("豆瓣想看同步：未配置 douban_cookie")
        return []

    # 缓存 key
    cache_key = "wish:{}:{}".format(uid, limit)
    now = time.time()
    with _douban_lock:
        if cache_key in _douban_cache:
            ct, cd = _douban_cache[cache_key]
            if now - ct < _DOUBAN_TTL:
                return cd

    all_items = []
    start = 0
    page_size = 20
    try:
        while start < limit:
            params = {"type": "wish", "count": page_size, "start": start}
            encoded = urllib.parse.urlencode(params)
            url = "{}/user/{}/interests?{}".format(DOUBAN_BASE, uid, encoded)
            # 构造带 Cookie 的请求
            import requests as _requests
            resp = _requests.get(url, timeout=15,
                                 headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
                                          "Referer": "https://m.douban.com/",
                                          "Cookie": cookie})
            resp.raise_for_status()
            data = resp.json()
            interests = data.get("interests", [])
            if not interests:
                break
            for item in interests:
                subject = item.get("subject", {})
                title = subject.get("title", "")
                if title:
                    all_items.append({
                        "title": title,
                        "rating": subject.get("rating", {}).get("value", 0) or 0,
                        "year": _parse_year(subject.get("year", "")),
                        "category": subject.get("subtype", "movie") or "movie"
                    })
            start += page_size
            if len(interests) < page_size:
                break
            time.sleep(1)  # 避免请求过快

        all_items = all_items[:limit]
        with _douban_lock:
            _douban_cache[cache_key] = (now, all_items)
            _prune_cache()
        log("豆瓣想看列表: {} 条".format(len(all_items)))
        return all_items
    except Exception as e:
        log("豆瓣想看列表获取错误: {}".format(e))
        return []
