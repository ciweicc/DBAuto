# tmdb.py — TMDB (The Movie Database) 官方 API 客户端
import re
import time
import requests
from requests.adapters import HTTPAdapter
from threading import Lock, Semaphore
from utils import log
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
# 放大连接池：默认 pool_maxsize=10，连续搜索时并发请求会把它耗尽，
# 后续请求只能排队等连接释放 → 表现为「搜索卡住/无法搜索」。放大到 20 留足余量。
_tmdb_session = requests.Session()
_tmdb_session.headers.update({"Accept": "application/json"})
_tmdb_adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
_tmdb_session.mount("https://", _tmdb_adapter)
_tmdb_session.mount("http://", _tmdb_adapter)

# 并发上限：限制同时在飞的 TMDB 网络调用数量。
# server.py 用 ThreadingMixIn（每连接一个线程、无上限），若不做限制，连续搜索时
# 缓慢的 TMDB 请求（单请求最长 8s×3 地址 ≈ 24s）会堆积大量阻塞线程，
# 既拖垮其它接口，又可能吃满内存触发 OOM 重启（重启后若 key 仅由环境变量注入则会变空，
# 进而误报「未配置 TMDB API Key」）。信号量把在飞请求收敛到固定数量。
_TMDB_MAX_CONCURRENCY = 4
_tmdb_semaphore = Semaphore(_TMDB_MAX_CONCURRENCY)

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
    # 信号量限制在飞请求数：多余请求在此排队（仍占一个 server 线程，但网络调用被收敛），
    # 既避免连接池耗尽，也避免无界线程堆积。
    with _tmdb_semaphore:
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
        log("TMDB: {}/{} → {} 条 (page {})".format(media_type, list_type, len(items), page), tag="TMDB")
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


# ============ 文件名 → TMDB 作品匹配（媒体库图文卡片，issue #27） ============
#
# 用户诉求：媒体库（网盘文件）不要只是「文件夹样式的表格」，要像 TMDB 那样图文并茂。
# 网盘里只有文件名，要出海报就得先判断「这个文件是哪部作品」。
#
# 设计要点：
#  1. 解析是**纯函数**：文件名 → 标题/核心名/年份，便于单元测试与复用；
#  2. 匹配用「核心标题相关性」做闸门，再用年份微调，宁可不出海报也不挂错海报；
#  3. 结果进 _tmdb_cache（与其它 TMDB 调用同一份缓存），重复浏览同一目录不再打网络；
#  4. 未配置 key / 网络失败 → 返回 None，前端降级为占位卡，媒体库不因 TMDB 不可用而变砖。
#
# 注意：下面的 _clean_title / _core_title 与 transfer.py 的同名函数**口径必须一致**。
# 这里不 import transfer：tmdb 会被 transfer/搜索链路间接引用，反向 import 会成环；
# 两份实现一旦漂移，「媒体库认为已匹配」与「转存去重认为不存在」就会互相矛盾。

# 发布组/编码/来源等噪声 token（小写比较）
_NOISE_TOKENS = (
    "bluray", "blu-ray", "bdrip", "brrip", "dvdrip", "hdtv", "webrip", "web-dl",
    "webdl", "hdrip", "remux", "hdr", "hdr10", "dolby", "dv", "sdr", "uhd",
    "x264", "x265", "h264", "h265", "hevc", "avc", "10bit", "8bit", "60fps",
    "aac", "ac3", "dts", "ddp", "dd5", "truehd", "atmos", "flac", "mp3",
    "chs", "cht", "gb", "big5", "简", "繁", "中字", "双语", "国语", "粤语",
    "内嵌", "外挂", "字幕", "repack", "proper", "extended", "imax", "remastered",
    "complete", "season", "collection", "合集", "全季", "全集", "更新至",
)

