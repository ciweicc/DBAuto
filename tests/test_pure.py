import os
import sys
import tempfile
import time
import queue

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import requests
import requests.exceptions
from unittest import mock

from transfer import _clean_title, _core_title, _extract_meta, search_pansou
from utils import _http_retryable, _http_backoff
import routes_transfer
import link_check


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


class TestLinkCheckQueue:
    """P3：链接检测异步队列——请求线程零占用，并发由 worker 池限定，
    从架构上根除「无界 ThreadedHTTPServer 线程被 QAS 占满 → 饿死搜索/配置接口」的链路。"""

    def test_busy_when_queue_full(self):
        # 模拟队列已满：put 抛 Full -> enqueue 返回 busy，且不留僵尸任务
        with mock.patch.object(link_check._q, "put", side_effect=queue.Full):
            r = link_check.enqueue("http://x")
            assert r["state"] == "busy"
            assert "task_id" not in r
            # 不应残留 pending 僵尸条目
            assert link_check.get_status(link_check._task_id("http://x"))["state"] == "unknown"

    def test_done_via_worker_and_cache(self):
        fake = mock.MagicMock()
        fake.get_share_detail.return_value = {"success": True, "message": "ok"}
        with mock.patch("transfer._get_qas_client", return_value=fake):
            link_check.start_workers()
            r = link_check.enqueue("http://a")
            assert r["state"] in ("pending", "running", "done")
            tid = r["task_id"]
            # 轮询直到 done
            s = None
            for _ in range(60):
                s = link_check.get_status(tid)
                if s["state"] == "done":
                    break
                time.sleep(0.05)
            assert s["state"] == "done"
            assert s["result"]["success"] is True
            # 再次 enqueue 同 url -> 命中缓存（不重复打 QAS）
            r2 = link_check.enqueue("http://a")
            assert r2["state"] == "done"
            assert fake.get_share_detail.call_count == 1

    def test_unknown_for_missing_task(self):
        assert link_check.get_status("nonexistent-task-id")["state"] == "unknown"
