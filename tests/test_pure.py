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


class TestBatchLinkCheck:
    """批量链接检测（issue #88 第三轮：「有办法提高它的搜索速度吗？当前的
    搜索速度实在是有点慢」）。

    逐条检测时 N 条搜索结果 = N 个上游请求 + N 轮 status 轮询；PanSou 的
    check/links 本来就支持一次传多条，这里验证合并后真的只打一次。
    """

    def _fake_pansou(self, states=None):
        """返回一个假 PanSou 客户端，记录每次 check_links 收到的 items。"""
        fake = mock.MagicMock()
        calls = []
        states = states or {}

        def _check(urls):
            calls.append(list(urls))
            return {"results": [
                {"url": u, "state": states.get(u, "ok"),
                 "summary": "链接失效" if states.get(u) == "bad" else "链接正常"}
                for u in urls]}

        fake.check_links.side_effect = _check
        fake.calls = calls
        return fake

    def test_one_request_for_many_urls(self):
        fake = self._fake_pansou()
        urls = ["https://pan.quark.cn/s/a", "https://pan.quark.cn/s/b",
                "https://pan.quark.cn/s/c"]
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            res = link_check.check_links_batch(urls)
        assert len(fake.calls) == 1, "批量检测仍打了 {} 次上游（应为 1）".format(len(fake.calls))
        assert sorted(fake.calls[0]) == sorted(urls)
        assert all(res[u]["valid"] and res[u]["checked"] for u in urls)

    def test_bad_link_flagged_invalid(self):
        fake = self._fake_pansou({"https://pan.quark.cn/s/bad": "bad"})
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            res = link_check.check_links_batch(["https://pan.quark.cn/s/ok",
                                                "https://pan.quark.cn/s/bad"])
        assert res["https://pan.quark.cn/s/ok"]["valid"] is True
        assert res["https://pan.quark.cn/s/bad"]["valid"] is False
        assert "失效" in res["https://pan.quark.cn/s/bad"]["message"]

    def test_locked_is_not_marked_invalid(self):
        """locked/uncertain 只是「无法确认」，标红会让用户放弃有效资源。"""
        fake = self._fake_pansou({"https://pan.quark.cn/s/locked": "locked"})
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            res = link_check.check_links_batch(["https://pan.quark.cn/s/locked"])
        assert res["https://pan.quark.cn/s/locked"]["valid"] is True, \
            "加密/锁定分享被误判为失效"

    def test_upstream_error_marks_unchecked_not_invalid(self):
        fake = mock.MagicMock()
        fake.check_links.side_effect = RuntimeError("PanSou 500")
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            res = link_check.check_links_batch(["https://pan.quark.cn/s/err"])
        assert res["https://pan.quark.cn/s/err"]["valid"] is None
        assert res["https://pan.quark.cn/s/err"]["checked"] is False, \
            "检测失败必须与「链接失效」区分（否则整批有效链接被标红）"

    def test_chunking_limits_request_size(self):
        fake = self._fake_pansou()
        urls = ["https://pan.quark.cn/s/{}".format(i)
                for i in range(link_check._CHECK_BATCH_CHUNK * 2 + 3)]
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            link_check.check_links_batch(urls)
        assert len(fake.calls) == 3, "分片数错：{}".format(len(fake.calls))
        assert max(len(c) for c in fake.calls) <= link_check._CHECK_BATCH_CHUNK

    def test_dedup_and_empty_input(self):
        fake = self._fake_pansou()
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            res = link_check.check_links_batch(["https://pan.quark.cn/s/x",
                                                "https://pan.quark.cn/s/x", ""])
        assert len(fake.calls) == 1
        assert fake.calls[0] == ["https://pan.quark.cn/s/x"], "重复/空链接未去干净"
        assert list(res.keys()) == ["https://pan.quark.cn/s/x"]

    def test_second_batch_hits_result_cache(self):
        """同一次搜索重复检测（用户改关键词再回来）不该再打上游。"""
        fake = self._fake_pansou()
        url = "https://pan.quark.cn/s/cached-batch"
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            link_check.check_links_batch([url])
            res2 = link_check.check_links_batch([url])
        assert len(fake.calls) == 1, "第二次批量检测未命中结果缓存"
        assert res2[url]["valid"] is True

    def test_route_contract(self):
        fake = self._fake_pansou()
        with mock.patch("transfer._get_pansou_client", return_value=fake):
            h = TestLinkCheckRoute._make_handler(self)
            h._params = {"urls": "https://pan.quark.cn/s/a,https://pan.quark.cn/s/b"}
            h._handle_transfer_get("/api/check_links")
        assert h.last_code == 200
        assert h.last["success"] is True
        assert set(h.last["results"].keys()) == {"https://pan.quark.cn/s/a",
                                                 "https://pan.quark.cn/s/b"}

    def test_route_rejects_too_many_urls(self):
        h = TestLinkCheckRoute._make_handler(self)
        h._params = {"urls": ",".join("https://pan.quark.cn/s/{}".format(i)
                                      for i in range(50))}
        h._handle_transfer_get("/api/check_links")
        assert h.last_code == 400
        assert h.last["success"] is False


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


# ---------- 请求级异常兜底（issue #27「访问有点慢」） ----------

def test_route_handler_wraps_get_and_post_in_try():
    """任何未捕获异常都必须转成 500，而不是留下悬停请求。

    线程化 HTTP server 下，处理器抛异常时连接不被响应，
    客户端会一直等到连接超时——表现就是「加载慢」。这里锁死
    do_GET/do_POST 外层存在兜底 try/except + 500。
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app_modules", "routes.py"), encoding="utf-8") as f:
        src = f.read()
    assert "def do_GET(self):" in src and "def do_POST(self):" in src
    assert "internal server error" in src, "缺少 500 兜底响应"
    assert "traceback.format_exc()" in src, "异常未记录堆栈，线上问题无法定位"
    # 两条入口都必须走包装后的实现
    assert "self._do_GET(route=self._route_path())" in src
    assert "self._do_POST(route=self._route_path())" in src


def test_route_exception_returns_500():
    """真实调用：子路由抛异常时，处理器必须发 500 且不向外抛。"""
    import routes

    class Boom(routes.H):
        def _route_path(self):
            return "/api/qas/library"

    h = Boom.__new__(Boom)
    sent = []
    h.path = "/api/qas/library"
    h._send_json = lambda data, status=200: sent.append((data, status))

    import routes_library
    from unittest.mock import patch as _patch
    with _patch.object(routes_library.LibraryRouteMixin, "_handle_library_get",
                       side_effect=RuntimeError("boom")):
        h.do_GET()
    assert sent and sent[-1][1] == 500
