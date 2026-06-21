# douban.py — 豆瓣榜单：直接调用豆瓣移动端 API
import time, urllib.parse
from threading import Lock
from utils import http_get, log

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
_DOUBAN_TTL = 300

def get_douban_list(path, sub_type, limit=20):
    """直接调用豆瓣移动端 API 获取榜单"""
    cache_key = "{}/{}/{}".format(path, sub_type, limit)
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
        "limit": limit,
    }
    encoded = urllib.parse.urlencode(params)
    url = "{}/subject/recent_hot/{}?{}".format(DOUBAN_BASE, api_type, encoded)

    try:
        data = http_get(url, timeout=15)
        items = data.get("items") or data.get("subjects") or []
        result = [{"title": i.get("title", ""), "rating": i.get("rating", {}).get("value", 0)}
                  for i in items if i.get("title")]
        with _douban_lock:
            _douban_cache[cache_key] = (now, result)
        log("douban: {}/{} → {} 条".format(path, sub_type, len(result)))
        return result
    except Exception as e:
        log("douban 获取错误: {}".format(e))
        return []
