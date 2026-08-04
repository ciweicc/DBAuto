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
import routes_transfer


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


class TestLinkCheckBackpressure:
    """P3：手动搜索对每个结果做链接检测时，限制并发并快速返回“繁忙”，
    避免无界 ThreadedHTTPServer 线程被 QAS 阻塞调用占满，饿死搜索/配置接口。"""

    def test_busy_when_semaphore_full(self):
        sem = routes_transfer._LINK_CHECK_SEM
        # 占满全部槽位，模拟服务繁忙
        held = [sem.acquire() for _ in range(routes_transfer._LINK_CHECK_MAX_CONCURRENCY)]
        try:
            fake_client = mock.MagicMock()
            r = routes_transfer._safe_check_link("http://x", fake_client, wait=0.1)
            assert r.get("busy") is True
            assert "繁忙" in r.get("message", "")
            # 繁忙时不应调用 QAS，避免继续堆积
            fake_client.get_share_detail.assert_not_called()
        finally:
            for _ in held:
                sem.release()

    def test_normal_call_returns_qas_result_and_releases(self):
        sem = routes_transfer._LINK_CHECK_SEM
        before = sem.acquire()
        try:
            # 让初始状态只剩 2 个槽位，验证正常调用后可再次获取（信号量已释放）
            assert sem.acquire()
            sem.release()
            fake_client = mock.MagicMock()
            fake_client.get_share_detail.return_value = {"success": True, "message": "ok"}
            r = routes_transfer._safe_check_link("http://x", fake_client, wait=1.0)
            assert r == {"success": True, "message": "ok"}
            fake_client.get_share_detail.assert_called_once_with("http://x")
        finally:
            sem.release()
            if before:
                sem.release()

    def test_exception_releases_semaphore(self):
        sem = routes_transfer._LINK_CHECK_SEM
        # 确保调用前至少有 1 个可用槽位（调用会临时占满 1 个）
        acquired_start = sem.acquire()
        try:
            fake_client = mock.MagicMock()
            fake_client.get_share_detail.side_effect = RuntimeError("QAS 挂了")
            try:
                routes_transfer._safe_check_link("http://x", fake_client, wait=1.0)
                assert False, "应当抛出"
            except RuntimeError:
                pass
            # 异常后信号量必须已释放：应能立即再获取
            assert sem.acquire(timeout=0.5)
            sem.release()
        finally:
            if acquired_start:
                sem.release()
