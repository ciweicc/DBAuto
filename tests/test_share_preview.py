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
