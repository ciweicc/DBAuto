import os
import sys
import tempfile
import time
import queue
import threading

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

    def test_concurrent_same_url_dedup(self):
        # 同一 URL 在 pending 期间重复 enqueue：返回同一 task_id，且只打一次 QAS
        fake = mock.MagicMock()
        fake.get_share_detail.return_value = {"success": True, "message": "ok"}
        with mock.patch("transfer._get_qas_client", return_value=fake):
            link_check.start_workers()
            url = "http://dedup/1"
            r1 = link_check.enqueue(url)
            assert r1["state"] in ("pending", "running", "done")
            r2 = link_check.enqueue(url)  # 重复提交
            assert r2["task_id"] == r1["task_id"]
            # 轮询到 done
            s = None
            for _ in range(60):
                s = link_check.get_status(r1["task_id"])
                if s["state"] == "done":
                    break
                time.sleep(0.05)
            assert s["state"] == "done"
            assert fake.get_share_detail.call_count == 1  # 去重生效，只一次

    def test_running_state_visible(self):
        # worker 执行期间状态应为 running（用慢 QAS 放大窗口，紧密轮询捕捉）
        release = threading.Event()
        fake = mock.MagicMock()
        def _slow(*a, **k):
            release.wait(5)
            return {"success": True, "message": "ok"}
        fake.get_share_detail.side_effect = _slow
        with mock.patch("transfer._get_qas_client", return_value=fake):
            link_check.start_workers()
            r = link_check.enqueue("http://running/1")
            tid = r["task_id"]
            observed = set()
            # 紧密轮询捕捉 running 态（最多 ~2s）
            for _ in range(400):
                st = link_check.get_status(tid)["state"]
                observed.add(st)
                if st == "done":
                    break
                time.sleep(0.005)
            release.set()
            assert "running" in observed, "应观察到 running 态，实际: {}".format(observed)

    def test_real_queue_full_busy(self):
        # 真实路径：直接灌满底层队列（不 mock put），再 enqueue 触发 queue.Full -> busy
        for i in range(link_check._LINK_QUEUE_MAX):
            link_check._q.put("http://fill/%d" % i, block=False)
        try:
            r = link_check.enqueue("http://overflow-unique")
            assert r["state"] == "busy"
            assert "task_id" not in r
            # 未留下僵尸 pending 条目
            assert link_check.get_status(link_check._task_id("http://overflow-unique"))["state"] == "unknown"
        finally:
            # 排空队列，避免 worker 拿到无对应 _store 的 dummy（worker 会安全跳过）
            while not link_check._q.empty():
                try:
                    link_check._q.get_nowait()
                except Exception:
                    pass

    def test_sweeper_cleans_expired(self):
        # 真实 sweeper 线程：过期任务（ts 旧 + TTL=0）应被清理（含 _url_index 回收）
        fake = mock.MagicMock()
        fake.get_share_detail.return_value = {"success": True, "message": "ok"}
        with mock.patch("transfer._get_qas_client", return_value=fake):
            r = link_check.enqueue("http://sweep/1")
            tid = r["task_id"]
            for _ in range(60):
                if link_check.get_status(tid)["state"] == "done":
                    break
                time.sleep(0.05)
            with link_check._lock:
                link_check._store[tid]["ts"] = 0  # 标记为过期
            old_int, old_ttl = link_check._LINK_SWEEP_INTERVAL, link_check._LINK_TASK_TTL
            link_check._LINK_SWEEP_INTERVAL = 0
            link_check._LINK_TASK_TTL = 0
            orig_sleep = link_check.time.sleep
            link_check.time.sleep = lambda *a, **k: None  # 让 sweeper 立即跑一次清理
            try:
                threading.Thread(target=link_check._sweeper_loop, daemon=True).start()
                time.sleep(0.1)  # 等 sweeper 执行一轮
                with link_check._lock:
                    assert tid not in link_check._store
                    assert "http://sweep/1" not in link_check._url_index
            finally:
                link_check.time.sleep = orig_sleep
                link_check._LINK_SWEEP_INTERVAL = old_int
                link_check._LINK_TASK_TTL = old_ttl


