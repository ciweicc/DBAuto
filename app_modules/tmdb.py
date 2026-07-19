# tmdb.py — TMDB (The Movie Database) 官方 API 客户端
import time
import requests
from threading import Lock
from utils import http_get, log
from config import ConfigManager

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"

# 简易缓存
_tmdb_cache = {}
_tmdb_lock = Lock()
_TMDB_TTL = 1800  # 30 分钟
_TMDB_CACHE_MAX = 50

# 请求 session（复用连接池）
_tmdb_session = requests.Session()
_tmdb_session.headers.update({"Accept": "application/json"})

# 双地址自动切换（api.tmdb.org 短域名国内通常可访问）
_TMDB_PRIMARY = "https://api.tmdb.org/3"
_TMDB_BACKUP = "https://api.themoviedb.org/3"
_tmdb_current_url = _TMDB_PRIMARY


def _tmdb_request(url):
    """发起 TMDB API 请求，双地址自动切换 + SSL 容错"""
    global _tmdb_current_url
    last_err = None
    for attempt in range(3):
        try:
            resp = _tmdb_session.get(url, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.SSLError:
            # SSL 错误：尝试禁用验证重试（代理场景）
            try:
                resp = _tmdb_session.get(url, timeout=15, verify=False)
                resp.raise_for_status()
                return resp.json()
            except Exception as e2:
                last_err = e2
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(1)
    raise last_err


def _tmdb_request_with_failover(endpoint, params):
    """带双地址故障转移的请求：主地址失败 → 备用地址 → 自定义地址"""
    global _tmdb_current_url
    custom = _get_base_url()
    # 候选地址列表：当前地址优先，然后是其他地址
    urls = []
    base = _tmdb_current_url
    qs = "&".join("{}={}".format(k, v) for k, v in params.items())
    urls.append("{}{}?{}".format(base, endpoint, qs))
    # 添加备用地址（去重）
    for alt in [_TMDB_PRIMARY, _TMDB_BACKUP, custom]:
        alt = alt.rstrip("/")
        if alt != base:
            urls.append("{}{}?{}".format(alt, endpoint, qs))
    last_err = None
    for url in urls:
        try:
            data = _tmdb_request(url)
            # 成功，更新当前地址
            _tmdb_current_url = url.split("/3")[0] + "/3" if "/3" in url else _tmdb_current_url
            return data
        except Exception as e:
            last_err = e
            log("TMDB 地址请求失败 {}: {}".format(url.split("?")[0], e))
    raise last_err

# 列表类型 → endpoint 映射
_MOVIE_ENDPOINTS = {
    "trending": "/trending/movie/week",
    "popular": "/movie/popular",
    "top_rated": "/movie/top_rated",
    "now_playing": "/movie/now_playing",
    "upcoming": "/movie/upcoming",
}

_TV_ENDPOINTS = {
    "trending": "/trending/tv/week",
    "popular": "/tv/popular",
    "top_rated": "/tv/top_rated",
    "on_the_air": "/tv/on_the_air",
    "airing_today": "/tv/airing_today",
}

# 默认中文标题
_LANG = "zh-CN"


def _prune_cache():
    if len(_tmdb_cache) <= _TMDB_CACHE_MAX:
        return
    sorted_keys = sorted(_tmdb_cache.keys(), key=lambda k: _tmdb_cache[k][0])
    to_remove = len(_tmdb_cache) - _TMDB_CACHE_MAX
    for k in sorted_keys[:to_remove]:
        del _tmdb_cache[k]


def _get_api_key():
    cfg = ConfigManager.get_instance().get_config()
    return cfg.get("tmdb_api_key", "")

def _get_base_url():
    cfg = ConfigManager.get_instance().get_config()
    return cfg.get("tmdb_base_url", "").rstrip("/")


def _poster_url(path, size="w300"):
    if not path:
        return ""
    return "{}/{}/{}".format(TMDB_IMAGE_BASE, size, path)


def _parse_item(item, media_type):
    """从 TMDB API 响应中提取统一格式"""
    title = item.get("title") or item.get("name") or ""
    release_date = item.get("release_date") or item.get("first_air_date") or ""
    year = 0
    if release_date:
        try:
            year = int(release_date[:4])
        except:
            pass
    return {
        "id": item.get("id", 0),
        "title": title,
        "rating": round(item.get("vote_average", 0) or 0, 1),
        "votes": item.get("vote_count", 0) or 0,
        "year": year,
        "overview": item.get("overview", "") or "",
        "poster": _poster_url(item.get("poster_path", "")),
        "poster_lg": _poster_url(item.get("poster_path", ""), "w500"),
        "backdrop": _poster_url(item.get("backdrop_path", ""), "w780"),
        "genre_ids": item.get("genre_ids", []) or [],
        "popularity": round(item.get("popularity", 0) or 0, 1),
        "media_type": media_type,
        "original_title": item.get("original_title") or item.get("original_name") or "",
        "original_language": item.get("original_language", "") or "",
    }


def get_tmdb_list(media_type="movie", list_type="trending", page=1,
                  genre_id=0, year=0, min_rating=0, region="",
                  sort_by="popularity.desc", language=""):
    """获取 TMDB 列表

    Args:
        media_type: "movie" 或 "tv"
        list_type: trending/popular/top_rated/now_playing/upcoming/on_the_air/airing_today/discover
        page: 页码
        genre_id: 类型 ID（用于 discover）
        year: 年份（用于 discover）
        min_rating: 最低评分（用于 discover）
        region: 地区/语言代码（如 zh, en, ja, ko）
        sort_by: 排序方式
        language: 语言代码（默认 zh-CN）
    """
    api_key = _get_api_key()
    if not api_key:
        log("TMDB: 未配置 api_key")
        return {"items": [], "total_pages": 0, "total_results": 0, "page": 1, "error": "未配置 TMDB API Key"}

    lang = language or _LANG
    cache_key = "{}:{}:{}:{}:{}:{}:{}:{}:{}".format(
        media_type, list_type, page, genre_id, year, min_rating, region, sort_by, lang)
    now = time.time()
    with _tmdb_lock:
        if cache_key in _tmdb_cache:
            ct, cd = _tmdb_cache[cache_key]
            if now - ct < _TMDB_TTL:
                return cd

    # 构建请求参数
    params = {"api_key": api_key, "language": lang, "page": page}

    # 决定 endpoint
    if list_type == "discover":
        endpoint = "/discover/{}".format(media_type)
        if genre_id:
            params["with_genres"] = genre_id
        if year:
            if media_type == "movie":
                params["primary_release_year"] = year
            else:
                params["first_air_date_year"] = year
        if min_rating:
            params["vote_average.gte"] = min_rating
        if region:
            params["with_original_language"] = region
        params["sort_by"] = sort_by
        params["vote_count.gte"] = 50  # 过滤投票数太少的结果
    else:
        endpoints = _MOVIE_ENDPOINTS if media_type == "movie" else _TV_ENDPOINTS
        if list_type not in endpoints:
            log("TMDB: 未知列表类型 {}".format(list_type))
            return {"items": [], "total_pages": 0, "total_results": 0, "page": 1}
        endpoint = endpoints[list_type]
        if region:
            params["with_original_language"] = region

    try:
        data = _tmdb_request_with_failover(endpoint, params)
        raw_items = data.get("results", [])
        items = [_parse_item(item, media_type) for item in raw_items if item.get("title") or item.get("name")]

        result = {
            "items": items,
            "total_pages": data.get("total_pages", 0),
            "total_results": data.get("total_results", 0),
            "page": data.get("page", page),
        }

        with _tmdb_lock:
            _tmdb_cache[cache_key] = (now, result)
            _prune_cache()
        log("TMDB: {}/{} → {} 条 (page {})".format(media_type, list_type, len(items), page))
        return result
    except Exception as e:
        log("TMDB 获取错误: {}".format(e))
        return {"items": [], "total_pages": 0, "total_results": 0, "page": 1, "error": str(e)}


def get_tmdb_genres(media_type="movie", language=""):
    """获取类型列表"""
    api_key = _get_api_key()
    if not api_key:
        return []

    lang = language or _LANG
    cache_key = "genres:{}:{}".format(media_type, lang)
    now = time.time()
    with _tmdb_lock:
        if cache_key in _tmdb_cache:
            ct, cd = _tmdb_cache[cache_key]
            if now - ct < _TMDB_TTL * 24:  # 类型列表缓存 24 倍 TTL
                return cd

    try:
        params = {"api_key": api_key, "language": lang}
        data = _tmdb_request_with_failover("/genre/{}/list".format(media_type), params)
        genres = data.get("genres", [])
        with _tmdb_lock:
            _tmdb_cache[cache_key] = (now, genres)
            _prune_cache()
        return genres
    except Exception as e:
        log("TMDB 类型获取错误: {}".format(e))
        return []


def refresh_tmdb_cache():
    """清空 TMDB 缓存"""
    with _tmdb_lock:
        _tmdb_cache.clear()
    log("TMDB 缓存已清空")


# 地区/语言选项
REGION_OPTIONS = [
    {"code": "", "name": "全部"},
    {"code": "zh", "name": "华语"},
    {"code": "en", "name": "英语"},
    {"code": "ja", "name": "日语"},
    {"code": "ko", "name": "韩语"},
    {"code": "fr", "name": "法语"},
    {"code": "es", "name": "西班牙语"},
    {"code": "de", "name": "德语"},
    {"code": "th", "name": "泰语"},
    {"code": "hi", "name": "印地语"},
]

# 排序选项
SORT_OPTIONS = [
    {"code": "popularity.desc", "name": "按热度"},
    {"code": "vote_average.desc", "name": "按评分"},
    {"code": "release_date.desc", "name": "按时间"},
    {"code": "vote_count.desc", "name": "按投票数"},
]

# 列表类型选项
MOVIE_LIST_TYPES = [
    {"code": "trending", "name": "热门趋势"},
    {"code": "popular", "name": "流行"},
    {"code": "top_rated", "name": "高分"},
    {"code": "now_playing", "name": "正在上映"},
    {"code": "upcoming", "name": "即将上映"},
    {"code": "discover", "name": "自定义筛选"},
]

TV_LIST_TYPES = [
    {"code": "trending", "name": "热门趋势"},
    {"code": "popular", "name": "流行"},
    {"code": "top_rated", "name": "高分"},
    {"code": "on_the_air", "name": "正在播出"},
    {"code": "airing_today", "name": "今日播出"},
    {"code": "discover", "name": "自定义筛选"},
]
