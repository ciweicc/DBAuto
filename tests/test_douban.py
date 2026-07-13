import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from douban import _parse_year, _PATH_MAP, _TV_TYPE_MAP


class TestParseYear:
    def test_string_year(self):
        assert _parse_year("2024") == 2024

    def test_int_year(self):
        assert _parse_year(2023) == 2023

    def test_string_with_text(self):
        assert _parse_year("2024年") == 2024
        assert _parse_year("Released 2023-01-01") == 2023

    def test_empty(self):
        assert _parse_year("") == 0
        assert _parse_year(None) == 0

    def test_invalid(self):
        assert _parse_year("abc") == 0


class TestPathMap:
    def test_movie_paths(self):
        assert "movie/hot" in _PATH_MAP
        assert "movie/latest" in _PATH_MAP
        assert "movie/top" in _PATH_MAP
        assert "movie/underrated" in _PATH_MAP
        for path in ("movie/hot", "movie/latest", "movie/top", "movie/underrated"):
            api_type, category = _PATH_MAP[path]
            assert api_type == "movie"

    def test_tv_paths(self):
        assert "tv/drama" in _PATH_MAP
        api_type, category = _PATH_MAP["tv/drama"]
        assert api_type == "tv"

    def test_variety_paths(self):
        assert "tv/variety" in _PATH_MAP
        api_type, category = _PATH_MAP["tv/variety"]
        assert category == "show"


class TestTvTypeMap:
    def test_domestic(self):
        assert _TV_TYPE_MAP["国产剧"] == "tv_domestic"

    def test_american(self):
        assert _TV_TYPE_MAP["欧美剧"] == "tv_american"

    def test_japanese(self):
        assert _TV_TYPE_MAP["日剧"] == "tv_japanese"

    def test_korean(self):
        assert _TV_TYPE_MAP["韩剧"] == "tv_korean"

    def test_animation(self):
        assert _TV_TYPE_MAP["动画"] == "tv_animation"

    def test_variety_domestic(self):
        assert _TV_TYPE_MAP["国内"] == "show_domestic"

    def test_variety_foreign(self):
        assert _TV_TYPE_MAP["国外"] == "show_foreign"
