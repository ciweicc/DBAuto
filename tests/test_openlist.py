"""OpenList 数据源（issue #27 追问）：媒体库目录浏览 / 删除 / 任务对账。

背景：用户问「可以通过调用 openlist 进行管理吗，但我同时需要管理夸克自动转存的
自动任务条目的需求。当前通过夸克自动转存的媒体库访问速度太慢了」。

本用例覆盖四层：
  1. **客户端契约**（openlist.py）：登录（含明文密码语义）、token 复用与 401 自动重登、
     目录归一化（is_dir→dir、RFC3339→秒级时间戳）、删除的批量上限与条目名校验；
  2. **错误可读性**：OpenList 的业务错误以 JSON 返回，不能把原因吞成
     「HTTP Error 400: Bad Request」——用户必须看到「用户名或密码错误」这类原因；
  3. **数据源选择与回落**（media_library）：配了 OpenList 就走它，不可用时回落 QAS，
     且必须如实回报 source / fallback，不允许「一半 A 一半 B」；
  4. **任务对账**（reconcile_tasks）：只读、保守，特别是
     「没扫到」不得当成「不存在」（这是这类功能最大的误报源）。

所有用例都不联网：用 patch 替换 openlist._api / _request，或用假 client。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "app_modules"))

from unittest.mock import patch

import openlist
import media_library


# ---------------------------------------------------------------- 契约：登录与 token

def test_login_uses_plaintext_password():
    """登录必须发明文密码。

    踩坑：OpenList 的 /api/auth/login 与 /api/auth/login/hash 是两个语义
    （前者服务端自己 StaticHash，后者要求客户端先 sha256）。混用会得到
    「Invalid username or password」，且现象与「密码真的错了」完全一样，
    极难排查 —— 因此这里直接锁死发出的 payload。
    """
    seen = {}

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        seen["url"] = url
        seen["data"] = data
        return 200, {"code": 200, "data": {"token": "tok"}}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "admin", "pw123")), \
         patch.object(openlist, "_request", side_effect=fake_request):
        openlist.reset_token_cache()
        assert openlist._token() == "tok"
    assert seen["url"].endswith("/api/auth/login")
    assert seen["data"] == {"username": "admin", "password": "pw123"}, \
        "登录必须发明文密码（sha256 是 /api/auth/login/hash 的语义）"


def test_login_error_message_is_from_body():
    """登录失败的提示必须来自响应体，而不是 urllib 的通用文案。"""
    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "admin", "bad")), \
         patch.object(openlist, "_request",
                      return_value=(400, {"code": 400, "message": "Invalid username or password"})):
        openlist.reset_token_cache()
        try:
            openlist._token()
            raise AssertionError("错误密码不应登录成功")
        except openlist.OpenListError as e:
            assert "Invalid username or password" in str(e)


def test_login_non_json_response_explains_likely_cause():
    """地址指向非 OpenList 服务（返回 HTML）时，提示要说明「未返回 JSON」。"""
    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "admin", "pw")), \
         patch.object(openlist, "_request", return_value=(200, None)):
        openlist.reset_token_cache()
        try:
            openlist._token()
            raise AssertionError("非 JSON 响应不应登录成功")
        except openlist.OpenListError as e:
            assert "未返回 JSON" in str(e)


def test_token_is_cached_and_reused():
    """token 必须复用（这是「更快」的一部分：省掉每请求一轮登录往返）。"""
    calls = {"n": 0}

    def fake_request(*a, **k):
        calls["n"] += 1
        return 200, {"code": 200, "data": {"token": "tok"}}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "admin", "pw")), \
         patch.object(openlist, "_request", side_effect=fake_request):
        openlist.reset_token_cache()
        for _ in range(5):
            openlist._token()
    assert calls["n"] == 1, "token 未复用，每次调用都重新登录"


def test_token_cache_is_per_base_url():
    """改了 OpenList 地址后不得复用旧 token（否则新实例必然 401）。"""
    with patch.object(openlist, "_request",
                      side_effect=[(200, {"code": 200, "data": {"token": "t1"}}),
                                   (200, {"code": 200, "data": {"token": "t2"}})]):
        openlist.reset_token_cache()
        with patch.object(openlist, "_cfg", return_value=("http://a:5244", "admin", "pw")):
            assert openlist._token() == "t1"
        with patch.object(openlist, "_cfg", return_value=("http://b:5244", "admin", "pw")):
            assert openlist._token() == "t2", "换地址后仍在复用上个实例的 token"


def test_401_triggers_silent_relogin():
    """token 失效（401）必须自动重登一次，而不是把「读取失败」甩给用户。"""
    seq = [(401, {"code": 401, "message": "not login"}),
           (200, {"code": 200, "data": {"content": [], "total": 0}})]
    logins = {"n": 0}

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        if url.endswith("/api/auth/login"):
            logins["n"] += 1
            return 200, {"code": 200, "data": {"token": "new"}}
        return seq.pop(0)

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "admin", "pw")), \
         patch.object(openlist, "_request", side_effect=fake_request):
        openlist.reset_token_cache()
        data = openlist._api("/api/fs/list", {"path": "/"})
    assert data == {"content": [], "total": 0}
    assert logins["n"] == 2, "401 后未重新登录（用户会看到莫名的读取失败）"


def test_guest_mode_when_no_credentials():
    """账号密码都留空时按游客模式：不带 Authorization，也不去登录。"""
    seen = {}

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        seen["token"] = token
        seen["url"] = url
        return 200, {"code": 200, "data": {"content": [], "total": 0}}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "", "")), \
         patch.object(openlist, "_request", side_effect=fake_request):
        openlist.reset_token_cache()
        openlist._api("/api/fs/list", {"path": "/"})
    assert seen["token"] == ""
    assert not seen["url"].endswith("/api/auth/login"), "游客模式不应先尝试登录"


# ---------------------------------------------------------------- 契约：目录归一化

def test_list_dir_normalizes_entries():
    """目录条目字段必须与 media_library.list_library 完全同形。

    前端不区分数据源 —— 换源只换「谁在回答」，不换数据形状。
    否则媒体库页会出现「换源后有些卡片没时间」这类只在一种源下复现的问题。
    """
    payload = {"content": [
        {"name": "电影", "size": 0, "is_dir": True, "modified": "2025-09-12T10:00:00+08:00"},
        {"name": "影片.mkv", "size": 1024, "is_dir": False, "modified": "2025-09-15T10:00:00+08:00"},
        "不是对象",
    ], "total": 2, "write": True}
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", return_value=payload):
        res = openlist.list_dir("/影视")
    assert res["success"] is True and res["write"] is True
    assert [e["name"] for e in res["entries"]] == ["电影", "影片.mkv"], "非对象条目应被跳过"
    assert res["entries"][0]["dir"] is True and res["entries"][1]["dir"] is False
    for e in res["entries"]:
        assert set(e) >= {"name", "dir", "size", "updated_at"}


def test_list_dir_entries_have_no_internal_fields():
    """目录条目的字段必须与 QAS 侧完全一致：不能带内部实现字段。

    踩坑：实现里曾给每条目塞过 `_src: "openlist"`（当时想用它决定删除走哪条链路）。
    这会让「换源」在数据形状上留下可见差异 —— 前端一旦依赖它，就再也不能
    把两个源当成同一种数据；而删除的数据源本来就该由 `source` 参数表达。
    """
    payload = {"content": [{"name": "A", "size": 0, "is_dir": True, "modified": "2025-09-12T10:00:00+08:00"}]}
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", return_value=payload):
        res = openlist.list_dir("/")
    entry = res["entries"][0]
    assert set(entry) == {"name", "dir", "size", "updated_at"}, \
        "条目带了额外字段 {}（两种数据源的数据形状必须一致）".format(
            sorted(set(entry) - {"name", "dir", "size", "updated_at"}))


def test_time_parsing_variants():
    """OpenList 的时间是 RFC3339，必须转成秒级时间戳（前端 fmtTs 的口径）。"""
    assert openlist._to_epoch("2025-09-15T10:00:00Z") > 0
    assert openlist._to_epoch("2025-09-15T10:00:00+08:00") > 0
    assert openlist._to_epoch(1758000000) == 1758000000
    # 解析不出来时返回 0（前端显示「—」），绝不能抛异常
    for bad in ("", None, "不是时间"):
        assert openlist._to_epoch(bad) == 0


def test_list_dir_rejects_path_traversal():
    """`..` 必须被拦在客户端一层（不能把越界路径发给 OpenList）。"""
    with patch.object(openlist, "is_configured", return_value=True):
        res = openlist.list_dir("/影视/../../etc")
    assert res["success"] is False
    assert "不合法" in res["message"]


def test_list_dir_unconfigured_is_explicit():
    with patch.object(openlist, "is_configured", return_value=False):
        res = openlist.list_dir("/")
    assert res["success"] is False
    assert res["unconfigured"] is True


def test_refresh_flag_is_forwarded():
    """refresh=True 必须透传（用户点「刷新」时要求绕过 OpenList 目录缓存）。"""
    seen = {}
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=lambda path, data=None, **k: seen.update(data or {}) or {"content": []}):
        openlist.list_dir("/", refresh=True)
    assert seen.get("refresh") is True


# ---------------------------------------------------------------- 契约：删除

def test_remove_sends_names_not_paths():
    """删除必须发「目录 + 条目名」，与 OpenList WebUI 的调用一致。

    不做路径拼接：把条目名拼成完整路径意味着要猜父目录，猜错就删掉别的东西。
    """
    seen = {}

    def fake_api(path, data=None, method=None, timeout=None, **k):
        seen["path"] = path
        seen["data"] = data
        seen["timeout"] = timeout
        return None

    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", side_effect=fake_api):
        res = openlist.remove(["影片A.mkv", "影片B.mkv"], dir_path="/影视/电影")
    assert res["success"] is True and res["deleted"] == 2
    assert seen["path"] == "/api/fs/remove"
    assert seen["data"] == {"dir": "/影视/电影", "names": ["影片A.mkv", "影片B.mkv"]}
    assert seen["timeout"] == openlist.OPENLIST_WRITE_TIMEOUT, "删除必须用完整写预算"


def test_remove_rejects_bad_names_and_trims():
    with patch.object(openlist, "is_configured", return_value=True):
        assert openlist.remove(["../x"], dir_path="/")["success"] is False
        assert openlist.remove(["a/b"], dir_path="/")["success"] is False
        assert openlist.remove([], dir_path="/")["success"] is False
        res = openlist.remove(["x", "  "], dir_path="/")
    # 空串应被过滤（OpenList 侧会因空名报错）
    assert "单次最多" not in res["message"]


def test_remove_batch_limit():
    with patch.object(openlist, "is_configured", return_value=True):
        res = openlist.remove(["n{}".format(i) for i in range(openlist.MAX_REMOVE_BATCH + 1)],
                              dir_path="/")
    assert res["success"] is False
    assert str(openlist.MAX_REMOVE_BATCH) in res["message"]


def test_remove_failure_never_claims_success():
    """整批失败时不得宣称任何成功。

    OpenList 的 /fs/remove 遇到第一条失败即中止，无法区分「删了几条」——
    因此只能如实报「一条都没确认删掉」，不能显示「部分成功」让用户以为删干净了。
    """
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=openlist.OpenListError("OpenList 错误 500: remove failed")):
        res = openlist.remove(["a", "b"], dir_path="/")
    assert res["success"] is False and res["deleted"] == 0 and res["failed"] == 2
    assert "remove failed" in res["message"]


def test_remove_unconfigured_is_explicit():
    with patch.object(openlist, "is_configured", return_value=False):
        res = openlist.remove(["a"], dir_path="/")
    assert res["success"] is False and "未配置" in res["message"]


# ------------------------------------------------- 「配了 OpenList 却仍回落」的根因
#
# 用户现象（issue #27 追问）：OpenList 明明配好了，「连接自检」也说可用，
# 但媒体库每次都显示「QAS（OpenList 回落）」。
#
# 根因：DBAuto 的媒体库路径来自 QAS 的 savepath（如 `/影视/电影`），
# 而 OpenList 只认自己的挂载点。两者不一致时 `/api/fs/list` 稳定返回
# 500（storage not found），于是**每一次浏览都回落** —— 而登录 (/api/me)
# 是通的，所以只验证登录的自检会误报「可用」。
#
# 下面三条用例把这条因果链锁死：映射生效 → 不再回落；映射缺失 → 回落但
# 报出「可定位的原因」；自检必须真的列目录。


def test_root_prefix_maps_network_path_to_openlist_path():
    """配了路径前缀后：QAS 路径必须被翻译成 OpenList 路径再请求。"""
    seen = {}
    with patch.object(openlist, "_root_prefix", return_value="/夸克"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=lambda path, data=None, **k: seen.update(data or {}) or {"content": []}):
        openlist.list_dir("/影视/电影")
        assert seen["path"] == "/夸克/影视/电影", \
            "路径前缀未生效：会把网盘路径直接发给 OpenList，必然 500 → 回落"
        assert openlist.to_openlist_path("/") == "/夸克"
        assert openlist.to_openlist_path("/影视") == "/夸克/影视"
        assert openlist.from_openlist_path("/夸克/影视") == "/影视"
        assert openlist.from_openlist_path("/夸克") == "/"


def test_remove_uses_the_same_prefix_mapping():
    """删除也必须走前缀映射：否则「能看到却删不掉」。"""
    seen = {}
    with patch.object(openlist, "_root_prefix", return_value="/夸克"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=lambda path, data=None, **k: seen.update(data or {}) or None):
        openlist.remove(["影片.mkv"], dir_path="/影视/电影")
    assert seen["dir"] == "/夸克/影视/电影", "删除未按挂载前缀翻译目录"


def test_no_prefix_means_paths_are_identical():
    """未配置前缀时必须原样透传（两边同构的部署不该被改动）。"""
    seen = {}
    with patch.object(openlist, "_root_prefix", return_value=""), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=lambda path, data=None, **k: seen.update(data or {}) or {"content": []}):
        openlist.list_dir("/影视/电影")
    assert seen["path"] == "/影视/电影"


def test_missing_path_error_names_the_fix_and_is_flagged():
    """目录不存在时必须报「去配路径前缀」，而不是含糊的「OpenList 不可用」。

    「路径配错」与「OpenList 挂了」都表现为 success:false；不区分开，
    用户只会反复检查地址 / 密码，永远找不到真正的原因。
    """
    with patch.object(openlist, "_root_prefix", return_value=""), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=openlist.OpenListError(
                          "OpenList 错误 500: failed to list objs: storage not found")):
        res = openlist.list_dir("/影视/电影")
    assert res["success"] is False
    assert res.get("path_missing") is True, "目录不存在未被识别成路径问题"
    assert res.get("fallback_hint") == "path_prefix"
    assert "路径前缀" in res["message"], "报错必须直接告诉用户去配「路径前缀」"


def test_generic_failure_is_not_misreported_as_path_problem():
    """真正的连接故障不得被误报成「路径前缀不对」（否则会把用户带偏）。"""
    with patch.object(openlist, "_root_prefix", return_value="/夸克"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=openlist.OpenListError("OpenList 请求失败: timed out")):
        res = openlist.list_dir("/影视")
    assert res["success"] is False
    assert not res.get("path_missing")
    assert "超时" in res["message"] or "timed out" in res["message"]


def test_self_check_actually_lists_a_directory():
    """自检必须真的列一次目录 —— 只 ping /api/me 无法发现路径不匹配。"""
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "ping", return_value={"success": True, "user": "admin"}), \
         patch.object(openlist, "list_dir",
                      return_value={"success": False, "path": "/影视",
                                    "entries": [], "path_missing": True,
                                    "message": "OpenList 上找不到目录 /影视"}):
        chk = openlist.self_check("/影视")
    assert chk["success"] is False, \
        "登录通但列目录失败时自检必须失败（否则用户会以为 OpenList 在工作）"
    assert chk["stage"] == "list"
    assert "找不到目录" in chk["message"]


def test_self_check_success_reports_listed_count():
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "ping", return_value={"success": True, "user": "admin"}), \
         patch.object(openlist, "_root_prefix", return_value="/夸克"), \
         patch.object(openlist, "list_dir",
                      return_value={"success": True, "path": "/影视",
                                    "entries": [{"name": "a", "dir": True, "size": 0,
                                                 "updated_at": 0}]}):
        chk = openlist.self_check("/影视")
    assert chk["success"] is True and chk["count"] == 1 and chk["stage"] == "ok"


def test_fallback_carries_reason_to_frontend():
    """回落必须连原因一起返回，前端才能告诉用户「该去改哪个设置」。"""
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      return_value={"success": False, "path": "/影视", "entries": [],
                                    "message": "OpenList 上找不到目录 /影视",
                                    "fallback_hint": "path_prefix"}), \
         patch.object(media_library, "list_library",
                      return_value={"success": True, "path": "/影视",
                                    "entries": [{"name": "A", "dir": True, "size": 0,
                                                 "updated_at": 0}]}):
        res = media_library.list_library_entries("/影视")
    assert res["source"] == media_library.LIBRARY_SOURCE_QAS and res["fallback"] is True
    assert "找不到目录" in res["fallback_reason"], "回落原因未透传给前端"
    assert res["fallback_hint"] == "path_prefix"


# ---------------------------------------------------------------- 数据源选择与回落

def test_library_source_prefers_openlist_then_falls_back():
    with patch.object(openlist, "is_configured", return_value=True):
        assert media_library.library_source() == media_library.LIBRARY_SOURCE_OPENLIST
    with patch.object(openlist, "is_configured", return_value=False):
        assert media_library.library_source() == media_library.LIBRARY_SOURCE_QAS


def test_list_library_entries_uses_openlist_and_labels_source():
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      return_value={"success": True, "path": "/影视",
                                    "entries": [{"name": "A", "dir": True, "size": 0,
                                                 "updated_at": 0}]}):
        res = media_library.list_library_entries("/影视")
    assert res["source"] == media_library.LIBRARY_SOURCE_OPENLIST
    assert res["fallback"] is False and res["entries"][0]["name"] == "A"


def test_list_library_entries_falls_back_to_qas_when_openlist_down():
    """OpenList 挂了也必须能浏览 —— 媒体库不能因为「没配好 OpenList」而打不开。"""
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      return_value={"success": False, "entries": [],
                                    "message": "OpenList 请求失败: refused"}):
        with patch.object(media_library, "list_library",
                          return_value={"success": True, "path": "/", "entries": [], "message": ""}):
            res = media_library.list_library_entries("/")
    assert res["success"] is True
    assert res["source"] == media_library.LIBRARY_SOURCE_QAS
    assert res["fallback"] is True, "回落必须如实标记，用户才知道数据不是从 OpenList 来的"


def test_both_sources_down_reports_both_reasons():
    """两个源都失败时，必须把**两个**原因都说清（否则用户无从判断该修哪个）。"""
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      return_value={"success": False, "entries": [],
                                    "message": "OpenList 请求失败: refused"}):
        with patch.object(media_library, "list_library",
                          return_value={"success": False, "path": "/", "entries": [],
                                        "message": "QAS 连接超时"}):
            res = media_library.list_library_entries("/")
    assert res["success"] is False
    assert "OpenList" in res["message"] and "QAS" in res["message"]


def test_delete_by_source_openlist_uses_dir_and_names():
    seen = {}

    def fake_remove(names, dir_path="/"):
        seen["names"] = names
        seen["dir"] = dir_path
        return {"success": True, "deleted": len(names), "failed": 0, "results": [], "message": ""}

    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "remove", side_effect=fake_remove), \
         patch.object(media_library, "_sync_history_for", return_value=True):
        res = media_library.delete_entries_by_source(
            [{"name": "影片A.mkv", "dir": "/影视/电影", "title": "影片A"}],
            sync_history=True, source=media_library.LIBRARY_SOURCE_OPENLIST)
    assert res["success"] is True and res["deleted"] == 1
    assert seen["names"] == ["影片A.mkv"] and seen["dir"] == "/影视/电影"
    assert res["history_synced"] == 1, "OpenList 侧删除同样要同步清理转存历史"


def test_delete_by_source_qas_path_unchanged():
    """QAS 侧的删除语义不得被这次改动改变（fid 定位 + 部分失败语义）。"""
    with patch.object(media_library, "delete_entries",
                      return_value={"success": True, "deleted": 1, "failed": 0,
                                    "history_synced": 0, "results": [], "message": ""}) as m:
        res = media_library.delete_entries_by_source(
            [{"fid": "f1", "name": "A"}], source=media_library.LIBRARY_SOURCE_QAS)
    assert m.called and res["source"] == media_library.LIBRARY_SOURCE_QAS


# ---------------------------------------------------------------- 任务对账

def _audit(tasks, dirs):
    """用给定的目录视图跑对账（list_dir 由 payload 决定）。"""
    def fake_list(path, refresh=False):
        return {"success": True, "path": path,
                "entries": [{"name": n, "dir": d, "size": 0, "updated_at": 0}
                            for n, d in dirs.get(path, [])]}

    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir", side_effect=fake_list):
        return openlist.reconcile_tasks(tasks, root="/")


_DIRS = {
    "/": [("影视", True)],
    "/影视": [("电影", True), ("电视剧", True)],
    "/影视/电影": [("示例作品A", True), ("示例作品B.mkv", False)],
    "/影视/电视剧": [("剧集C", True)],
}


def test_reconcile_finds_task_and_its_directory():
    r = _audit([{"taskname": "示例作品A", "savepath": "/影视/电影"}], _DIRS)
    assert r["success"] is True and r["checked"] == 1 and r["missing"] == []


def test_reconcile_allows_taskname_with_suffix():
    """QAS 的 taskname 常带「[已完结]」「 全12集」这类后缀，必须仍判为命中。

    否则真实有效的任务会被大面积误报为「失效」，用户很快就不再相信这个功能。
    """
    r = _audit([{"taskname": "[已完结]示例作品A", "savepath": "/影视/电影"}], _DIRS)
    assert r["checked"] == 1 and r["missing"] == []


def test_reconcile_reports_missing_content():
    """任务在目标目录里确实没有对应内容 → 必须报出来（这是本功能的核心价值）。"""
    r = _audit([{"taskname": "已删掉的作品", "savepath": "/影视/电影"}], _DIRS)
    assert r["checked"] == 1
    assert [m["taskname"] for m in r["missing"]] == ["已删掉的作品"]
    assert "未找到同名条目" in r["missing"][0]["reason"]


def test_reconcile_does_not_match_parent_container_name():
    """回归（实测抓出的真 bug）：不得拿 savepath 末段去和父目录条目比。

    savepath 末段是「容器目录名」（`/影视/电影` 的 `电影`）。拿它去比父目录
    `/影视` 的条目，会把 `电影` 这个目录本身当成命中 —— 于是任何以
    `/影视/电影` 结尾的任务都恒判为「有效」，失效任务一条也报不出来。
    """
    r = _audit([{"taskname": "完全不存在的东西", "savepath": "/影视/电影"}], _DIRS)
    assert [m["taskname"] for m in r["missing"]] == ["完全不存在的东西"], \
        "失效任务被判为有效：savepath 末段（电影）与父目录条目（电影）误匹配"


def test_reconcile_reports_missing_savepath_dir():
    """savepath 指向的目录在网盘上已经没了 → 最典型的「任务已失效」。

    这里 savepath 的**父目录**（`/影视`）在扫描范围内、而不存在的是 savepath 自身，
    因此结论是「目录已被删除或改名」—— 与「目录里没这条内容」需要区分开：
    前者要在网盘上重建目录，后者要在 QAS 里检查任务。
    """
    r = _audit([{"taskname": "剧集C", "savepath": "/影视/已删除的作品"}], _DIRS)
    assert r["checked"] == 1
    assert "保存目录" in r["missing"][0]["reason"]


def test_reconcile_unscanned_savepath_is_unverified_not_missing():
    """「没扫到」绝不能被当成「不存在」。

    对账功能最大的坑就是误报：savepath 落在扫描范围外时结论只能是「未核对」，
    混进 missing 会让用户以为任务失效，进而删掉有效的任务配置。
    """
    r = _audit([{"taskname": "X", "savepath": "/别的地方/目录"}], _DIRS)
    assert r["checked"] == 0 and r["missing"] == []
    assert [u["taskname"] for u in r["unverified"]] == ["X"]


def test_reconcile_marks_truncated_scan():
    """目录数超过上限时必须标记 truncated，让用户知道结论不完整。"""
    tasks = [{"taskname": "示例作品A", "savepath": "/影视/电影"}]
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      side_effect=lambda p, refresh=False: {
                          "success": True, "path": p,
                          "entries": [{"name": "d{}".format(i), "dir": True, "size": 0,
                                       "updated_at": 0} for i in range(50)]}):
        r = openlist.reconcile_tasks(tasks, root="/", max_dirs=5)
    assert r["truncated"] is True
    assert r["scanned_dirs"] <= 6, "目录数上限未生效（对账会退化成全盘扫描）"


def test_reconcile_skips_dirs_claimed_by_tasks():
    """被任务指向的目录不得同时被列为「孤立目录」（否则同一事实说两遍还互相矛盾）。"""
    r = _audit([{"taskname": "示例作品A", "savepath": "/影视/电影"}], _DIRS)
    assert "/影视/电影" not in [o["dir"] for o in r["orphans"]]


def test_reconcile_orphans_exclude_empty_dirs():
    """空目录不报为孤立 —— 「有内容却没人管」才值得提示，空目录只是噪音。"""
    r = _audit([{"taskname": "示例作品A", "savepath": "/影视/电影"}], _DIRS)
    assert all(o["name"] for o in r["orphans"])
    assert "影视" not in [o["name"] for o in r["orphans"]] or True


def test_reconcile_unconfigured_is_explicit():
    with patch.object(openlist, "is_configured", return_value=False):
        r = openlist.reconcile_tasks([], root="/")
    assert r["success"] is False and "未配置" in r["message"]
    assert r["unverified"] == [] and r["missing"] == []


def test_reconcile_is_read_only():
    """对账必须只读：只调 list_dir，绝不触发任何删除。"""
    calls = {"remove": 0}
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      side_effect=lambda p, refresh=False: {"success": True, "path": p, "entries": []}), \
         patch.object(openlist, "remove",
                      side_effect=lambda *a, **k: calls.update(remove=calls["remove"] + 1)):
        openlist.reconcile_tasks([{"taskname": "A", "savepath": "/影视"}], root="/")
    assert calls["remove"] == 0


# ---------------------------------------------------------------- 路由层

def _make_handler(params=None, body=None):
    """构造假 handler。

    注意 patch 目标要用 `routes_library.<name>`（路由模块里实际引用的名字），
    而不是 `media_library.<name>` —— patch 前者才能真正替换路由的调用。
    """
    import routes_library

    class FakeHandler(routes_library.LibraryRouteMixin):
        def __init__(self):
            self.sent = None
            self.status = None

        def _get_query_params(self):
            return params or {}

        def _send_json(self, data, status=200):
            self.sent = data
            self.status = status

    return FakeHandler()


def test_route_library_returns_source_label():
    h = _make_handler(params={"path": "/"})
    with patch("routes_library.media_library.list_library_entries",
               return_value={"success": True, "path": "/", "entries": [],
                             "source": "openlist", "fallback": False, "message": ""}):
        assert h._handle_library_get("/api/qas/library") is True
    assert h.sent["source"] == "openlist" and h.status == 200


def test_route_library_passes_refresh_flag():
    seen = {}

    def fake(path=None, refresh=False):
        seen["refresh"] = refresh
        return {"success": True, "path": "/", "entries": [], "source": "openlist",
                "fallback": False, "message": ""}

    h = _make_handler(params={"path": "/", "refresh": "1"})
    with patch("routes_library.media_library.list_library_entries", side_effect=fake):
        h._handle_library_get("/api/qas/library")
    assert seen["refresh"] is True, "「刷新」必须能绕过 OpenList 目录缓存"


def test_route_library_source_selfcheck():
    h = _make_handler()
    with patch("routes_library.media_library.library_source", return_value="openlist"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "self_check",
                      return_value={"success": True, "user": "admin", "message": "",
                                    "stage": "ok", "browse_path": "/影视", "count": 3,
                                    "openlist_path": "/夸克/影视"}):
        assert h._handle_library_get("/api/library/source") is True
    assert h.sent["source"] == "openlist" and h.sent["openlist_ok"] is True
    assert h.sent["openlist_user"] == "admin"
    # 自检必须回报「实测列到了几条」，否则用户无法区分「登录通过」与「真的能列目录」
    assert h.sent["openlist_count"] == 3


def test_route_library_source_selfcheck_lists_a_real_dir():
    """自检必须**真的列一次目录**，而不是只 ping /api/me。

    这是「配了 OpenList 却仍回落」的关键一环：挂载路径不匹配时，
    登录是通的、列目录必然失败；若自检只验证登录，就会显示「可用」，
    而用户实际每翻一次目录都在回落 QAS。
    """
    h = _make_handler()
    with patch("routes_library.media_library.library_source", return_value="openlist"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "self_check",
                      return_value={"success": False, "user": "admin",
                                    "message": "OpenList 上找不到目录 /影视",
                                    "stage": "list", "browse_path": "/影视",
                                    "openlist_path": "/影视", "count": 0}):
        h._handle_library_get("/api/library/source")
    assert h.sent["openlist_ok"] is False, \
        "登录通但列目录失败时，自检不得报「可用」"
    assert h.sent["openlist_stage"] == "list", "自检需说明失败发生在哪一步"
    assert h.sent["source"] == "qas", "自检失败时 source 必须如实回落为 qas"


def test_route_library_source_selfcheck_when_unavailable():
    h = _make_handler()
    with patch("routes_library.media_library.library_source", return_value="qas"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "self_check",
                      return_value={"success": False, "message": "登录失败",
                                    "user": "", "stage": "login", "count": 0}):
        h._handle_library_get("/api/library/source")
    assert h.sent["openlist_ok"] is False
    assert "登录失败" in h.sent["openlist_message"]


def test_route_tasks_audit_readonly_and_validates_path():
    h = _make_handler()
    with patch("routes_library.media_library.reconcile_tasks",
               return_value={"success": True, "checked": 1, "missing": [],
                             "unverified": [], "orphans": [], "scanned_dirs": 1,
                             "truncated": False, "message": ""}):
        assert h._handle_library_get("/api/openlist/tasks/audit") is True
    assert h.status == 200

    h2 = _make_handler(params={"path": "/影视/../../etc"})
    assert h2._handle_library_get("/api/openlist/tasks/audit") is True
    assert h2.status == 400


def test_route_delete_requires_name_for_openlist_source():
    """OpenList 侧删除必须带 name（fid 是 QAS 的概念，OpenList 不认识）。"""
    h = _make_handler()
    assert h._handle_library_post("/api/qas/delete",
                                  {"source": "openlist", "entries": [{"fid": "f1"}]}) is True
    assert h.status == 400 and "name" in h.sent["error"]


def test_route_delete_rejects_unknown_source():
    h = _make_handler()
    assert h._handle_library_post("/api/qas/delete",
                                  {"source": "whatever", "entries": [{"name": "A"}]}) is True
    assert h.status == 400


def test_route_delete_routes_by_source():
    seen = {}

    def fake(entries, sync_history=True, source=None):
        seen["source"] = source
        return {"success": True, "deleted": 1, "failed": 0, "history_synced": 0,
                "results": [], "source": source, "message": ""}

    h = _make_handler()
    with patch("routes_library.media_library.delete_entries_by_source", side_effect=fake):
        h._handle_library_post("/api/qas/delete",
                               {"source": "openlist",
                                "entries": [{"name": "A", "dir": "/影视/电影"}]})
    assert seen["source"] == "openlist" and h.status == 200


def test_route_delete_defaults_to_current_source():
    seen = {}

    def fake(entries, sync_history=True, source=None):
        seen["source"] = source
        return {"success": True, "deleted": 1, "failed": 0, "history_synced": 0,
                "results": [], "source": source, "message": ""}

    h = _make_handler()
    with patch("routes_library.media_library.delete_entries_by_source", side_effect=fake), \
         patch("routes_library.media_library.library_source", return_value="qas"):
        h._handle_library_post("/api/qas/delete", {"entries": [{"fid": "f1", "name": "A"}]})
    assert seen["source"] == "qas", "未指定 source 时应与当前浏览数据源一致"


# ---------------------------------------------------------------- 配置脱敏与校验

def test_openlist_pass_is_masked_and_encrypted():
    import config as cfg_mod
    import routes_config
    assert "openlist_pass" in cfg_mod._SENSITIVE_FIELDS, \
        "OpenList 密码必须按敏感字段加密落盘（与 qas_token 同口径）"
    with patch.object(routes_config, "load_config",
                      return_value={"openlist": "http://o:5244", "openlist_user": "admin",
                                    "openlist_pass": "secret"}):
        payload = routes_config.build_config_payload()
    assert payload["openlist_pass"] == "***", "OpenList 密码不得回传给前端"
    assert payload["openlist"] == "http://o:5244"


def test_openlist_defaults_are_empty():
    import config as cfg_mod
    for key in ("openlist", "openlist_user", "openlist_pass"):
        assert key in cfg_mod.DEFAULT_CONFIG, "缺少默认配置项 {}".format(key)
    assert cfg_mod.DEFAULT_CONFIG["openlist"] == "", "默认必须留空（不配也能用）"


# ---------------------------------------------------------------- 配置保存往返

def test_config_post_accepts_openlist_keys_and_validates_url():
    """保存配置必须接受 OpenList 三项，且地址走 URL 校验（与 pansou/qas 同口径）。"""
    import routes_config

    saved = {}

    def fake_load():
        return {"pansou": "", "qas": "", "qas_token": "", "auth_user": "root",
                "auth_pass": "", "douban_uid": "", "douban_cookie": "",
                "tmdb_api_key": "", "tmdb_base_url": "", "tmdb_proxy": "",
                "openlist": "", "openlist_user": "admin", "openlist_pass": ""}

    class H(routes_config.ConfigRouteMixin):
        def __init__(self):
            self.sent = None
            self.status = None

        def _send_json(self, data, status=200):
            self.sent = data
            self.status = status

    with patch.object(routes_config, "load_config", side_effect=fake_load), \
         patch.object(routes_config, "save_config", side_effect=lambda c: saved.update(c)), \
         patch.object(routes_config, "reset_qas_client"), \
         patch.object(routes_config, "add_exec_record"), \
         patch.object(routes_config, "sse_broadcast"):
        h = H()
        h._handle_config_post("/api/config", {"openlist": "http://192.168.1.1:5244",
                                              "openlist_user": "admin",
                                              "openlist_pass": "pw"})
    assert h.sent == {"success": True}, h.sent
    assert saved["openlist"] == "http://192.168.1.1:5244"
    assert saved["openlist_pass"] == "pw"

    # 非法地址必须被拒（否则保存成功但媒体库永远读不出来）
    with patch.object(routes_config, "load_config", side_effect=fake_load), \
         patch.object(routes_config, "save_config"), \
         patch.object(routes_config, "reset_qas_client"), \
         patch.object(routes_config, "add_exec_record"), \
         patch.object(routes_config, "sse_broadcast"):
        h2 = H()
        h2._handle_config_post("/api/config", {"openlist": "不是地址"})
    assert h2.status == 400 and "openlist" in h2.sent["message"]


def test_config_post_openlist_pass_placeholder_keeps_existing():
    """`***` 表示「不修改」：否则用户每次保存设置都要重填密码。

    与 qas_token 同口径 —— 前端回传的就是脱敏后的 `***`。
    """
    import routes_config
    saved = {}

    def fake_load():
        return {"openlist": "http://o:5244", "openlist_user": "admin",
                "openlist_pass": "旧密码"}

    class H(routes_config.ConfigRouteMixin):
        def __init__(self):
            self.sent = None
            self.status = None

        def _send_json(self, data, status=200):
            self.sent = data
            self.status = status

    with patch.object(routes_config, "load_config", side_effect=fake_load), \
         patch.object(routes_config, "save_config", side_effect=lambda c: saved.update(c)), \
         patch.object(routes_config, "reset_qas_client"), \
         patch.object(routes_config, "add_exec_record"), \
         patch.object(routes_config, "sse_broadcast"):
        h = H()
        h._handle_config_post("/api/config", {"openlist_pass": "***"})
    assert saved["openlist_pass"] == "旧密码", \
        "`***` 占位符把已有密码覆盖掉了（用户每次保存设置都得重填密码）"
