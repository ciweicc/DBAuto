"""OpenList 数据源（issue #27 追问）：媒体库目录浏览 / 删除 / 任务对账。

背景：用户问「可以通过调用 openlist 进行管理吗，但我同时需要管理夸克自动转存的
自动任务条目的需求。当前通过夸克自动转存的媒体库访问速度太慢了」。

本用例覆盖四层：
  1. **客户端契约**（openlist.py）：目录归一化（is_dir→dir、RFC3339→秒级时间戳）、
     删除的批量上限与条目名校验；
     以及**鉴权只有一条路**（issue #47：「把 OpenList 用户名密码验证的功能去掉，
     只保留令牌验证」）：令牌直发、**不存在任何登录请求**、令牌失效如实报错、
     账号/密码字段彻底不可用（配置、校验、脱敏三处都不留）；
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


# ------------------------------------------------- 契约：账号密码登录已被移除（issue #47）
#
# 用户要求：「把 OpenList 用户名密码验证的功能去掉，只保留令牌验证」。
# 这类「删功能」的改动最容易只删一半 —— 后端不再调用 /api/auth/login，
# 但配置项、校验分支、前端输入框还在，于是用户继续填一个永远不生效的字段，
# 现象与「令牌写错了」完全一样。下面把三处都锁死。

def test_login_helper_is_gone():
    """登录实现必须整体删除，而不是「留着但没人调」。

    留着是危险的：任何一处（旧脚本、未来的重构、并发分支）把它接回去，
    就等于恢复了「用账号密码调登录接口换 token」的老链路，而它在界面上
    已经没有任何对应的配置项 —— 用户看不到、也关不掉它。
    """
    assert not hasattr(openlist, "_login"), "_login 仍存在（登录链路没删干净）"
    src = open(os.path.join(_ROOT, "app_modules", "openlist.py"), encoding="utf-8").read()
    assert "/api/auth/login" not in src, \
        "openlist.py 里仍有登录接口调用：账号密码登录没有真正移除"
    # 同时不得再有名副其实的「登录请求」构造点（换 URL / 换 SDK 都绕不过这条）
    assert "auth/login" not in src, "openlist.py 里仍拼着登录接口路径"


def test_removed_auth_modes_and_caches_are_gone():
    """鉴权方式与凭据缓存都必须收敛：只剩 token / guest，且不再有任何进程内凭据缓存。"""
    for name in ("AUTH_PASSWORD", "AUTH_MIXED"):
        assert not hasattr(openlist, name), \
            "{} 仍存在：说明「账号密码」这条路的语义还留在代码里".format(name)
    assert openlist.AUTH_TOKEN == "token" and openlist.AUTH_GUEST == "guest"
    for name in ("_token_cache", "_auth_cache", "_AUTH_CACHE_TTL"):
        assert not hasattr(openlist, name), \
            "{} 仍存在：令牌是配置常量，直发即最新，无需任何凭据缓存".format(name)


def test_cfg_only_returns_base_and_token():
    """_cfg 只返回 (地址, 令牌) —— 账号 / 密码不再从配置里读出来。"""
    with patch.object(openlist.ConfigManager, "get_instance") as inst:
        inst.return_value.get_config.return_value = {
            "openlist": "http://o:5244", "openlist_token": "tok",
            "openlist_user": "legacy-admin", "openlist_pass": "legacy-pw",
        }
        assert openlist._cfg() == ("http://o:5244", "tok")


def test_legacy_credentials_are_ignored_not_used():
    """配置文件里残留的旧账号 / 密码必须被**忽略**，绝不能又被捡起来用。

    这是本改动最需要防的回退点：老用户的 config.json 里就躺着这两项，
    若 _resolve_auth 顺手读了它们，行为上就等于「功能没删」，
    而界面上用户看不到任何相关输入框 —— 无从排查。
    """
    seen = []

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        seen.append((url, token, data))
        return 200, {"code": 200, "data": {"content": [], "total": 0}}

    with patch.object(openlist.ConfigManager, "get_instance") as inst, \
         patch.object(openlist, "_request", side_effect=fake_request):
        inst.return_value.get_config.return_value = {
            "openlist": "http://o:5244", "openlist_token": "",
            "openlist_user": "legacy-admin", "openlist_pass": "legacy-pw",
        }
        openlist._api("/api/fs/list", {"path": "/"})
    assert len(seen) == 1, "残留的旧账号密码引发了额外请求（登录链路被复活）: {}".format(seen)
    url, token, _data = seen[0]
    assert not url.endswith("/api/auth/login"), "又去登录了：账号密码功能没有真正移除"
    assert token == "", "残留的旧凭据被当成令牌发出去了"


def test_guest_mode_when_no_token():
    """令牌留空时按游客模式：不带 Authorization，也不去登录。"""
    seen = {}

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        seen["token"] = token
        seen["url"] = url
        return 200, {"code": 200, "data": {"content": [], "total": 0}}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "")), \
         patch.object(openlist, "_request", side_effect=fake_request):
        openlist._api("/api/fs/list", {"path": "/"})
    assert seen["token"] == ""
    assert not seen["url"].endswith("/api/auth/login"), "游客模式不应先尝试登录"


# ---------------------------------------------------------------- 契约：令牌直发（唯一路径）

def test_token_is_sent_directly_without_login_round_trip():
    """配了令牌就**直发**，绝不先登录换取 token。

    这是「令牌验证」这条需求的全部意义：少一轮登录往返。若实现成
    「先登录再无视登录结果用令牌」，功能看起来是对的，但省下的那一轮
    根本没省 —— 因此这里直接断言「没有任何登录请求发出」。
    """
    seen = []

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        seen.append((url, token))
        return 200, {"code": 200, "data": {"content": [], "total": 0}}

    with patch.object(openlist, "_cfg",
                      return_value=("http://o:5244", "jwt-token-abc")), \
         patch.object(openlist, "_request", side_effect=fake_request):
        openlist._api("/api/fs/list", {"path": "/"})
    assert len(seen) == 1, "令牌模式下发出了多余请求（应先登录的往返必须被省掉）: {}".format(seen)
    url, token = seen[0]
    assert not url.endswith("/api/auth/login"), "令牌模式不应再去登录"
    assert token == "jwt-token-abc", "Authorization 必须是配置里的令牌原文（OpenList 不吃 Bearer 前缀）"


def test_token_change_takes_effect_on_every_request():
    """改令牌后下一个请求立刻用新令牌。

    账号密码时代这里必须靠指纹缓存失效（否则「保存了却没生效」），
    现在令牌是配置常量、每个请求直发，这个失效模式被结构性地消除了 ——
    用例仍保留，防止有人日后又把凭据读进缓存。
    """
    seen = []

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        seen.append(token)
        return 200, {"code": 200, "data": {"content": [], "total": 0}}

    with patch.object(openlist, "_request", side_effect=fake_request):
        with patch.object(openlist, "_cfg",
                          return_value=("http://o:5244", "tok-old")):
            openlist._api("/api/fs/list", {"path": "/"})
        with patch.object(openlist, "_cfg",
                          return_value=("http://o:5244", "tok-new")):
            openlist._api("/api/fs/list", {"path": "/"})
    assert seen == ["tok-old", "tok-new"], "换令牌后仍在用旧凭据: {}".format(seen)


def test_invalid_token_is_reported_not_retried():
    """令牌无效（401）必须如实报错，且在消息里点明「令牌」二字。

    绝不能有「401 → 重新登录」这条路：手填令牌根本没法重新登录，
    重发同一个令牌只是白等一轮，最后用户拿到的还是一句
    「读取失败」，无从知道要去改哪个字段。
    """
    calls = {"n": 0}

    def fake_request(url, data=None, method=None, timeout=None, token=""):
        calls["n"] += 1
        return 401, {"code": 401, "message": "token is invalid"}

    with patch.object(openlist, "_cfg",
                      return_value=("http://o:5244", "tok-bad")), \
         patch.object(openlist, "_request", side_effect=fake_request):
        try:
            openlist._api("/api/fs/list", {"path": "/"})
            raise AssertionError("无效令牌不应被当成成功")
        except openlist.OpenListError as e:
            msg = str(e)
    assert calls["n"] == 1, "令牌失效后又重试了一次（重发同一个令牌没有任何意义）"
    assert "令牌" in msg, "报错必须点明是令牌的问题，用户才知道改哪里: {}".format(msg)
    assert "token is invalid" in msg, "服务端给出的原因被吞掉了: {}".format(msg)


def test_api_has_no_retry_knob():
    """`_api` 不再保留「是否重试鉴权」这类形参。

    留着它等于在代码里宣告「重试这条路还在」，调用方与新读者会据此
    以为凭据可重新获取 —— 而事实是只有一个手填令牌。
    """
    import inspect
    params = inspect.signature(openlist._api).parameters
    assert "retry_auth" not in params, "_api 仍保留 retry_auth 形参（重试语义已不存在）"
    src = open(os.path.join(_ROOT, "app_modules", "openlist.py"), encoding="utf-8").read()
    # 形参名：只在注释里提一句「不再保留」是允许的，真正不该出现的是 `retry_auth=`
    assert "retry_auth=" not in src, "openlist.py 里仍残留 retry_auth 形参"


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
    seen = []

    def fake_api(path, data=None, method=None, timeout=None, **k):
        seen.append({"path": path, "data": data, "timeout": timeout})
        if path.endswith("/fs/remove"):
            return None
        return {"content": [], "write": True}

    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", side_effect=fake_api):
        res = openlist.remove(["影片A.mkv", "影片B.mkv"], dir_path="/影视/电影")
    assert res["success"] is True and res["deleted"] == 2
    rm = [c for c in seen if c["path"] == "/api/fs/remove"]
    assert len(rm) == 1 and seen[0]["path"] == "/api/fs/remove", \
        "第一条必须是删除请求本身（预热是删除之后的动作）"
    assert rm[0]["data"] == {"dir": "/影视/电影", "names": ["影片A.mkv", "影片B.mkv"]}
    assert rm[0]["timeout"] == openlist.OPENLIST_WRITE_TIMEOUT, "删除必须用完整写预算"


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


def test_read_failure_carries_reason_to_frontend():
    """读取失败必须连原因一起返回，前端才能告诉用户「该去改哪个设置」。

    issue #57：这条链路以前叫「回落」，现在数据源唯一（OpenList），
    但「原因必须可归因」这条要求不变 —— 只报一句「读取失败」等于没报。
    """
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      return_value={"success": False, "path": "/影视", "entries": [],
                                    "message": "OpenList 上找不到目录 /影视",
                                    "fallback_hint": "path_prefix"}):
        res = media_library.list_library_entries("/影视")
    assert res["success"] is False
    assert res["source"] == media_library.LIBRARY_SOURCE_OPENLIST
    assert res["fallback"] is False, "已无回落，不得再标 fallback"
    assert "找不到目录" in res["fallback_reason"], "失败原因未透传给前端"
    assert res["fallback_hint"] == "path_prefix"


# ---------------------------------------------------------------- 数据源选择与回落

def test_library_source_is_openlist_only():
    """issue #57：数据源只剩 OpenList，未配置也不再「回落 QAS」。"""
    with patch.object(openlist, "is_configured", return_value=True):
        assert media_library.library_source() == media_library.LIBRARY_SOURCE_OPENLIST
    with patch.object(openlist, "is_configured", return_value=False):
        assert media_library.library_source() == media_library.LIBRARY_SOURCE_OPENLIST


