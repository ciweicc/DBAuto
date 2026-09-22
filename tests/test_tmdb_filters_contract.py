"""TMDB 榜单筛选契约测试（issue #99）。

用户报告：「在特定筛选条件时无法正确响应特定筛选」。
截图里的条件是 电视剧 + 热门趋势 + 地区=中国大陆 + 语言=华语，
但列表纹丝不动 —— 因为 TMDB 只有 `/discover` 支持
`with_origin_country` / `with_original_language`：

- `/trending/*`、`/movie|tv/popular`、`/movie|tv/top_rated` 完全不接受这两个参数；
- `now_playing/upcoming/on_the_air/airing_today` 只接受 `region`（地区），
  仍不接受原始语言过滤；
- 此前 `country` 被直接丢弃、`language` 被误当成 TMDB 的**展示语言**
  （`language=zh` 既不是有效的展示语言，也从未映射到 `with_original_language`）。

修复语义：只要「地区」或「语言」非「全部」，就改走 `/discover`，
让这两项「快捷筛选·全榜单通用」的控件在**任何榜单**下都真正生效。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app_modules"))
import tmdb  # noqa: E402


@pytest.fixture
def capture(monkeypatch):
    """拦截发往 TMDB 的请求，返回 (endpoint, params) 列表。"""
    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"results": [], "total_pages": 1, "total_results": 0, "page": 1}

    def fake(endpoint, params=None, **kw):
        calls.append((endpoint, dict(params or {})))
        return _Resp()

    monkeypatch.setattr(tmdb, "_tmdb_request_with_failover", fake)
    monkeypatch.setattr(tmdb, "_get_api_key", lambda: "k")
    tmdb._tmdb_cache.clear()
    return calls


def _params_last(calls):
    endpoint, params = calls[-1]
    return endpoint, {k: v for k, v in params.items() if k != "api_key"}


# 「图片里那一个组合」：只要地区/语言非空，就必须走 discover 并带上过滤参数。
@pytest.mark.parametrize("media_type,list_type", [
    ("movie", "trending"), ("movie", "popular"), ("movie", "top_rated"),
    ("movie", "now_playing"), ("movie", "upcoming"),
    ("tv", "trending"), ("tv", "popular"), ("tv", "top_rated"),
    ("tv", "on_the_air"), ("tv", "airing_today"),
])
def test_region_and_language_are_honored_on_every_list_type(capture, media_type, list_type):
    """任何榜单 + 地区(CN) + 语言(zh)：必须请求 discover，且两项都成为过滤条件。"""
    tmdb.get_tmdb_list(media_type=media_type, list_type=list_type, page=1,
                       country="CN", language="zh")
    endpoint, params = _params_last(capture)
    assert endpoint == "/discover/{}".format(media_type), \
        "{} + 地区/语言未走 discover，筛选会被丢弃".format(list_type)
    assert params["with_origin_country"] == "CN", "地区未映射到 with_origin_country"
    assert params["with_original_language"] == "zh", "语言未映射到 with_original_language"
    # 展示语言固定 zh-CN，不能被原始语言覆盖
    assert params["language"] == "zh-CN", "展示语言被原始语言污染"


@pytest.mark.parametrize("list_type", ["trending", "popular", "top_rated"])
def test_language_alone_forces_discover(capture, list_type):
    """只选语言（不选地区）也必须走 discover —— 语言过滤只存在于 discover。"""
    tmdb.get_tmdb_list(media_type="tv", list_type=list_type, page=1, language="ja")
    endpoint, params = _params_last(capture)
    assert endpoint == "/discover/tv"
    assert params["with_original_language"] == "ja"
    assert "with_origin_country" not in params


@pytest.mark.parametrize("list_type", ["trending", "popular", "top_rated"])
def test_region_alone_forces_discover(capture, list_type):
    """只选地区也必须走 discover —— 这三个榜单原生不认 country。"""
    tmdb.get_tmdb_list(media_type="movie", list_type=list_type, page=1, country="CN")
    endpoint, params = _params_last(capture)
    assert endpoint == "/discover/movie"
    assert params["with_origin_country"] == "CN"


@pytest.mark.parametrize("media_type,list_type,endpoint", [
    ("movie", "trending", "/trending/movie/week"),
    ("movie", "popular", "/movie/popular"),
    ("movie", "top_rated", "/movie/top_rated"),
    ("movie", "now_playing", "/movie/now_playing"),
    ("movie", "upcoming", "/movie/upcoming"),
    ("tv", "trending", "/trending/tv/week"),
    ("tv", "popular", "/tv/popular"),
    ("tv", "top_rated", "/tv/top_rated"),
    ("tv", "on_the_air", "/tv/on_the_air"),
    ("tv", "airing_today", "/tv/airing_today"),
])
def test_no_region_no_language_keeps_native_list(capture, media_type, list_type, endpoint):
    """地区/语言都是「全部」时，原生榜单不得被改写 —— 这是修复的边界。"""
    tmdb.get_tmdb_list(media_type=media_type, list_type=list_type, page=1)
    got, params = _params_last(capture)
    assert got == endpoint
    assert "with_origin_country" not in params
    assert "with_original_language" not in params


def test_discover_still_supports_region_language_and_advanced_filters(capture):
    """discover 路径不能被回归：地区/语言/排序/评分/年份/平台都要在。"""
    tmdb.get_tmdb_list(media_type="movie", list_type="discover", page=1,
                       country="CN", language="zh", sort_by="vote_average.desc",
                       min_rating=7.5, year=2024, genre_id=18, watch_providers="8|9")
    endpoint, params = _params_last(capture)
    assert endpoint == "/discover/movie"
    assert params["with_origin_country"] == "CN"
    assert params["with_original_language"] == "zh"
    assert params["sort_by"] == "vote_average.desc"
    assert params["vote_average.gte"] == 7.5
    assert params["primary_release_year"] == 2024
    assert params["with_genres"] == 18
    assert params["with_watch_providers"] == "8|9"


def _tmdb_js():
    js_path = os.path.join(os.path.dirname(__file__), "..", "static", "src", "scripts", "tmdb.js")
    with open(js_path, encoding="utf-8") as f:
        return f.read()


def test_frontend_derives_discover_for_region_and_language():
    """前端必须把地区/语言纳入「实际请求 discover」的推导（issue #99）。"""
    js = _tmdb_js()
    assert "function effectiveTmdbListType" in js, "缺少「实际榜单」推导函数"
    fn = js.split("function effectiveTmdbListType", 1)[1].split("function ", 1)[0]
    assert "getId('tmdbRegion')" in fn, "实际榜单推导未读取地区控件"
    assert "getId('tmdbLanguage')" in fn, "实际榜单推导未读取语言控件"
    assert "return 'discover'" in fn, "地区/语言非空时未推导为 discover"


def test_frontend_does_not_recursively_autoswitch():
    """回归：不得再用「设 select.value + 递归 onTmdbFilterChange」切 discover。

    旧写法在 discover 选项缺失时会静默设值失败并无限递归，
    表现是海报墙一直停在骨架屏、一张卡都渲染不出来。
    """
    js = _tmdb_js()
    load_fn = js.split("async function loadTmdbList", 1)[1].split("\nfunction ", 1)[0]
    assert "onTmdbFilterChange();" not in load_fn, \
        "loadTmdbList 仍在递归调用 onTmdbFilterChange（会无限递归）"
    assert "syncTmdbListTypeSelect()" in load_fn, "loadTmdbList 未同步实际榜单到下拉框"


def test_frontend_sync_guards_missing_discover_option():
    """把 select.value 设成不存在的选项是静默无效的 —— 同步前必须先探测存在性。"""
    js = _tmdb_js()
    assert "function tmdbListTypeExists" in js, "缺少 option 存在性探测"
    sync_fn = js.split("function syncTmdbListTypeSelect", 1)[1].split("\nfunction ", 1)[0]
    assert "tmdbListTypeExists(eff)" in sync_fn, \
        "同步榜单下拉前未校验 discover 选项是否存在"