# 常见视频扩展名（文件名结尾）
_VIDEO_EXT_PAT = re.compile(r"\.(mkv|mp4|avi|rmvb|ts|m2ts|mov|flv|wmv|iso|mpg|mpeg)$", re.I)
# 年份：1900-2099，且不被其它数字包夹
_YEAR_PAT = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9])")
# 季集：S01E02 / S01 / E02 / 第二季 / 第 3 集
_SEASON_PAT = re.compile(r"(?i)(?:^|[^a-z0-9])(s\d{1,2}(?:e\d{1,3})?|e\d{1,3})(?:[^a-z0-9]|$)")
_CJK_SEASON_PAT = re.compile(r"第\s*[0-9一二三四五六七八九十百]+\s*(?:季|部|集|话|回)")
# 分辨率
_RES_PAT = re.compile(r"(?i)(?:^|[^a-z0-9])((?:480|576|720|1080|1440|2160|4320)[pi]|4k|8k)(?:[^a-z0-9]|$)")
# 标题归一化时剔除的字符（与 transfer.py._clean_title 同口径）
_NON_TITLE_PAT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _clean_title(title):
    """去除所有非中英文数字字符并转小写（与 transfer.py 同口径）。"""
    if not title:
        return ""
    return _NON_TITLE_PAT.sub("", str(title).lower())


def _core_title(title):
    """从「干净标题」里剥掉年份/季/分辨率，得到作品核心名（与 transfer.py 同口径）。

    入参必须是 _clean_title 之后的结果，调用顺序不能颠倒。
    """
    if not title:
        return ""
    t = _YEAR_PAT.sub("", title)
    t = _SEASON_PAT.sub("", t)
    t = _CJK_SEASON_PAT.sub("", t)
    t = _RES_PAT.sub("", t)
    return t.strip()


def _has_release_group(tok):
    """token 是否为发布组/编码/来源等噪声（用于界定标题结束位置）。

    只认显式噪声词与「x264-GROUP」这类含明确编码标识的复合 token，
    不把年份/季集/分辨率算进来（它们由调用方单独判断）。
    """
    if not tok:
        return False
    low = tok.lower()
    for part in re.split(r"[-+@]", low):
        if part and part in _NOISE_TOKENS:
            return True
    if len(low) == 8 and re.fullmatch(r"[0-9a-f]{8}", low):
        return True          # 8 位十六进制：常见发布组后缀
    if low.startswith("www.") or ".com" in low or ".cn" in low or ".net" in low:
        return True          # 站点水印
    return False


def _is_noise_token(tok):
    """token 是否表示「标题已结束」（年份/季集/分辨率/集数/纯数字/噪声词）。"""
    low = (tok or "").strip(" .-_[]()（）【】{}")
    if not low:
        return True
    if _has_release_group(low):
        return True
    if _YEAR_PAT.fullmatch(low) or _RES_PAT.fullmatch(low):
        return True
    if _SEASON_PAT.match(low) or _CJK_SEASON_PAT.search(low):
        return True
    if re.fullmatch(r"\d+", low):       # 纯数字：集数/序号（注意：不影响「1917」这类纯数字片名）
        return True
    return False


def _strip_extension(name):
    """剥离视频扩展名。

    先按白名单（.mkv/.mp4/...）剥；白名单不命中时按「最后一个点 + 2~5 位字母数字」
    兜底剥掉（`Show.S02E05` 这类从上游 API 拿到的、被截断的奇怪文件名），
    否则尾部扩展名会被当成标题的一部分。
    """
    raw = str(name or "")
    if not raw:
        return ""
    stripped = _VIDEO_EXT_PAT.sub("", raw)
    if stripped != raw:
        return stripped
    if "." in raw:
        head, _, tail = raw.rpartition(".")
        if head and re.fullmatch(r"[A-Za-z0-9]{2,5}", tail):
            return head
    return raw


def _split_fields(stem):
    """把文件名主体切成 token。

    `.` 在发布名里既是分隔符（Movie.Title.2019）又可能是标题的一部分（S.W.A.T.），
    因此在「有点号、同时又有空格/连字符/中文/数字夹点」时才拆点号。
    """
    s = stem.replace("_", " ").replace("·", " ")
    has_sep = bool(re.search(r"[\s\-]", s)) or bool(re.search(r"[\u4e00-\u9fff]", s))
    if "." in s and (has_sep or re.search(r"\d\.\w", s)):
        s = s.replace(".", " ")
    return [t for t in re.split(r"\s+", s) if t]