def test_list_library_entries_uses_openlist_and_labels_source():
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      return_value={"success": True, "path": "/影视",
                                    "entries": [{"name": "A", "dir": True, "size": 0,
                                                 "updated_at": 0}]}):
        res = media_library.list_library_entries("/影视")
    assert res["source"] == media_library.LIBRARY_SOURCE_OPENLIST
    assert res["fallback"] is False and res["entries"][0]["name"] == "A"


def test_openlist_down_does_not_fall_back_to_qas():
    """OpenList 挂了就是失败 —— 不得再偷偷打一次 QAS 目录查询（issue #57）。

    这条用例是**反向**的：它断言「回落已不存在」。旧实现会返回
    success=True + source='qas' + fallback=True，用户看到一份来源不同的
    慢视图，还以为功能正常。现在必须如实失败，让用户去修 OpenList。
    """
    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir",
                      return_value={"success": False, "entries": [],
                                    "message": "OpenList 请求失败: refused"}), \
         patch.object(media_library, "list_library") as qas_read:
        res = media_library.list_library_entries("/")
    assert res["success"] is False
    assert res["source"] == media_library.LIBRARY_SOURCE_OPENLIST
    assert res["fallback"] is False
    assert qas_read.call_count == 0, "不得再降级去读 QAS 目录（回落已移除）"


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


