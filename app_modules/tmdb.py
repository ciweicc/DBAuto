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
# 单次请求超时（秒）：原 15s 太长，网络抖动时会造成页面长时间空白；
# 配合 failover 多地址快速切换，8s 足够且体验更好
_TMDB_TIMEOUT = 8

# 请求 session（复用连接池）
_tmdb_session = requests.Session()
_tmdb_session.headers.update({"Accept": "application/json"})

# 双地址自动切换（api.tmdb.org 短域名国内通常可访问）
_TMDB_PRIMARY = "https://api.tmdb.org/3"
_TMDB_BACKUP = "https://api.themoviedb.org/3"
_tmdb_current_url = _TMDB_PRIMARY


def _tmdb_request(url, proxies=None):
    """发起单次 TMDB 请求，含一次 SSL 容错（关闭证书验证）。

    说明：EOF 类握手失败（中间网络设备重置连接）发生在证书验证之前，
    verify=False 无法解决，必须交由 _tmdb_request_with_failover 切换到其他地址。
    因此本函数只对「证书链/系统根证书」类错误尝试关闭验证重试一次，其余错误直接上抛。
    """
    try:
        resp = _tmdb_session.get(url, timeout=_TMDB_TIMEOUT, proxies=proxies)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.SSLError:
        # 因系统缺根证书导致的证书验证失败：关闭验证重试一次
        try:
            resp = _tmdb_session.get(url, timeout=_TMDB_TIMEOUT, verify=False, proxies=proxies)
            resp.raise_for_status()
            return resp.json()
        except Exception as e2:
            raise e2
    except Exception as e:
        raise e


def _tmdb_request_with_failover(endpoint, params):
    """带故障转移的请求：短域名(api.tmdb.org)优先 → 长域名 → 自定义地址。

    候选顺序固定为「短域名 → 长域名 → 自定义地址」，不把上次成功地址排到最前，
    避免某次抖动把连接永久锁死在一个已变差的地址上（短域名国内通常更可达）。
    每个地址只试一次，失败立即切换，缩短整体耗时。
    """
    global _tmdb_current_url
    custom = _get_base_url().rstrip("/")
    proxies = _get_proxies()
    qs = "&".join("{}={}".format(k, v) for k, v in params.items())

    # 候选地址：短域名优先，其次长域名、自定义地址；上次成功地址仅作兜底（不在前述列表中时）
    candidates = [_TMDB_PRIMARY, _TMDB_BACKUP]
    if custom:
        candidates.append(custom)
    cur = _tmdb_current_url.rstrip("/") if _tmdb_current_url else ""
    if cur and cur not in candidates:
        candidates.append(cur)
    seen = set()
    ordered = []
    for c in candidates:
        c = c.rstrip("/")
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    last_err = None
    for base in ordered:
        url = "{}{}?{}".format(base, endpoint, qs)
        try:
            data = _tmdb_request(url, proxies=proxies)
            _tmdb_current_url = base  # 成功，记录为当前优选地址
            return data
        except Exception as e:
            last_err = e
            host = url.split("?")[0]
            log("TMDB 地址请求失败 {}: {}".format(host, e))
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


def _get_proxies():
    """返回 TMDB 请求使用的代理字典。

    优先级：设置项 tmdb_proxy > 环境变量 HTTPS_PROXY / HTTP_PROXY。
    国内网络访问 TMDB 官方域名常被干扰（TLS 握手被重置），
    通过该字段配置一个可用代理（如 http://127.0.0.1:7890）即可正常访问。
    返回 None 表示不使用代理（直连）。
    """
    cfg = ConfigManager.get_instance().get_config()
    p = (cfg.get("tmdb_proxy") or "").strip()
    if p:
        return {"http": p, "https": p}
    import os
    env = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or
           os.environ.get("https_proxy") or os.environ.get("http_proxy"))
    if env:
        return {"http": env, "https": env}
    return None


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
                  genre_id=0, year=0, min_rating=0, country="",
                  sort_by="popularity.desc", language="", watch_providers=""):
    """获取 TMDB 列表

    Args:
        media_type: "movie" 或 "tv"
        list_type: trending/popular/top_rated/now_playing/upcoming/on_the_air/airing_today/discover
        page: 页码
        genre_id: 类型 ID（用于 discover）
        year: 年份（用于 discover）
        min_rating: 最低评分（用于 discover，映射 vote_average.gte）
        country: 地区/国家代码（ISO 3166-1，如 CN/US/JP）；
                 discover 下映射 with_origin_country，now_playing/upcoming/on_the_air/airing_today 下映射 region
        sort_by: 排序方式
        language: 原始语言代码（默认 zh-CN；discover 下映射 with_original_language）
        watch_providers: 流媒体平台 ID（逗号分隔，用于 discover，配合 watch_region 映射 with_watch_providers）
    """
    api_key = _get_api_key()
    if not api_key:
        log("TMDB: 未配置 api_key")
        return {"items": [], "total_pages": 0, "total_results": 0, "page": 1, "error": "未配置 TMDB API Key"}

    lang = language or _LANG
    cache_key = "{}:{}:{}:{}:{}:{}:{}:{}:{}:{}".format(
        media_type, list_type, page, genre_id, year, min_rating, country, sort_by, lang, watch_providers)
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
        if country:
            params["with_origin_country"] = country
        if language:
            params["with_original_language"] = language
        if watch_providers:
            params["with_watch_providers"] = watch_providers
            params["watch_region"] = country or "US"
        params["sort_by"] = sort_by
    else:
        endpoints = _MOVIE_ENDPOINTS if media_type == "movie" else _TV_ENDPOINTS
        if list_type not in endpoints:
            log("TMDB: 未知列表类型 {}".format(list_type))
            return {"items": [], "total_pages": 0, "total_results": 0, "page": 1}
        endpoint = endpoints[list_type]
        # region（国家码）仅对支持该参数的榜单生效（正在上映/即将上映/正在播出/今日播出）
        if country and list_type in ("now_playing", "upcoming", "on_the_air", "airing_today"):
            params["region"] = country

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