def _looks_like_episode_token(tok):
    """token 是否为「剧集标记的一部分」：S02E05 / s02e05 / E05 / 02x05。"""
    low = (tok or "").strip(" .-_[]()（）【】{}").lower()
    if not low:
        return False
    if re.fullmatch(r"s\d{1,2}(e\d{1,3})?", low):
        return True
    if re.fullmatch(r"e\d{1,3}", low):
        return True
    if re.fullmatch(r"\d{1,2}x\d{1,3}", low):
        return True
    return False


def _episode_cut(tokens):
    """找「季集标记」在 token 序列里的起始下标。

    要点：季集标记可能被 `.` 粘连成**一个** token（`Show.S02E05.mkv` 会被
    扩展名剥离后只剩整串 `Show.S02E05`，点号因缺分隔线索未被拆开），
    因此这里用正则从 token 里**切出前缀**，而不是只做整 token 比较。
    """
    pat = re.compile(r"(?i)(?<![a-z0-9])(s\d{1,2}(?:e\d{1,3})?|e\d{1,3}|\d{1,2}x\d{1,3})(?![a-z0-9])")
    for i, t in enumerate(tokens):
        m = pat.search(t)
        if not m:
            continue
        # 前缀本身就是标题的一部分（如 `Show`）：截断到前缀之前
        if m.start() > 0:
            return i, m.start()
        return i, None
    return None, None


def _looks_like_bare_episode(tokens, cut):
    """`Show 11` / `Show 05` 这种「标题 + 裸集数」形态判定。

    只在「去掉尾部数字后仍有 ≥2 个 token」时成立，避免把
    `流浪地球2`、`1917.2019.1080p` 这类**纯数字片名**当成集数截断。
    """
    if cut <= 0 or cut >= len(tokens):
        return False
    digit = tokens[cut]
    if not re.fullmatch(r"\d{1,3}", digit):
        return False
    head = cut - 1
    if head - 1 < 0:
        return False
    return len(re.sub(r"[^A-Za-z0-9]", "", tokens[head])) >= 2


def _cut_bare_episode(tokens):
    """`Show 11` 形态：标题 + 「裸集数」。

    与上一版「先截断再前看」不同，这里**只在能构造出合法标题时**才截：
    从尾部往前找「与前面一部分明显不相连」的 1~2 位数字 token，
    要求去掉它之后剩下的**首个 token**（标题主语）长度 ≥ 2。
    这样 `流浪地球2 2023 1080p` 不会被截成 `流浪地球`，
    而 `Show 11` / `Show.US 11` 能正确截成 `Show` / `Show.US`。
    """
    for i in range(len(tokens) - 1, 0, -1):
        if not re.fullmatch(r"\d{1,3}", tokens[i]):
            continue
        if len(tokens[i]) > 2:
            continue
        head = re.sub(r"[^A-Za-z0-9]", "", tokens[i - 1])
        if len(head) >= 2 and len(re.sub(r"[^A-Za-z0-9]", "", tokens[0])) >= 2:
            return i
    return None


def _split_filename_noise(name):
    """文件名 → (标题, 原始文件名)。

    算法（issue #27）：
      1. 先做「季集标记」定位（`S02E05` / `E05` / 裸集数 / `第二季`），它比年份更可靠；
      2. 再找「第一个噪声 token」（年份/分辨率/发布编码/站点水印）作为标题结束位；
      3. 向后扩展那些「明显仍是标题一部分」的短 token —— 否则
         `Some.Show.US.S02E05` 会在 `US` 处被截断。
    扩展条件刻意保守：不含发布组标识、且长度 ≤ 2（`US`、`II`、`S`）。
    """
    raw = str(name or "").strip()
    if not raw:
        return "", ""
    stem = _strip_extension(raw)
    tokens = _split_fields(stem)
    if not tokens:
        return _clean_title(stem), raw

    cut = len(tokens)
    ep_start, ep_prefix_len = _episode_cut(tokens)
    if ep_start is not None and (ep_start > 0 or ep_prefix_len is not None):
        if ep_prefix_len is not None:
            # 季集标记粘在标题同一个 token 里（`Show.S02E05`）：保留前缀作为标题
            tokens = tokens[:ep_start] + [tokens[ep_start][:ep_prefix_len]]
            cut = len(tokens)
        else:
            cut = ep_start
    else:
        for i, t in enumerate(tokens):
            if _is_noise_token(t):
                cut = i
                break
        bare = _cut_bare_episode(tokens)
        if bare is not None:
            cut = bare      # `Show 11` 的 11 是集数，不属于标题

    def _extendable(tok):
        low = tok.lower().strip(" .-_[]()（）【】{}")
        if not low or len(low) > 2:
            return False
        return not _has_release_group(low)

    while cut < len(tokens) and _extendable(tokens[cut]):
        cut += 1

    title = " ".join(tokens[:cut]).strip(" .-_[]()（）【】{}")
    if not title:
        # 全是噪声（如 `2019.1080p`）或整段被季集规则吃掉（如 `第一季.mkv`）：
        # 退回**剥离扩展名后的原始串**（不能带 .mkv，否则核心名会变成 mkv），
        # 交给相似度匹配兜底。
        title = _clean_title(stem)
    return title, raw