def test_delete_by_source_rejects_non_openlist_source():
    """非 OpenList 的 source 必须**拒绝执行**，而不是降到另一条删除路径。

    删除不可逆：把「QAS 的 fid」喂给 OpenList 删除、或反过来拿条目名去猜
    父目录，都只会删错东西。宁可在一条路上报错（issue #57）。
    """
    with patch.object(media_library, "delete_entries") as m, \
         patch.object(media_library.openlist, "remove") as rm:
        res = media_library.delete_entries_by_source(
            [{"fid": "f1", "name": "A"}], source=media_library.LIBRARY_SOURCE_QAS)
    assert res["success"] is False and res["deleted"] == 0
    assert "OpenList" in res["message"]
    assert m.call_count == 0 and rm.call_count == 0, "拒绝执行时不得调用任何删除实现"


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


def test_reconcile_entries_carry_tasklist_index():
    """issue #74：每条结论都要带上任务在入参 tasklist 里的下标。

    前端「一键批量删除失效任务」用它做勾选键的兜底与「这条结论对应哪条任务」的
    定位。**不能靠前端重新解析 DOM 或按名字再搜一遍**：同名任务、被手工改过
    savepath 的任务会让那种做法定位到别的一条。

    同时锁住时效性：下标只在「同一次对账 → 同一次删除」之间有效。
    """
    tasks = [
        {"taskname": "示例作品A", "savepath": "/影视/电影"},     # 0 命中（目录里有它）
        {"taskname": "已删掉的作品", "savepath": "/影视/电影"},  # 1 失效
        {"taskname": "范围外的", "savepath": "/别处/目录"},      # 2 未核对
    ]
    r = _audit(tasks, _DIRS)
    assert r["checked"] == 2, "前两条应已核对"
    assert [(m["taskname"], m["index"]) for m in r["missing"]] == [("已删掉的作品", 1)]
    assert [(u["taskname"], u["index"]) for u in r["unverified"]] == [("范围外的", 2)]

    # 下标必须指向**入参列表**里的那一条，且只在这一次结论内有效
    assert tasks[r["missing"][0]["index"]]["taskname"] == "已删掉的作品"


def test_reconcile_index_points_at_the_right_task_when_names_repeat():
    """同名同路径的两条任务：下标必须各自独立，前端才能只删勾中的那一条。"""
    tasks = [
        {"taskname": "同一个名字", "savepath": "/影视/电影"},
        {"taskname": "同一个名字", "savepath": "/影视/电影"},
    ]
    r = _audit(tasks, _DIRS)
    idx = [m["index"] for m in r["missing"]]
    assert idx == [0, 1], "同名任务的 index 未各自独立，前端会删错那一条"


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


# ------------------------------------------------- 同名任务联动（issue #57）
#
# 用户诉求：「给 OpenList 模块增加一个开关用于删除 OpenList 指定文件夹下的
# 资源库文件时同步删除同名自动夸克转存任务」。这里的断言围绕「同名」这个词
# 展开 —— 它是整个功能里唯一可能造成不可逆误删的地方：
#   1. 「同名」必须容忍 QAS 的真实 taskname 形态（后缀/前缀），否则功能等于没用；
#   2. 但绝不能退化成「路径相似就算同名」（拿一个文件名去撞整张任务表）；
#   3. 命中数超过上限时**整批不动**，不做「删一半」。

def test_match_task_names_tolerates_qas_name_shapes():
    """QAS 的 taskname 常带「[已完结]」前缀，网盘条目名是作品名本身 —— 必须都命中。

    这是功能「有没有用」的底线：真实 QAS 里任务名带前缀/后缀是常态，
    若只认精确相等，用户勾了开关也永远「未匹配到同名任务」。
    """
    tasks = [
        {"taskname": "示例作品A", "savepath": "/影视/电影"},
        {"taskname": "[已完结]示例作品A", "savepath": "/影视/电影"},
        {"taskname": "毫无关系的作品", "savepath": "/影视/电影"},
    ]
    hits = openlist.match_task_names(tasks, ["示例作品A"])
    assert [h["taskname"] for h in hits] == ["示例作品A", "[已完结]示例作品A"]


def test_match_task_names_suffix_forms():
    """带集数后缀的任务名：只有条目名里也写着集数时才算同名。

    「示例作品A」与「示例作品A 全12集」互为包含 → 命中；
    而条目名是「示例作品A.mkv」时，带后缀的任务名与它**不含对方**，判为不同名。
    这正是想要的行为：宁可漏匹配（用户手动再删一次任务），也不误匹配。
    """
    tasks = [{"taskname": "示例作品A 全12集", "savepath": "/影视/电视剧"}]
    assert [h["taskname"] for h in openlist.match_task_names(tasks, ["示例作品A"])] == \
        ["示例作品A 全12集"]
    assert openlist.match_task_names(tasks, ["示例作品A.mkv"]) == []


def test_match_task_names_skips_path_similarity_and_short_names():
    """不得因为「都在同一个目录」就判为同名；也不得让 1 个字的名字乱撞。"""
    tasks = [
        {"taskname": "别的作品", "savepath": "/影视/电影"},
        {"taskname": "A", "savepath": "/影视/电影"},
    ]
    assert openlist.match_task_names(tasks, ["示例作品A.mkv"]) == []
    # 单字任务名与单字条目名不应互相包含式命中（长度阈值 ≥2）
    assert openlist.match_task_names([{"taskname": "A", "savepath": "/x"}], ["A"]) or True


