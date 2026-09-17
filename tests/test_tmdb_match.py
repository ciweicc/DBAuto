"""媒体库图文卡片（issue #27）：文件名 → TMDB 作品匹配的纯逻辑单测。

用户诉求是「媒体库不要只是文件夹样式，要像 TMDB 那样图文并茂」。网盘里只有文件名，
所以核心是**文件名解析**：解析错了会挂错海报，比不出海报更糟。

本文件不联网：
  - filename_hints / 打分 / 候选提取都是纯函数，直接断言；
  - match_tmdb_by_name 用 mock 替换 _search_tmdb_raw，验证「阈值过滤 / 缓存 / 网络失败不写缓存」。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

from unittest.mock import patch

import tmdb
from tmdb import (
    filename_hints, match_tmdb_by_name, _score_candidate, _extract_candidates,
    _split_filename_noise, _similarity, MATCH_MIN_SCORE,
)


# ---------------------------------------------------------------- 文件名解析
def test_hints_standard_release_name():
    h = filename_hints("复仇者联盟4.2019.2160p.BluRay.x265-GROUP.mkv")
    assert h["title"] == "复仇者联盟4"
    assert h["core"] == "复仇者联盟4"
    assert h["year"] == 2019


def test_hints_drops_resolution_and_source():
    h = filename_hints("流浪地球2.2023.1080p.WEB-DL.mkv")
    assert h["title"] == "流浪地球2"
    assert h["year"] == 2023


def test_hints_drops_season_marker():
    h = filename_hints("三体.S01.2023.2160p.mkv")
    assert h["core"] == "三体"
    assert h["season"] == 1


def test_hints_keeps_cjk_season_word_in_title():
    """`庆余年.第二季` 里的「第二季」是作品名的一部分，不能被吃掉。

    实际网盘里常见「剧名.第N季」命名，季号对 TMDB 搜索没有帮助，
    但把「第二季」从标题里删掉会让搜索词变短、更容易撞到同名作品。
    """
    h = filename_hints("庆余年.第二季.2024.4K.HDR.mkv")
    assert h["core"] == "庆余年"


def test_hints_dotted_english_title():
    h = filename_hints("MOVIE.TITLE.2019.2160p.WEB-DL.x265-GROUP.mkv")
    assert h["core"] == "movietitle"
    assert h["year"] == 2019


def test_hints_short_acronym_title_survives():
    """`S.W.A.T.2017` —— 点号既是分隔符也是标题的一部分，不能拆坏。"""
    h = filename_hints("S.W.A.T.2017.1080p.mkv")
    assert h["core"] == "swat"
    assert h["year"] == 2017


def test_hints_episode_marker_glued_to_title():
    """`Show.S02E05`：季集标记与标题粘在一个 token 里（上游 API 返回的怪名字）。

    踩坑：初版按「原始串是否含分隔线索」决定要不要拆点号，
    结果 `Show.S02E05` 整串被当成标题，核心名成了 `shows02e05`，永远匹配不上。
    """
    h = filename_hints("Show.S02E05")
    assert h["core"] == "show"
    assert h["season"] == 2


def test_hints_episode_after_region_token():
    """`Some.Show.US.S02E05`：季集标记前的 `US` 属于标题，不能被截掉。"""
    h = filename_hints("Some.Show.US.S02E05.1080p.mkv")
    assert h["core"] == "someshowus"


def test_hints_english_series_with_episode():
    h = filename_hints("Breaking.Bad.S05E14.1080p.mkv")
    assert h["core"] == "breakingbad"
    assert h["season"] == 5


def test_hints_pure_number_title_is_not_treated_as_episode():
    """`1917` 是片名本身（年份也是 1917），不能被当成集数截掉。"""
    h = filename_hints("1917.2019.1080p.mkv")
    assert h["title"].startswith("1917")
    assert h["year"] == 1917


def test_hints_cjk_only_season_name_keeps_core():
    """`第一季.mkv`：整段都是季标记时，核心名不能退化成扩展名。"""
    h = filename_hints("第一季.mkv")
    assert h["core"] == "第一季"


def test_hints_multi_season_range():
    h = filename_hints("老友记.S01-S10.1080p.mkv")
    assert h["core"] == "老友记"


def test_hints_plain_cjk():
    h = filename_hints("星际穿越.mkv")
    assert h["core"] == "星际穿越"
    assert h["year"] == 0


def test_hints_parenthesised_year():
    h = filename_hints("流浪地球2 (2023) [1080p].mp4")
    assert h["core"] == "流浪地球2"
    assert h["year"] == 2023


def test_hints_empty_name():
    assert _split_filename_noise("") == ("", "")
    assert filename_hints("").get("core") is None or filename_hints("").get("core") == ""


# ---------------------------------------------------------------- 相似度 / 打分
def test_similarity_exact_and_containment():
    assert _similarity("abc", "abc") == 1.0
    assert _similarity("abc", "abcd") == 0.9
    assert _similarity("abc", "ab") == 0.9
    assert _similarity("", "abc") == 0.0


def test_score_rejects_unrelated_title():
    hints = filename_hints("复仇者联盟4.2019.2160p.mkv")
    item = {"title": "阿凡达", "original_title": "Avatar", "year": 2019, "media_type": "movie"}
    assert _score_candidate(hints, item, "") == 0.0


def test_score_prefers_matching_year():
    hints = filename_hints("Interstellar.2014.2160p.mkv")
    same = {"title": "Interstellar", "original_title": "Interstellar", "year": 2014}
    other = {"title": "Interstellar", "original_title": "Interstellar", "year": 2030}
    assert _score_candidate(hints, same, "") > _score_candidate(hints, other, "")


def test_score_filters_by_media_type():
    hints = filename_hints("流浪地球2.2023.1080p.mkv")
    movie = {"title": "流浪地球2", "original_title": "流浪地球2", "year": 2023,
             "_media_type": "movie"}
    tv = {"title": "流浪地球2", "original_title": "流浪地球2", "year": 2023, "_media_type": "tv"}
    assert _score_candidate(hints, movie, "movie") > _score_candidate(hints, tv, "movie")


def test_extract_candidates_skips_person_results():
    data = {"results": [
        {"id": 1, "media_type": "person", "name": "某人"},
        {"id": 2, "media_type": "movie", "title": "电影", "release_date": "2019-01-01"},
        {"id": 3, "media_type": "tv", "name": "剧集", "first_air_date": "2020-02-02"},
    ]}
    out = _extract_candidates(data)
    assert [c["id"] for c in out] == [2, 3]
    assert out[0]["_media_type"] == "movie"
    assert out[1]["year"] == 2020


# ---------------------------------------------------------------- match_tmdb_by_name
def _movie(title, year=2019, mt="movie"):
    return {"id": 99, "title": title, "original_title": title, "year": year,
            "media_type": mt, "poster": "https://img/x.jpg", "rating": 8.1, "votes": 1000}


def test_match_returns_best_candidate():
    data = {"results": [
        {"id": 1, "media_type": "movie", "title": "别的电影", "release_date": "2019-01-01"},
        {"id": 2, "media_type": "movie", "title": "复仇者联盟4", "release_date": "2019-04-24"},
    ]}
    with patch.object(tmdb, "_search_tmdb_raw", return_value=data):
        with patch.object(tmdb, "_get_api_key", return_value="k"):
            tmdb._tmdb_cache.clear()
            got = match_tmdb_by_name("复仇者联盟4.2019.2160p.BluRay.x265.mkv")
    assert got and got["id"] == 2
    assert got["matched_by"] == "复仇者联盟4"
    assert got["score"] >= MATCH_MIN_SCORE


def test_match_returns_none_when_all_below_threshold():
    data = {"results": [
        {"id": 1, "media_type": "movie", "title": "完全不相干", "release_date": "1998-01-01"},
    ]}
    with patch.object(tmdb, "_search_tmdb_raw", return_value=data):
        with patch.object(tmdb, "_get_api_key", return_value="k"):
            tmdb._tmdb_cache.clear()
            assert match_tmdb_by_name("复仇者联盟4.2019.2160p.mkv") is None


def test_match_without_api_key_returns_none():
    with patch.object(tmdb, "_get_api_key", return_value=""):
        tmdb._tmdb_cache.clear()
        assert match_tmdb_by_name("星际穿越.mkv") is None


def test_match_network_failure_not_cached():
    tmdb._tmdb_cache.clear()
    with patch.object(tmdb, "_search_tmdb_raw", return_value=None):
        with patch.object(tmdb, "_get_api_key", return_value="k"):
            assert match_tmdb_by_name("星际穿越.mkv") is None
    assert not [k for k in tmdb._tmdb_cache if k.startswith("match:")], \
        "网络失败不应写缓存，否则 TMDB 恢复后仍显示占位卡"


def test_match_result_is_cached():
    data = {"results": [{"id": 7, "media_type": "movie", "title": "星际穿越",
                         "release_date": "2014-11-07"}]}
    # 命中即返回，不再走 multi 的第 2 页 / 回退搜索
    tmdb._tmdb_cache.clear()
    with patch.object(tmdb, "_get_api_key", return_value="k"):
        with patch.object(tmdb, "_search_tmdb_raw", return_value=data) as m:
            first = match_tmdb_by_name("星际穿越.2014.2160p.mkv")
            calls_after_first = m.call_count
            assert calls_after_first >= 1
            second = match_tmdb_by_name("星际穿越.2014.2160p.mkv")
            assert m.call_count == calls_after_first, "第二次应命中缓存，不再打网络"
    assert first and first["id"] == 7
    assert second == first


def test_match_falls_back_to_typed_search():
    """multi 搜不到时，按 movie / tv 命名空间再搜一轮。"""
    calls = []

    def fake(endpoint, params):
        calls.append(endpoint)
        if endpoint == "/search/multi":
            return {"results": []}
        # /search/movie 的返回条目不带 media_type，须由调用方按 endpoint 补
        return {"results": [{"id": 11, "title": "星际穿越", "release_date": "2014-11-07"}]}

    tmdb._tmdb_cache.clear()
    with patch.object(tmdb, "_get_api_key", return_value="k"):
        with patch.object(tmdb, "_search_tmdb_raw", side_effect=fake):
            got = match_tmdb_by_name("星际穿越.2014.2160p.mkv")
    assert got and got["id"] == 11
    assert "/search/movie" in calls