def get_tmdb_watch_providers(media_type="movie", region=""):
    """获取指定地区可用的流媒体平台（watch providers）列表

    Args:
        media_type: "movie" 或 "tv"
        region: 地区/国家代码（ISO 3166-1，如 CN/US/JP），缺省回退到 US
    返回:
        [{"id": int, "name": str}, ...]（按名称排序）
    """
    api_key = _get_api_key()
    if not api_key:
        return []
    region = region or "US"
    lang = _LANG
    cache_key = "providers:{}:{}".format(media_type, region)
    now = time.time()
    with _tmdb_lock:
        if cache_key in _tmdb_cache:
            ct, cd = _tmdb_cache[cache_key]
            if now - ct < _TMDB_TTL * 24:  # 平台列表缓存 24 倍 TTL
                return cd
    try:
        params = {"api_key": api_key, "language": lang, "watch_region": region}
        data = _tmdb_request_with_failover("/watch/providers/{}".format(media_type), params)
        results = data.get("results", [])
        providers = [{"id": p.get("provider_id"), "name": p.get("provider_name")}
                     for p in results if p.get("provider_id")]
        providers.sort(key=lambda x: (x["name"] or "").lower())
        with _tmdb_lock:
            _tmdb_cache[cache_key] = (now, providers)
            _prune_cache()
        log("TMDB 流媒体平台 {} 列表 → {} 个".format(region, len(providers)))
        return providers
    except Exception as e:
        log("TMDB 流媒体平台获取错误: {}".format(e))
        return []


def search_tmdb_id(query, media_type="movie", year=0):
    """按标题搜索 TMDB，返回最佳匹配的整型作品 id；无结果 / 未配置 key / 出错时返回 None。

    用于转存去重：同一作品（含不同译名、续集/分季差异）在 TMDB 拥有稳定唯一 id，
    以 id 去重比标题规范化更可靠，可避免"阿凡达"误吞"阿凡达2"等误判。
    """
    api_key = _get_api_key()
    if not api_key:
        return None
    if not query:
        return None
    cache_key = "search:{}:{}:{}".format(query, media_type, year)
    now = time.time()
    with _tmdb_lock:
        if cache_key in _tmdb_cache:
            ct, cd = _tmdb_cache[cache_key]
            if now - ct < _TMDB_TTL:
                return cd
    params = {
        "api_key": api_key,
        "language": _LANG,
        "query": query,
        "include_adult": "false",
        "page": "1",
    }
    try:
        data = _tmdb_request_with_failover("/search/multi", params)
    except Exception as e:
        log("TMDB 搜索失败 {}: {}".format(query, e))
        return None
    results = data.get("results", [])
    picked = None
    for item in results:
        mt = item.get("media_type")
        if mt not in ("movie", "tv"):
            continue
        # 按 category 过滤类型，避免电影误匹配到同名剧集（反之亦然）
        if media_type == "movie" and mt != "movie":
            continue
        if media_type == "tv" and mt != "tv":
            continue
        if year:
            rd = item.get("release_date") or item.get("first_air_date") or ""
            try:
                iy = int(rd[:4])
            except (ValueError, TypeError):
                iy = 0
            if iy and iy != year:
                continue
        picked = item.get("id")
        if picked:
            break
    with _tmdb_lock:
        _tmdb_cache[cache_key] = (now, picked)
        _prune_cache()
    return picked


# 原始语言选项（对应 TMDB with_original_language，ISO 639-1）
LANGUAGE_OPTIONS = [
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

# 地区/国家选项（对应 TMDB region / with_origin_country，ISO 3166-1 alpha-2）
COUNTRY_OPTIONS = [
    {"code": "", "name": "全部"},
    {"code": "CN", "name": "中国大陆"},
    {"code": "HK", "name": "中国香港"},
    {"code": "TW", "name": "中国台湾"},
    {"code": "US", "name": "美国"},
    {"code": "JP", "name": "日本"},
    {"code": "KR", "name": "韩国"},
    {"code": "GB", "name": "英国"},
    {"code": "FR", "name": "法国"},
    {"code": "DE", "name": "德国"},
    {"code": "ES", "name": "西班牙"},
    {"code": "TH", "name": "泰国"},
    {"code": "IN", "name": "印度"},
]

# 排序选项（与 TMDB discover sort_by 对齐，含 asc/desc 及 revenue/original_title）
SORT_OPTIONS = [
    {"code": "popularity.desc", "name": "热度(高→低)"},
    {"code": "popularity.asc", "name": "热度(低→高)"},
    {"code": "vote_average.desc", "name": "评分(高→低)"},
    {"code": "vote_average.asc", "name": "评分(低→高)"},
    {"code": "release_date.desc", "name": "时间(新→旧)"},
    {"code": "release_date.asc", "name": "时间(旧→新)"},
    {"code": "vote_count.desc", "name": "投票数(多→少)"},
    {"code": "revenue.desc", "name": "票房(高→低)"},
    {"code": "original_title.asc", "name": "标题(A→Z)"},
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