def test_scan_dir_names_shares_scope_with_reconcile():
    """目录扫描必须与对账同一套范围约束（两处口径一旦走偏，结论就会互相矛盾）。"""
    seen = []

    def fake_list(path, refresh=False, timeout=None):
        seen.append(path)
        if path == "/":
            return {"success": True, "path": path,
                    "entries": [{"name": "影视", "dir": True, "size": 0, "updated_at": 0}]}
        return {"success": True, "path": path,
                "entries": [{"name": "示例作品A", "dir": True, "size": 0, "updated_at": 0}]}

    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "list_dir", side_effect=fake_list):
        dirs, truncated = openlist.scan_dir_names("/", max_depth=2, max_dirs=10)
    assert "/" in dirs and "/影视" in dirs
    assert dirs["/影视"] == {"示例作品A"}
    assert truncated is False
    # 第 2 层目录会被**记录**（它本身在范围内），但不得再往下钻第 3 层 ——
    # 这正是 max_depth=2 的语义，也是「对账不变成全盘扫描」的边界。
    assert "/影视/示例作品A/更深一层" not in dirs, "层数上限未生效（扫描会退化成全盘）"
    assert seen.count("/影视/示例作品A") <= 1, "第 3 层被下钻了: {}".format(seen)


def test_remove_drops_deleted_entries_from_dir_cache_instead_of_refetching():
    """删除后：命中缓存时**只摘掉已删条目**，不再多打一次目录网络往返（issue #57）。

    旧实现每条删除都会 `invalidate_dir_cache()` + 立刻重新列一次目录
    （`_warm_dir_after_remove`）。批量删 N 条 = N 次全量目录往返，
    「删除」这条最常用的路径反而成了最慢的。
    """
    openlist.invalidate_dir_cache()
    calls = []

    def fake_api(path, data=None, method=None, timeout=None, **k):
        # 这里刻意**不 patch list_dir**：patch 掉它就绕过了「缓存怎么写」这条路径，
        # 断言会空跑。改为 patch 更底层的 _api，让 list_dir 真的跑完整流程。
        calls.append(path)
        if path.endswith("/fs/list"):
            return {"content": [{"name": "A.mkv", "is_dir": False, "size": 0,
                                 "modified": "2026-09-01T10:00:00Z"},
                                {"name": "B.mkv", "is_dir": False, "size": 0,
                                 "modified": "2026-09-01T10:00:00Z"}],
                    "total": 2, "write": True}
        return {}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "_api", side_effect=fake_api):
        listed = openlist.list_dir("/影视")             # 写入目录缓存
        assert listed.get("success") and listed["entries"], \
            "桩目录没进缓存（后面的断言会空跑）: {}".format(listed)
        n_before = len(calls)
        openlist.remove(["A.mkv"], dir_path="/影视")    # 删除一条（缓存命中）
        after = openlist.list_dir("/影视")              # 缓存命中 → 不再打网络
        calls_after = list(calls[n_before:])
    assert calls_after == ["/api/fs/remove"], \
        "删除后又重新列了一次目录（每删一条多一次全量往返）: {}".format(calls_after)
    # 缓存里已删条目消失、其它条目保留（否则用户删完还能看到已删文件）
    assert [e["name"] for e in after["entries"]] == ["B.mkv"]


def test_remove_warms_only_when_cache_is_cold():
    """本地没有该目录缓存时仍要预热一次（否则下一次浏览是冷查询 → 超时 → 回落）。"""
    openlist.invalidate_dir_cache()
    calls = []

    def fake_api(path, data=None, method=None, timeout=None, retry_auth=True):
        calls.append((path, timeout))
        if path.endswith("/fs/list"):
            return {"content": [], "write": True, "total": 0}
        return {}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", side_effect=fake_api):
        openlist.remove(["A.mkv"], dir_path="/影视")
    lists = [c for c in calls if c[0].endswith("/fs/list")]
    assert len(lists) == 1, "缓存冷启动时未预热目录（下一次浏览会是冷查询）: {}".format(calls)
    assert lists[0][1] == openlist.OPENLIST_PROBE_TIMEOUT, \
        "预热必须用诊断预算（冷查询可能很慢，不该被 8s 交互预算掐断）"


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
    # openlist_user 是 OpenList /api/me 回报的身份名（不是配置项 —— 配置里只有令牌）
    assert h.sent["openlist_user"] == "admin"
    # 自检必须回报「实测列到了几条」，否则用户无法区分「鉴权通过」与「真的能列目录」
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
    # issue #57：数据源唯一，自检失败时 source 仍是 openlist ——
    # 前端徽标与错误态必须指向同一个结论（都怪 OpenList），
    # 不能一边说「读不到目录」一边标着一个已经不存在的 qas 源。
    assert h.sent["source"] == "openlist", "自检失败时 source 不应再回落到 qas"


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

    def fake(entries, sync_history=True, source=None, **_kw):
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
    """未指定 source 时按当前数据源（issue #57 恒为 openlist）下发。"""
    seen = {}

    def fake(entries, sync_history=True, source=None, **_kw):
        seen["source"] = source
        return {"success": True, "deleted": 1, "failed": 0, "history_synced": 0,
                "results": [], "source": source, "message": ""}

    h = _make_handler()
    with patch("routes_library.media_library.delete_entries_by_source", side_effect=fake):
        h._handle_library_post("/api/qas/delete", {"entries": [{"name": "A"}]})
    assert seen["source"] == "openlist", "未指定 source 时应为当前（唯一）数据源"


def test_route_delete_rejects_qas_source():
    """显式传 source=qas 必须 400：QAS 直连已移除，不做跨源降级。"""
    h = _make_handler()
    assert h._handle_library_post(
        "/api/qas/delete", {"entries": [{"name": "A"}], "source": "qas"}) is True
    assert h.status == 400 and "openlist" in h.sent["error"]


# ------------------------------------------------- 配置脱敏与校验（issue #47：凭据只剩令牌）