def _fallback_core(stem):
    """fallback 场景（整段被季集规则吃掉）的核心名：直接清洗、不再剥季。

    `第一季.mkv` 这类只有季标记的文件名，_core_title 会把 `第1季` 整段清空，
    导致核心名为空 → 永远匹配不上。这里退一步：用清洗后的整串当核心名，
    至少能和 TMDB 里的中文标题做相似度比较。
    """
    return _clean_title(stem)


def filename_hints(name):
    """从文件名提取匹配线索：标题 / 核心名 / 年份 / 季号 / 原始串。"""
    title, raw = _split_filename_noise(name)
    stem = _strip_extension(raw)
    clean = _clean_title(title) or _clean_title(stem)
    core = _core_title(clean) or _fallback_core(stem)
    m = _YEAR_PAT.search(raw)
    year = int(m.group(1)) if m else 0
    if not year:
        m2 = _YEAR_PAT.search(title)
        year = int(m2.group(1)) if m2 else 0
    season = 0
    ms = _SEASON_PAT.search(raw)
    if ms:
        mm = re.search(r"\d+", ms.group(1))
        season = int(mm.group(0)) if mm else 0
    if not season:
        mc = _CJK_SEASON_PAT.search(raw)
        if mc:
            digits = re.sub(r"[^0-9]", "", mc.group(0))
            season = int(digits) if digits else 0
    return {"title": title, "core": core, "year": year, "season": season,
            "raw": raw, "clean": clean}


# ---------- 候选打分 ----------
# 保守阈值：宁可「匹配不上、显示占位卡」，也不要给某部片子挂上别的海报。
MATCH_MIN_SCORE = 0.55
# 搜索轮次与回退页数（search/multi 命中差时再单独搜 movie / tv）
MATCH_MAX_ROUNDS = 2
MATCH_FALLBACK_PAGES = 2


def _norm_name(s):
    return _NON_TITLE_PAT.sub("", str(s or "").lower())


