"""分享文件清单预览（issue #88：「搜出来点进去不能看集数全不全」）。

验证点：
  1. normalize_share_detail 对 QAS 原始响应的归一化：文件/目录拆分、
     视频计数、体积转换、标题提取；
  2. 失败响应（success=False）必须把真实原因带出来，不能静默变成「空列表」；
  3. 条目数超过上限时截断并置 truncated（DOM 不能被几百个文件撑爆）；
  4. **不做任何集号/缺集推断**（issue #88 第五轮）：用户原话「在日志里
     报出候选集数还是太麻烦了，因为有识别不准的情况」。响应里只有原始
     清单与可数计数，`episodes` / `missing` 这类字段不得回来。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

from unittest.mock import patch

import share_preview
from share_preview import (
    normalize_share_detail, preview_share, MAX_PREVIEW_FILES,
)


def _raw(items, title="某剧 2024", success=True):
    return {"success": success,
            "data": {"share": {"title": title}, "list": items}}


def _f(name, dir=False, size=0, cat=""):
    return {"file_name": name, "dir": dir, "size": size, "fid": "f-" + name,
            "obj_category": cat}


# ------------------------------------------------ normalize_share_detail

def test_normalize_counts_videos_and_dirs():
    raw = _raw([
        _f("剧名/", dir=True),
        _f("S01E01.mkv", size=1024, cat="video"),
        _f("S01E02.mkv", size=2048, cat="video"),
        _f("readme.txt", size=10),
    ])
    r = normalize_share_detail(raw)
    assert r["ok"] is True
    assert r["total"] == 4
    assert r["dir_count"] == 1
    assert r["video_count"] == 2, "视频计数错（它就是用户口中的「集数」）"
    assert r["title"] == "某剧 2024"
    names = [f["name"] for f in r["files"]]
    assert names == ["剧名/", "S01E01.mkv", "S01E02.mkv", "readme.txt"], "顺序/文件名被改动"
    assert r["files"][0]["dir"] is True
    assert r["files"][1]["size"] == 1024, "体积未原样透传"


def test_normalize_accepts_flat_list_without_data_wrapper():
    """兼容无 data 包裹的桩响应（旧版/测试桩）。"""
    r = normalize_share_detail({"list": [_f("01.mp4", cat="video")]})
    assert r["ok"] is True and r["total"] == 1 and r["video_count"] == 1


def test_normalize_failure_keeps_real_reason():
    """失败必须带原因：静默变空列表会让用户以为「这个分享是空的」。"""
    r = normalize_share_detail(
        {"success": False, "data": {"error": "分享已失效"}})
    assert r["ok"] is False
    assert "分享已失效" in r["message"], "真实失败原因被吞掉了"
    assert r["files"] == []

    r2 = normalize_share_detail({"success": False, "message": "未登录"})
    assert r2["ok"] is False and "未登录" in r2["message"]


def test_normalize_tolerates_garbage():
    assert normalize_share_detail(None)["ok"] is False
    assert normalize_share_detail("nope")["ok"] is False
    # list 里混入非 dict / 无名条目时跳过而整体不崩
    r = normalize_share_detail(_raw([None, {"fid": "x"}, _f("a.mp4", cat="video")]))
    assert r["ok"] is True and r["total"] == 1


def test_normalize_truncates_large_share():
    items = [_f("E{:03d}.mp4".format(i), cat="video") for i in range(MAX_PREVIEW_FILES + 25)]
    r = normalize_share_detail(_raw(items))
    assert len(r["files"]) == MAX_PREVIEW_FILES, "未按上限截断（会撑爆下拉）"
    assert r["truncated"] is True
    assert r["total"] == MAX_PREVIEW_FILES + 25, "total 必须是真实总数，不是截断后的"
    assert r["video_count"] == MAX_PREVIEW_FILES + 25, "计数不能在截断前丢掉"


def test_normalize_has_no_episode_inference():
    """响应里不得再出现集号/缺口字段（issue #88 第五轮）。

    用户原话：「在日志里报出候选集数还是太麻烦了，因为有识别不准的情况」。
    集号解析对 `1-22季` / 年份 / 分辨率 / 季号一律会误报，而**误报比不报
    更贵**（用户会去排查一个不存在的缺集）。所以这里钉住「字段不存在」，
    而不是「算得对不对」—— 只要字段回来，就一定会有人再信它。
    """
    raw = _raw([_f("剧.S01E{:02d}.mkv".format(i), cat="video")
                for i in (1, 2, 3, 4, 5, 6, 7, 10, 11, 12)])
    r = normalize_share_detail(raw)
    assert "episodes" not in r, "集号推断字段回来了：{}".format(r.get("episodes"))
    assert "missing" not in json.dumps(r, ensure_ascii=False), \
        "响应体里仍有「缺口」类字段"
    # 可数的东西必须在：条目数 / 目录数 / 视频数（用户据此自己判断）
    assert r["total"] == 10 and r["video_count"] == 10 and r["dir_count"] == 0
    assert [f["name"] for f in r["files"]][0].endswith("01.mkv"), "原始文件名必须保留"


def test_no_episode_inference_on_odd_names():
    """命名千奇百怪也不猜（旧版正是在这些输入上误报）。

    `恶搞之家1-22季` / `Show.2023.1080p` / `Show.S12` 都曾被解析成集号并
    算出荒唐缺口；现在这些输入只应原样出现在 files 里。
    """
    for name in ("恶搞之家1-22季", "Show.2023.1080p", "Show.S12", "[高清]某剧 全1-12集 1080P"):
        r = normalize_share_detail(_raw([_f(name, cat="video")]))
        assert "episodes" not in r
        assert [f["name"] for f in r["files"]] == [name]


def test_size_is_normalized_to_int():
    raw = _raw([{"file_name": "a.mp4", "dir": False, "size": "2048"},
                {"file_name": "b.mp4", "dir": False, "size": "oops"},
                {"file_name": "c.mp4", "dir": False}])
    r = normalize_share_detail(raw)
    sizes = [f["size"] for f in r["files"]]
    assert sizes == [2048, 0, 0], "体积归一化错（字符串/缺失未兜底）"

# ------------------------------------------------ preview_share

class _FakeQAS:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def get_share_detail(self, url):
        self.calls.append(url)
        return self.resp


def test_preview_share_caches_success():
    fake = _FakeQAS(_raw([_f("01.mp4", cat="video")]))
    share_preview._preview_cache.clear()
    with patch("transfer._get_qas_client", lambda: fake):
        r1 = preview_share("https://pan.quark.cn/s/abc")
        r2 = preview_share("https://pan.quark.cn/s/abc")
    assert r1["ok"] and r2["ok"]
    assert len(fake.calls) == 1, "同链接重复展开应命中缓存，不再打 QAS"


def test_preview_share_does_not_cache_failure():
    """失败多半是 QAS 抖动；缓存失败会让「重试」也拿到同一个失败。"""
    share_preview._preview_cache.clear()
    fake = _FakeQAS({"success": False, "message": "QAS 挂了"})
    with patch("transfer._get_qas_client", lambda: fake):
        preview_share("https://pan.quark.cn/s/xyz")
        preview_share("https://pan.quark.cn/s/xyz")
    assert len(fake.calls) == 2, "失败被缓存了，重试无效"


def test_preview_share_swallows_upstream_exception():
    share_preview._preview_cache.clear()

    def boom():
        raise RuntimeError("QAS 不可达")

    with patch("transfer._get_qas_client", boom):
        r = preview_share("https://pan.quark.cn/s/err")
    assert r["ok"] is False and "QAS 不可达" in r["message"], "上游异常必须归一化，不能抛给路由"


def test_preview_share_rejects_empty_url():
    r = preview_share("   ")
    assert r["ok"] is False and r["files"] == []


# ------------------------------------------------ 路由层契约

class TestShareDetailRoute:
    """/api/share_detail 的响应契约（TransferRouteMixin + fake _send_json）。"""

    def _make_handler(self):
        import routes_transfer

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

    def test_returns_normalized_files(self):
        fake = _FakeQAS(_raw([_f("01.mp4", cat="video"), _f("02.mp4", cat="video")]))
        share_preview._preview_cache.clear()
        with patch("transfer._get_qas_client", lambda: fake):
            h = self._make_handler()
            h._params = {"url": "https://pan.quark.cn/s/route1"}
            h._handle_transfer_get("/api/share_detail")
        assert h.last_code == 200
        assert h.last["ok"] is True
        assert h.last["video_count"] == 2
        assert [f["name"] for f in h.last["files"]] == ["01.mp4", "02.mp4"]

    def test_missing_url_is_400(self):
        h = self._make_handler()
        h._params = {"url": ""}
        h._handle_transfer_get("/api/share_detail")
        assert h.last_code == 400
        assert h.last["ok"] is False

    def test_upstream_exception_does_not_500(self):
        """QAS 抖动时路由必须照常 200 + ok:false —— 搜索下拉的展开不该炸出 500。

        失败在 preview_share 内部就已归一化（带真实原因），路由的 500 分支只是
        兜底（防归一化本身出问题），正常路径不应走到那里。
        """
        share_preview._preview_cache.clear()

        def boom():
            raise RuntimeError("QAS 不可达")

        with patch("transfer._get_qas_client", boom):
            h = self._make_handler()
            h._params = {"url": "https://pan.quark.cn/s/route-err"}
            h._handle_transfer_get("/api/share_detail")
        assert h.last_code == 200
        assert h.last["ok"] is False
        assert "QAS 不可达" in h.last["message"], "失败原因未透传，用户只能看到「空的」"


# ------------------------------------------------ 子目录浏览（issue #88 四次反馈）
# 用户第四次反馈原文：「还是看不到第二层目录的文件名称。为什么」。
# 根因：盘搜结果的根目录里常常只有一个文件夹（如 `恶搞之家/`），真正的视频文件
# 在那个文件夹里；QAS /get_share_detail 只列**当前层**，所以用户只看到一行目录名。
# 修法：目录行可点击 → 带 dir_fid 请求 → 后端把分享链接拼成 `<url>/<fid>-<名称>`，
# QAS 的 extract_url 会把它解析成 pdir_fid，从而列出子目录。

class _FakeDirQAS:
    """按请求 URL 的路径层级返回不同的清单，模拟 QAS 的 pdir_fid 行为。"""

    def __init__(self):
        self.calls = []

    def get_share_detail(self, url):
        import re
        self.calls.append(url)
        # URL 里带 32 位 fid 段 => 子目录；否则根目录
        m = re.search(r"/([A-Za-z0-9]{32})(?:-|$)", url)
        if m:
            return _raw([_f("第01集.mkv", cat="video"), _f("第02集.mkv", cat="video")],
                        title="恶搞之家")
        return _raw([_f("恶搞之家", dir=True)], title="恶搞之家")


def test_build_dir_url_appends_fid_segment():
    fid = "a" * 32
    u = share_preview.build_dir_url("https://pan.quark.cn/s/abc123def456", fid, "恶搞之家")
    assert u == "https://pan.quark.cn/s/abc123def456/" + fid + "-恶搞之家", u
    # QAS extract_url 能从中解析出 pdir_fid
    import re
    matches = re.findall(r"/(\w{32})-?([^/]+)?", u)
    assert matches and matches[-1][0] == fid, "拼出的 URL 不能被 QAS extract_url 识别"


def test_build_dir_url_rejects_bad_fid():
    """非法 fid 不拼链接 —— 拼出坏链接会变成「点了没反应」。"""
    u = "https://pan.quark.cn/s/abc123def456"
    assert share_preview.build_dir_url(u, "short", "x") == u
    assert share_preview.build_dir_url(u, "", "x") == u
    assert share_preview.build_dir_url(u, "z" * 31, "x") == u


def test_build_dir_url_is_idempotent():
    """同名目录再次进入时不应把 fid 段叠两遍。"""
    fid = "b" * 32
    u = share_preview.build_dir_url("https://pan.quark.cn/s/abc123def456", fid, "剧")
    assert share_preview.build_dir_url(u, fid, "剧") == u


def test_preview_share_with_dir_fid_lists_subdir():
    """带 dir_fid 时必须请求「子目录链接」，并列出那一层的文件。"""
    share_preview._preview_cache.clear()
    fake = _FakeDirQAS()
    fid = "a" * 32
    with patch("transfer._get_qas_client", lambda: fake):
        root = preview_share("https://pan.quark.cn/s/abc123def456")
        sub = preview_share("https://pan.quark.cn/s/abc123def456", dir_fid=fid, dir_name="恶搞之家")
    assert root["dir_count"] == 1 and root["video_count"] == 0, "根目录清单错了"
    assert root["files"][0]["fid"], "目录条目必须带上 fid，否则前端无法进入"
    assert sub["video_count"] == 2, "进入子目录后应列出里面的视频文件"
    assert [f["name"] for f in sub["files"]] == ["第01集.mkv", "第02集.mkv"]
    assert any(fid in c for c in fake.calls), "子目录请求未带上 fid"


def test_preview_share_dir_fid_builds_breadcrumb():
    """QAS 没给面包屑时，至少补一层 —— 否则前端无法显示「回上级」。"""
    share_preview._preview_cache.clear()
    fake = _FakeDirQAS()
    fid = "a" * 32
    with patch("transfer._get_qas_client", lambda: fake):
        sub = preview_share("https://pan.quark.cn/s/abc123def456", dir_fid=fid, dir_name="恶搞之家")
    assert sub["path"] == [{"fid": fid, "name": "恶搞之家"}], sub["path"]


def test_preview_share_root_and_subdir_cached_separately():
    """根目录与子目录各用独立缓存键 —— 否则进子目录会拿到根目录的旧清单。"""
    share_preview._preview_cache.clear()
    fake = _FakeDirQAS()
    fid = "a" * 32
    with patch("transfer._get_qas_client", lambda: fake):
        preview_share("https://pan.quark.cn/s/abc123def456")
        preview_share("https://pan.quark.cn/s/abc123def456", dir_fid=fid, dir_name="恶搞之家")
        n = len(fake.calls)
        preview_share("https://pan.quark.cn/s/abc123def456")
        preview_share("https://pan.quark.cn/s/abc123def456", dir_fid=fid, dir_name="恶搞之家")
    assert len(fake.calls) == n, "二次请求未命中各自的缓存（根/子目录串味）"


def test_normalize_exposes_stoken_and_path():
    """stoken 与 path 必须透出：前端进目录 / 回上级都靠它们。"""
    raw = {"success": True, "data": {
        "share": {"title": "x"},
        "list": [_f("a.mp4", cat="video")],
        "stoken": "tok-123",
        "paths": [{"fid": "f" * 32, "name": "恶搞之家"}],
    }}
    r = normalize_share_detail(raw)
    assert r["stoken"] == "tok-123"
    assert r["path"] == [{"fid": "f" * 32, "name": "恶搞之家"}]


def test_route_forwards_dir_params():
    """/api/share_detail 必须把 dir_fid / dir_name 传给 preview_share。"""
    share_preview._preview_cache.clear()
    fake = _FakeDirQAS()

    import routes_transfer

    class H(routes_transfer.TransferRouteMixin):
        def _get_query_params(self):
            return self._params

        def _send_json(self, obj, code=200):
            self.last_code = code
            self.last = obj

    fid = "a" * 32
    with patch("transfer._get_qas_client", lambda: fake):
        h = H()
        h._params = {"url": "https://pan.quark.cn/s/abc123def456",
                     "dir_fid": fid, "dir_name": "恶搞之家"}
        h._handle_transfer_get("/api/share_detail")
    assert h.last_code == 200
    assert h.last["video_count"] == 2, "路由未把 dir_fid 转给 preview_share"
    assert any(fid in c for c in fake.calls)