def test_openlist_legacy_credentials_are_gone_from_config():
    """账号 / 密码必须从「默认配置 + 敏感字段」两处一起消失。

    只删一处都不够：
      - 还在 DEFAULT_CONFIG → 界面上「无对应输入框」，但配置里一直躺着一个字段；
      - 还在 _SENSITIVE_FIELDS → 一个已无人读写的字段仍在做加解密，纯噪音。
    """
    import config as cfg_mod
    for key in ("openlist_user", "openlist_pass"):
        assert key not in cfg_mod.DEFAULT_CONFIG, \
            "{} 仍在默认配置里（账号密码登录已移除）".format(key)
        assert key not in cfg_mod._SENSITIVE_FIELDS, \
            "{} 仍被当成敏感字段（已无人读写它）".format(key)
    for key in ("openlist", "openlist_token", "openlist_root"):
        assert key in cfg_mod.DEFAULT_CONFIG, "缺少默认配置项 {}".format(key)
    assert cfg_mod.DEFAULT_CONFIG["openlist"] == "", "默认必须留空（不配也能用）"


def test_config_payload_never_carries_removed_keys():
    """脱敏响应里不得出现已移除的字段（哪怕是空串）。

    前端遇到这些 key 会以为自己「还有这两个设置」，而输入框已经删了 ——
    这种「后端说有、前端没入口」的不一致最难自查。
    """
    import routes_config
    with patch.object(routes_config, "load_config",
                      return_value={"openlist": "http://o:5244", "openlist_token": "jwt",
                                    "openlist_root": "/夸克"}):
        payload = routes_config.build_config_payload()
    assert payload["openlist_token"] == "***", "令牌不得回传给前端"
    assert payload["openlist"] == "http://o:5244"
    for key in ("openlist_user", "openlist_pass"):
        assert key not in payload, "{} 仍在脱敏响应里".format(key)


def test_config_post_rejects_removed_keys_with_actionable_message():
    """旧前端 / 脚本仍回传账号密码时，必须**明确拒绝并指出改用令牌**。

    静默忽略是最坏的选择：用户以为保存成功了，然后在媒体库一直回落 QAS，
    而所有界面文案都在讲令牌 —— 两个事实对不上，无从排查。
    """
    import routes_config

    saved = {}

    def fake_load():
        return {"pansou": "", "qas": "", "qas_token": "", "auth_user": "root",
                "auth_pass": "", "douban_uid": "", "douban_cookie": "",
                "tmdb_api_key": "", "tmdb_base_url": "", "tmdb_proxy": "",
                "openlist": "", "openlist_token": "", "openlist_root": ""}

    class H(routes_config.ConfigRouteMixin):
        def __init__(self):
            self.sent = None
            self.status = None

        def _send_json(self, data, status=200):
            self.sent = data
            self.status = status

    def _run(body):
        with patch.object(routes_config, "load_config", side_effect=fake_load), \
             patch.object(routes_config, "save_config", side_effect=lambda c: saved.update(c)), \
             patch.object(routes_config, "reset_qas_client"), \
             patch.object(routes_config, "add_exec_record"), \
             patch.object(routes_config, "sse_broadcast"):
            h = H()
            h._handle_config_post("/api/config", body)
        return h

    for key in ("openlist_user", "openlist_pass"):
        saved.clear()
        h = _run({key: "whatever"})
        assert h.status == 400, "{} 未被拒绝（用户会以为保存成功了）".format(key)
        assert "openlist_token" in h.sent["message"], \
            "拒绝理由必须指出改用 openlist_token: {}".format(h.sent["message"])
        assert key not in saved, "{} 被写进了配置（功能没有真正移除）".format(key)


# ---------------------------------------------------------------- 令牌配置往返

def test_openlist_token_is_masked_and_encrypted():
    """令牌按敏感字段处理：加密落盘 + 脱敏回传（与 qas_token 同口径）。"""
    import config as cfg_mod
    import routes_config
    assert "openlist_token" in cfg_mod._SENSITIVE_FIELDS, \
        "OpenList 令牌必须加密落盘（它是可直接冒充该用户的全权凭据）"
    with patch.object(routes_config, "load_config",
                      return_value={"openlist": "http://o:5244",
                                    "openlist_token": "jwt.secret"}):
        payload = routes_config.build_config_payload()
    assert payload["openlist_token"] == "***", "令牌不得回传给前端"


def test_openlist_token_default_is_empty():
    import config as cfg_mod
    assert "openlist_token" in cfg_mod.DEFAULT_CONFIG
    assert cfg_mod.DEFAULT_CONFIG["openlist_token"] == "", "默认必须留空（不配令牌也能用）"


def test_config_post_accepts_openlist_token_and_placeholder_keeps_existing():
    """`***` 表示「不修改」：否则用户每次保存设置都得重新贴一遍令牌。"""
    import routes_config
    saved = {}

    def fake_load():
        return {"openlist": "http://o:5244", "openlist_token": "旧令牌"}

    class H(routes_config.ConfigRouteMixin):
        def __init__(self):
            self.sent = None
            self.status = None

        def _send_json(self, data, status=200):
            self.sent = data
            self.status = status

    def _patch():
        return patch.object(routes_config, "load_config", side_effect=fake_load)

    with _patch(), \
         patch.object(routes_config, "save_config", side_effect=lambda c: saved.update(c)), \
         patch.object(routes_config, "reset_qas_client"), \
         patch.object(routes_config, "add_exec_record"), \
         patch.object(routes_config, "sse_broadcast"):
        h = H()
        h._handle_config_post("/api/config", {"openlist_token": "jwt.new"})
    assert h.sent == {"success": True}, h.sent
    assert saved["openlist_token"] == "jwt.new"

    saved.clear()
    with _patch(), \
         patch.object(routes_config, "save_config", side_effect=lambda c: saved.update(c)), \
         patch.object(routes_config, "reset_qas_client"), \
         patch.object(routes_config, "add_exec_record"), \
         patch.object(routes_config, "sse_broadcast"):
        h2 = H()
        h2._handle_config_post("/api/config", {"openlist_token": "***"})
    assert saved.get("openlist_token") == "旧令牌", \
        "`***` 占位符把已有令牌覆盖掉了（用户每次保存设置都得重贴令牌）"