def _similarity(a, b):
    """核心名相似度（0~1）：完全相等 1.0，包含关系 0.9，其余按字符重合度。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    sa, sb = set(a), set(b)
    return len(sa & sb) / float(len(sa | sb))


def _score_candidate(hints, item, media_type):
    """给一个 TMDB 候选打分；低于阈值的直接判为「不是同一部」。

    闸门是核心标题相关性：核心名完全对不上时，即使年份一致也不给分，
    否则「同名年份」会把不相干的作品凑成一对。
    """
    core = hints.get("core") or _norm_name(hints.get("title"))
    if not core:
        return 0.0
    cand_titles = [item.get("title") or "", item.get("original_title") or ""]
    best = 0.0
    for ct in cand_titles:
        nc = _norm_name(ct)
        if not nc:
            continue
        best = max(best, _similarity(core, nc))
    if best < 0.5:
        return 0.0
    score = best
    if best == 1.0:
        score = 0.9
    year = hints.get("year") or 0
    cy = item.get("year") or 0
    if year and cy:
        if year == cy:
            score += 0.15
        else:
            score -= 0.35          # 年份冲突：很可能是同名不同作
    if media_type in ("movie", "tv") and item.get("_media_type") and item["_media_type"] != media_type:
        score -= 0.1
    return max(0.0, min(score, 1.0))


def _extract_candidates(data, default_media_type=None):
    """把 TMDB 搜索响应转成候选列表（补上 media_type / year）。

    `/search/movie`、`/search/tv` 的返回条目**不带** `media_type` 字段，
    必须由调用方按 endpoint 补（`default_media_type`），否则会被当成人物结果丢掉。
    """
    out = []
    for item in (data or {}).get("results", []) or []:
        mt = item.get("media_type") or default_media_type
        if mt not in ("movie", "tv"):
            continue
        parsed = _parse_item(item, mt)
        parsed["_media_type"] = mt
        if not parsed.get("year"):
            rd = item.get("release_date") or item.get("first_air_date") or ""
            try:
                parsed["year"] = int(rd[:4])
            except (ValueError, TypeError):
                parsed["year"] = 0
        out.append(parsed)
    return out


def _search_tmdb_raw(endpoint, params):
    """发起一次 TMDB 搜索；网络/配置异常返回 None（区别于「搜到了但没结果」）。"""
    api_key = _get_api_key()
    if not api_key:
        return None
    q = dict(params)
    q["api_key"] = api_key
    q["language"] = _LANG
    q["include_adult"] = "false"
    try:
        return _tmdb_request_with_failover(endpoint, q)
    except Exception as e:
        log("TMDB 匹配搜索失败 {}: {}".format(endpoint, e))
        return None


def match_tmdb_by_name(name, media_type=""):
    """文件名 → 最佳 TMDB 作品（媒体库图文卡片用）；无法匹配时返回 None。

    返回字段与 _parse_item 一致（title/poster/rating/year/votes/media_type...），
    额外带 score / matched_by 便于排查「为什么挂了这张海报」。
    """
    hints = filename_hints(name)
    query = hints["title"] or hints["raw"]
    if not query:
        return None
    cache_key = "match:{}:{}:{}".format(_norm_name(hints["raw"]), hints["year"], media_type)
    now = time.time()
    with _tmdb_lock:
        if cache_key in _tmdb_cache:
            ct, cd = _tmdb_cache[cache_key]
            if now - ct < _TMDB_TTL:
                return cd

    import urllib.parse
    quoted = urllib.parse.quote(query)
    candidates = []
    unreachable = False

    # 第 1 轮：multi（含年份时先带年份，再看不带年份的结果）
    rounds = [{"query": quoted, "page": "1"}, {"query": quoted, "page": "2"}][:MATCH_MAX_ROUNDS]
    if hints["year"]:
        rounds.insert(0, {"query": quoted, "year": str(hints["year"]), "page": "1"})
    for params in rounds:
        data = _search_tmdb_raw("/search/multi", params)
        if data is None:
            unreachable = True
            break
        candidates.extend(_extract_candidates(data))

    # 第 2 轮：multi 未命中时，按具体类型再搜（部分作品只在 movie/tv 命名空间可检索）
    if not candidates and not unreachable:
        for mt in (("movie", "tv") if not media_type else (media_type,)):
            for page in range(1, MATCH_FALLBACK_PAGES + 1):
                data = _search_tmdb_raw("/search/{}".format(mt),
                                        {"query": quoted, "page": str(page)})
                if data is None:
                    unreachable = True
                    break
                candidates.extend(_extract_candidates(data, default_media_type=mt))
            if unreachable or candidates:
                break

    if unreachable and not candidates:
        return None      # 不写缓存：网络恢复后重试应能拿到结果

    best, best_score = None, 0.0
    for c in candidates:
        s = _score_candidate(hints, c, media_type)
        if s > best_score:
            best, best_score = c, s
    if best is not None and best_score < MATCH_MIN_SCORE:
        best = None

    result = None
    if best is not None:
        result = dict(best)
        result["score"] = round(best_score, 3)
        result["matched_by"] = hints["title"]
        result["query"] = query
        result.pop("_media_type", None)

    with _tmdb_lock:
        _tmdb_cache[cache_key] = (now, result)
        _prune_cache()
    return result


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