class TestLinkCheckRoute:
    """B：路由层契约测试——/api/check_link(入队) 与 /api/check_link/status(轮询) 的响应形状。
    此前提交套件未覆盖这两条路由，本类用 TransferRouteMixin + fake _send_json 断言响应契约。"""

    def _make_handler(self):
        class H(routes_transfer.TransferRouteMixin):
            def _get_query_params(self):
                return self._params
            def _send_json(self, obj, code=200):
                self.last_code = code
                self.last = obj
        h = H()
        h.last = None
        h.last_code = None
        return h

    def test_check_link_enqueue_shape(self):
        fake = mock.MagicMock()
        fake.get_share_detail.return_value = {"success": True, "message": "ok"}
        with mock.patch("transfer._get_qas_client", return_value=fake):
            link_check.start_workers()
            h = self._make_handler()
            h._params = {"url": "http://route/1"}
            h._handle_transfer_get("/api/check_link")
            assert h.last_code == 200
            assert "state" in h.last
            assert h.last["state"] in ("pending", "running", "done")
            if h.last["state"] == "done":
                assert h.last["valid"] is True
                assert h.last["checked"] is True
            else:
                assert "task_id" in h.last

    def test_check_link_invalid_url_returns_400_error(self):
        # 非法 url 用独立的 error 语义（区别于队列繁忙 busy），前端据此渲染「链接无效」
        h = self._make_handler()
        h._params = {"url": ""}
        h._handle_transfer_get("/api/check_link")
        assert h.last_code == 400
        assert h.last["state"] == "error"

    def test_check_link_status_polls_to_done(self):
        fake = mock.MagicMock()
        fake.get_share_detail.return_value = {"success": True, "message": "ok"}
        with mock.patch("transfer._get_qas_client", return_value=fake):
            link_check.start_workers()
            h = self._make_handler()
            h._params = {"url": "http://route/2"}
            h._handle_transfer_get("/api/check_link")
            tid = h.last.get("task_id")
            assert tid
            out = None
            for _ in range(60):
                hh = self._make_handler()
                hh._params = {"task_id": tid}
                hh._handle_transfer_get("/api/check_link/status")
                if hh.last.get("state") == "done":
                    out = hh.last
                    break
                time.sleep(0.05)
            assert out is not None, "status 轮询应到达 done"
            assert out["state"] == "done"
            assert out["valid"] is True
            assert out["checked"] is True
            # _check_payload 透传 QAS 原始 message（为空时才回退到「链接正常」）
            assert out["message"] == "ok"

    def test_check_link_status_unknown_returns_state_unknown(self):
        # 合法格式但不存在/已过期的 task_id：返回 200 + {state:"unknown"}
        # （400 仅用于畸形/空 task_id，见下一条）
        h = self._make_handler()
        h._params = {"task_id": "does-not-exist"}
        h._handle_transfer_get("/api/check_link/status")
        assert h.last_code == 200
        assert h.last["state"] == "unknown"

    def test_check_link_status_invalid_tid_returns_400(self):
        h = self._make_handler()
        h._params = {"task_id": ""}
        h._handle_transfer_get("/api/check_link/status")
        assert h.last_code == 400
        assert h.last["state"] == "unknown"


class TestLinkCheckHttpIntegration:
    """D：HTTP 集成测试——起真实 ThreadedHTTPServer，经完整 HTTP 栈（含路由分发）打
    /api/check_link 与 /api/check_link/status。auth/rate-limit 在测试中隔离，专注验证链接检测链路本身。"""

    @classmethod
    def setup_class(cls):
        import threading
        # QAS 真实调用用 mock 替换；auth 与 rate-limit 仅做测试隔离（不影响链接检测逻辑）
        cls._qas = mock.patch(
            "transfer._get_qas_client",
            return_value=mock.MagicMock(get_share_detail=lambda u: {"success": True, "message": "ok"}),
        )
        cls._qas.start()
        cls._auth = mock.patch("routes_auth._check_auth", return_value=True)
        cls._auth.start()
        cls._rl = mock.patch("routes._check_rate_limit", return_value=(True, None))
        cls._rl.start()
        from server import ThreadedHTTPServer
        from routes import H
        link_check.start_workers()
        cls.server = ThreadedHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.server.server_address[1]
        cls._srv = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._srv.start()
        cls.base = "http://127.0.0.1:{}/".format(cls.port)

    @classmethod
    def teardown_class(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        finally:
            cls._qas.stop()
            cls._auth.stop()
            cls._rl.stop()

    def test_check_link_then_poll_status_over_http(self):
        r = requests.get(self.base + "api/check_link", params={"url": "http://http/1"}, timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["state"] in ("pending", "running", "done")
        assert "task_id" in body
        tid = body["task_id"]
        done = None
        for _ in range(80):
            s = requests.get(self.base + "api/check_link/status", params={"task_id": tid}, timeout=10).json()
            if s.get("state") == "done":
                done = s
                break
            time.sleep(0.05)
        assert done is not None, "经 HTTP 轮询应到达 done"
        assert done["state"] == "done"
        assert done["valid"] is True
        assert done["checked"] is True

    def test_check_link_invalid_url_returns_400_over_http(self):
        r = requests.get(self.base + "api/check_link", params={"url": ""}, timeout=10)
        assert r.status_code == 400
        assert r.json()["state"] == "error"
