import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import requests
import requests.exceptions
from unittest import mock

from transfer import _clean_title, _core_title, _extract_meta, search_pansou
from utils import _http_retryable, _http_backoff


class TestNormalizeTitles:
    def test_clean_title_strips_punct(self):
        assert _clean_title("电影 A (2024)") == "电影a2024"

    def test_core_title_strips_year_season_res(self):
        # 年份 / 季 / 分辨率都应被剥离，得到核心标题
        assert _core_title("阿凡达2 水之道 2024 1080p") == "阿凡达2水之道"

    def test_extract_meta_year(self):
        assert _extract_meta("阿凡达 2010")[0] == "2010"

    def test_extract_meta_season(self):
        assert _extract_meta("权力的游戏 第3季")[1] is not None


class TestRetryPolicy:
    def test_network_errors_retryable(self):
        assert _http_retryable(requests.exceptions.Timeout())
        assert _http_retryable(requests.exceptions.ConnectionError())

    def test_server_errors_retryable(self):
        resp = requests.Response()
        resp.status_code = 503
        assert _http_retryable(requests.exceptions.HTTPError(response=resp))

    def test_429_retryable(self):
        resp = requests.Response()
        resp.status_code = 429
        assert _http_retryable(requests.exceptions.HTTPError(response=resp))

    def test_404_not_retryable(self):
        resp = requests.Response()
        resp.status_code = 404
        assert not _http_retryable(requests.exceptions.HTTPError(response=resp))

    def test_unrelated_exc_not_retryable(self):
        assert not _http_retryable(ValueError("boom"))


class TestBackoff:
    def test_within_bounds(self):
        for attempt in range(5):
            d = _http_backoff(attempt)
            assert 0.0 <= d <= 5.0

    def test_non_negative_and_finite(self):
        assert _http_backoff(0) >= 0
        assert _http_backoff(3) < 5.0


class TestSearchFailurePropagation:
    def test_search_pansou_propagates_real_cause(self):
        # P2.7：搜索失败不再静默 return []，而是抛出带真实原因的错误
        with mock.patch("transfer._get_pansou_client") as m:
            m.return_value.search.side_effect = RuntimeError("PanSou 上游 500")
            try:
                search_pansou("某电影")
                assert False, "应当抛出异常"
            except RuntimeError as e:
                assert "PanSou" in str(e)
                assert "某电影" in str(e)