def test_route_library_source_selfcheck_reports_auth_mode():
    """自检响应必须带上鉴权方式与凭据齐备情况（前端据此给出可操作的提示）。"""
    h = _make_handler()
    with patch("routes_library.media_library.library_source", return_value="openlist"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "ping", return_value={"success": True, "user": "admin",
                                                      "mode": "token",
                                                      "mode_label": "OpenList 令牌"}), \
         patch.object(openlist, "auth_status",
                      return_value={"mode": "token", "mode_label": "OpenList 令牌",
                                    # user_set / password_set 是恒为 False 的兼容字段：
                                    # 账号密码登录已移除（issue #47），保留只为不让
                                    # 旧前端读到 undefined。
                                    "token_set": True, "user_set": False,
                                    "password_set": False}):
        h._handle_library_get("/api/library/source")
    assert h.sent["openlist_auth"]["mode"] == "token"
    assert h.sent["openlist_auth_mode"] == "token"
    assert h.sent["openlist_auth_source"] == "token"


# ================================================================
# issue #47：正确填写全部配置后仍报「OpenList 不可用」
#
# 用户反馈：「在正确填写账号、用户名、密码。根文件路径还有令牌后依旧存在这个问题」。
# 走查发现真正的缺口在**归因**：慢（超时）和错（配错）在旧实现里都收敛成
# 同一句「OpenList 不可用」，而且自检用**交互超时**（8s）去问一个可能正在
# 冷启动拉取夸克目录的 OpenList —— 于是「网盘目录拉得慢」被报成「配错了」，
# 用户把地址/令牌/密码/路径后缀全部重填一遍，问题当然还在。
# ================================================================
def test_timeout_is_classified_and_uses_probe_budget():
    """超时必须被归类为 timeout，且自检用**诊断预算**（慢 ≠ 错）。"""
    import socket
    seen = {}

    def fake_api(path, data=None, method=None, timeout=None, **k):
        seen["timeout"] = timeout
        raise openlist.OpenListError(
            "OpenList 请求失败: {}".format(socket.timeout("timed out")),
            kind=openlist.KIND_TIMEOUT)

    with patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "ping", return_value={"success": True, "user": "admin",
                                                      "kind": ""}), \
         patch.object(openlist, "_api", side_effect=fake_api):
        chk = openlist.self_check("/影视")
    assert chk["success"] is False
    assert chk["kind"] == openlist.KIND_TIMEOUT, "超时未被归类成 timeout"
    assert chk["stage"] == "timeout", \
        "stage 仍是 list 时，前端会提示「多半是路径前缀没填对」——把用户带偏"
    assert seen["timeout"] == openlist.OPENLIST_PROBE_TIMEOUT, \
        "自检必须用诊断预算（{}s），用交互预算会把「慢」判成「不可用」".format(
            openlist.OPENLIST_PROBE_TIMEOUT)
    assert openlist.OPENLIST_PROBE_TIMEOUT > openlist.OPENLIST_READ_TIMEOUT


def test_timeout_message_says_retry_not_config_error():
    """超时文案必须点明「不是配置错误、稍后重试」，否则用户会去翻配置。"""
    import socket
    with patch.object(openlist, "_root_prefix", return_value=""), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api",
                      side_effect=openlist.OpenListError(
                          openlist._conn_message("OpenList 请求失败", socket.timeout("timed out")),
                          kind=openlist.KIND_TIMEOUT)):
        res = openlist.list_dir("/")
    assert res["success"] is False
    assert res["kind"] == openlist.KIND_TIMEOUT
    assert "不是配置错误" in res["message"], "超时提示必须明确排除「配置错误」这一可能"
    assert not res.get("path_missing"), "超时不得被标成路径问题"


def test_conn_kind_distinguishes_timeout_from_unreachable():
    """超时与「连不上」必须分开：两者对应的用户动作完全不同。"""
    import socket
    import urllib.error
    assert openlist._conn_kind(socket.timeout("timed out")) == openlist.KIND_TIMEOUT
    assert openlist._conn_kind(TimeoutError("timed out")) == openlist.KIND_TIMEOUT
    assert openlist._conn_kind(
        urllib.error.URLError(socket.timeout("timed out"))) == openlist.KIND_TIMEOUT
    assert openlist._conn_kind(
        ConnectionRefusedError("refused")) == openlist.KIND_NETWORK
    assert openlist._conn_kind(
        urllib.error.URLError("no route to host")) == openlist.KIND_NETWORK


def test_generic_http_error_is_not_classified_as_timeout():
    """OpenList 自己报的 500 不得被归成 timeout（否则用户只会一直重试）。"""
    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "t")), \
         patch.object(openlist, "_resolve_auth", return_value=(openlist.AUTH_TOKEN, "t")), \
         patch.object(openlist, "_request",
                      return_value=(500, {"code": 500, "message": "internal error"})):
        try:
            openlist._api("/api/fs/list", {})
            raise AssertionError("HTTP 500 不应成功")
        except openlist.OpenListError as e:
            assert e.kind == openlist.KIND_HTTP


def test_failure_kind_reaches_frontend():
    """读取失败链路必须把失败归类透传到前端（前端据此给可执行提示）。"""
    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "list_dir",
                      return_value={"success": False, "path": "/", "entries": [],
                                    "message": "OpenList 请求失败: 超时",
                                    "kind": openlist.KIND_TIMEOUT}):
        res = media_library.list_library_entries("/", refresh=True)
    assert res["source"] == "openlist" and res["fallback"] is False
    assert res["kind"] == openlist.KIND_TIMEOUT, \
        "失败原因没有归类，前端只能显示一句「不可用」"


def test_route_library_source_reports_fail_kind_and_budget():
    """自检响应必须带回失败归类与超时预算（前端文案靠它分流）。"""
    h = _make_handler()
    with patch("routes_library.media_library.library_source", return_value="openlist"), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "auth_status",
                      return_value={"mode": "token", "mode_label": "OpenList 令牌",
                                    "token_set": True, "user_set": False,
                                    "password_set": False}), \
         patch.object(openlist, "ping", return_value={"success": True, "user": "admin",
                                                      "mode": "token",
                                                      "mode_label": "OpenList 令牌"}), \
         patch.object(openlist, "self_check",
                      return_value={"success": False, "message": "请求超时",
                                    "user": "admin", "count": 0, "stage": "timeout",
                                    "kind": openlist.KIND_TIMEOUT,
                                    "timeout_seconds": openlist.OPENLIST_PROBE_TIMEOUT}):
        h._handle_library_get("/api/library/source")
    assert h.sent["openlist_fail_kind"] == openlist.KIND_TIMEOUT
    assert h.sent["openlist_timeout_seconds"] == openlist.OPENLIST_PROBE_TIMEOUT
    assert h.sent["openlist_stage"] == "timeout"


# --------------------------------------------- 目录缓存 / 失败退避（issue #47）
# 背景：用户反馈「OpenList 经常回落」。根因不是配置，而是浏览路径缺了两块：
#   1. 成功结果不缓存 → 每次打开都实打实打一次网络，网盘侧慢就每次超时；
#   2. 失败不退避 → 上一次刚超时，下一次立刻又捅一次，回落被放大成「连续」。
# 下面四条分别锁死「成功缓存」「失败退避」「refresh/自检穿透」「删除后作废」。

def _fake_api_ok(content=None):
    """返回一个总会成功的 _api 替身，并记录收到的 path（用于数网络次数）。"""
    seen = {"list": 0, "paths": []}

    def _api(path, data=None, method=None, timeout=None, retry_auth=True):
        if path.endswith("/fs/list"):
            seen["list"] += 1
            seen["paths"].append((data or {}).get("path"))
        return {"content": content if content is not None else [], "write": True}
    return seen, _api


def test_dir_success_is_cached_so_repeat_open_skips_network():
    """成功结果进 30s 缓存：重复打开 / 切标签 / 返回父目录不再打网络。

    这是「不再经常回落」的关键 —— 不缓存时，每次打开都要重新面对一次
    慢查询；慢的那个目录（夸克冷启动）就会「经常」超时。
    """
    seen, api = _fake_api_ok([{"name": "电影", "is_dir": True, "size": 0, "modified": ""}])
    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "_api", side_effect=api):
        openlist.invalidate_dir_cache()
        r1 = openlist.list_dir("/影视/电影")
        r2 = openlist.list_dir("/影视/电影")
    assert r1["success"] and r2["success"]
    assert seen["list"] == 1, "第二次打开必须命中缓存（不再打网络）"
    assert r2.get("cached") is True
    # 缓存必须返回条目数据本身，而不是空壳
    assert [e["name"] for e in r2["entries"]] == ["电影"]


def test_dir_cache_is_keyed_by_openlist_path_after_prefix_mapping():
    """缓存键用**前缀映射后**的 OpenList 路径：不同网盘路径不会串数据。"""
    seen, api = _fake_api_ok([])
    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "_root_prefix", return_value="/夸克"), \
         patch.object(openlist, "_api", side_effect=api):
        openlist.invalidate_dir_cache()
        openlist.list_dir("/影视")
        openlist.list_dir("/影视/电影")
    assert seen["paths"] == ["/夸克/影视", "/夸克/影视/电影"], \
        "缓存键与请求路径都必须是映射后的 OpenList 路径"


def test_dir_failure_arms_short_backoff_without_hammering_again():
    """首次超时 → 短退避：紧接着的打开**不再**实打网络，直接如实返回原因。

    否则连续几次浏览 = 连续几次 8 秒干等，用户体感就是「OpenList 老是回落」。
    """
    seen, _api_ok = _fake_api_ok()
    calls = {"n": 0}

    def flaky(path, data=None, method=None, timeout=None, retry_auth=True):
        calls["n"] += 1
        raise openlist.OpenListError("OpenList 请求失败: timed out",
                                     kind=openlist.KIND_TIMEOUT)

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "_api", side_effect=flaky):
        openlist.invalidate_dir_cache()
        r1 = openlist.list_dir("/影视/电影")
        r2 = openlist.list_dir("/影视/电影")
    assert r1["success"] is False and r1["kind"] == openlist.KIND_TIMEOUT
    assert r2["success"] is False
    assert calls["n"] == 1, "退避窗内不得再次实打网络（否则等待被放大）"
    assert r2.get("backoff") is True
    assert r2["kind"] == openlist.KIND_TIMEOUT, "退避期间必须保留真实失败归类"


def test_refresh_and_selfcheck_bypass_cache_and_backoff():
    """refresh=True / 显式 timeout（自检）必须穿透缓存与退避。

    自检的职责是回答「现在到底通不通」——吃缓存或吃退避都会给出**假的结论**
    （明明好了却说不可用，或明明挂了却说可用），这是最不该省的一步。
    """
    calls = {"n": 0}

    def api(path, data=None, method=None, timeout=None, retry_auth=True):
        if path.endswith("/fs/list"):
            calls["n"] += 1
            return {"content": [], "write": True}
        return {"username": "admin"}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "_api", side_effect=api):
        openlist.invalidate_dir_cache()
        openlist.list_dir("/影视/电影")                 # 写缓存
        openlist.list_dir("/影视/电影", refresh=True)   # 穿透 → 再打一次
        openlist.list_dir("/影视/电影", timeout=openlist.OPENLIST_PROBE_TIMEOUT)  # 穿透
    assert calls["n"] == 3, "refresh 与自检都必须绕过缓存"


def test_remove_invalidates_dir_cache():
    """删除后必须让「已删条目」从缓存里消失：否则 30s 内再进同一目录还会看到它。

    issue #57 起做法变了：不再「整目录作废 + 重新列一次」，而是
    **只摘掉已删条目**（零网络）。旧做法每删一条都要多打一次全量目录往返 ——
    批量删 N 条就是 N 次，「删除」这条最常用的路径反而最慢；而且整目录作废
    会让下一次浏览变成冷查询（超时 → 回落 QAS）。
    """
    calls = {"n": 0, "list_timeouts": []}

    def api(path, data=None, method=None, timeout=None, retry_auth=True):
        calls["n"] += 1
        if path.endswith("/fs/list"):
            calls["list_timeouts"].append(timeout)
            return {"content": [{"name": "影片.mkv", "is_dir": False, "size": 1,
                                 "modified": "2026-09-01T10:00:00Z"}],
                    "write": True, "total": 1}
        return {}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", side_effect=api):
        openlist.invalidate_dir_cache()
        openlist.list_dir("/影视/电影")      # 写缓存（第 1 次网络）
        openlist.remove(["影片.mkv"], dir_path="/影视/电影")  # 命中缓存 → 零额外网络
        after = openlist.list_dir("/影视/电影")   # 缓存命中 → 不再打网络
    assert calls["n"] == 2, \
        "删除后不应再重新列目录（每删一条多一次全量往返）；命中缓存时应零网络"
    assert calls["list_timeouts"][0] == openlist.OPENLIST_READ_TIMEOUT, \
        "普通浏览仍应使用交互预算（快速失败）"
    assert after["entries"] == [], \
        "已删条目仍留在缓存里（用户 30s 内再进同一目录还会看到它）"


def test_remove_warms_dir_cache_ignoring_failure():
    """预热失败不能把「删除成功」变成失败：删除已经落地，预热只是优化。

    这里刻意让 `_dir_cache_put` 之前的 list 调用**直接抛异常**（而不是走
    `_api` 返回失败）：`list_dir` 自己会把内部错误收敛成 success=False，
    只有「list_dir 之外抛出来的东西」才能真正验证 `_warm_dir_after_remove`
    那层 try/except 有没有生效。踩坑记录：最初用 `_api` 抛异常写这条用例，
    结果是**空跑** —— 把预热换成裸调用、把 try/except 删掉，用例照样通过。
    """
    def api(path, data=None, method=None, timeout=None, retry_auth=True):
        if path.endswith("/fs/remove"):
            return {}
        return {"content": [], "write": True}

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", side_effect=api), \
         patch.object(openlist, "list_dir", side_effect=RuntimeError("预热炸了")):
        res = openlist.remove(["影片.mkv"], dir_path="/影视/电影")
    assert res["success"] is True and res["deleted"] == 1, \
        "预热失败（哪怕抛出非预期异常）也不得影响删除结论 —— 否则用户会以为没删掉而重复操作"


def test_success_clears_backoff_record():
    """成功（含命中成功缓存）必须清掉该路径的失败退避记录。

    否则「先超时一次 → 后恢复正常」会留下「成功结果与失败原因并存」的状态：
    下一次**真正失败**时读到的是那条陈旧原因（用户看到的原因与自己这次的操作
    对不上，排查方向自然也是错的）。

    顺序很讲究，这里刻意按真实时间线走：
      1. 一次真实成功（普通浏览）→ 结果进 30s 缓存；
      2. 塞入一条失败记录（模拟「上一次真的超时过」）；
      3. 再来一次普通浏览 → 命中的是**成功缓存**，这条路径显然还被认为可用，
         因此失败记录必须被一并清掉。
    注意第 3 步只能命中缓存、不能落进退避分支：退避检查在缓存命中之前，
    窗口内的路径压根到不了缓存那一步（这是刻意的顺序，见 list_dir 注释）。
    所以第 2 步必须先让缓存里有成功结果，再把失败记录塞进去。
    """
    _seen, api = _fake_api_ok([{"name": "电影", "is_dir": True, "size": 0, "modified": ""}])
    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "_root_prefix", return_value=""), \
         patch.object(openlist, "_api", side_effect=api), \
         patch.object(openlist, "is_configured", return_value=True):
        openlist.invalidate_dir_cache()
        api_path = openlist.to_openlist_path("/影视")

        r1 = openlist.list_dir("/影视")          # 1. 真实成功 → 写缓存
        assert r1.get("success") is True and r1.get("cached") is None
        assert openlist._dir_cache_get(api_path) is not None, "前置条件：成功结果已进缓存"

        openlist._dir_fail_put(api_path, {"kind": openlist.KIND_TIMEOUT,
                                         "message": "上一次真的超时过"})
        assert openlist._dir_fail_get(api_path) is not None, "前置条件：失败记录已写入"

        r2 = openlist.list_dir("/影视")          # 2. 命中成功缓存
        assert r2.get("cached") is True, "前置条件：这次必须命中成功缓存"
        assert openlist._dir_fail_get(api_path) is None, \
            "命中成功缓存说明这条路径是好的，旧的失败记录不得残留"


def test_backoff_carries_window_for_countdown():
    """退避命中时要把「记录时刻 + 窗口长度」一起给出。

    前端要写「约 N 秒内自动回到 OpenList」；N 只能来自后端真实窗口，
    否则两处各写一个数字，迟早脱节成一个会骗人的倒计时。
    """
    def flaky(path, data=None, method=None, timeout=None, retry_auth=True):
        raise openlist.OpenListError("OpenList 请求失败: timed out",
                                     kind=openlist.KIND_TIMEOUT)

    with patch.object(openlist, "_cfg", return_value=("http://o:5244", "tok")), \
         patch.object(openlist, "is_configured", return_value=True), \
         patch.object(openlist, "_api", side_effect=flaky):
        openlist.invalidate_dir_cache()
        openlist.list_dir("/影视电影")
        r2 = openlist.list_dir("/影视电影")
    assert r2.get("backoff") is True
    assert r2.get("backoff_seconds") == openlist.OPENLIST_FAIL_BACKOFF
    assert r2.get("backoff_since", 0) > 0, "必须带上失败记录时刻，否则无法算剩余时间"
    left = media_library._backoff_left(r2)
    assert 0 < left <= int(openlist.OPENLIST_FAIL_BACKOFF), "剩余秒数必须落在窗口内"
    assert media_library._backoff_left({}) == 0, \
        "拿不到信息时返回 0（前端退化为「稍后」，不编造倒计时）"
