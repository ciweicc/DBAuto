"""双向管理（issue #27）：QAS 任务列表 / 网盘媒体库 / 删除 + 历史同步。

验证点：
  1. list_tasks / list_library 对 QAS 响应做正确归一化（含异常兜底）；
  2. delete_entries 删除成功 → 联动清理 DBAuto 历史（双向同步核心）；
  3. 删除失败 / 部分失败时的 success 语义不可被误判；
  4. 路径与条目校验拦截（缺 fid/path、批量上限）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

from unittest.mock import patch

import media_library
from media_library import (
    list_tasks, list_library, delete_entries, delete_tasks,
    MAX_DELETE_BATCH, MAX_TASK_DELETE_BATCH,
)


class _FakeClient:
    """按调用记录返回预置响应的 QAS 假客户端。"""

    def __init__(self, data_resp=None, savepath_resp=None, delete_resp=None,
                 update_resp=None):
        self.data_resp = data_resp or {"success": True, "data": {"tasklist": []}}
        self.savepath_resp = savepath_resp or {"success": True, "data": {"fid": 0, "list": []}}
        self.delete_resp = delete_resp or {"success": True, "code": 0}
        self.update_resp = update_resp or {"success": True, "message": "配置更新成功"}
        self.delete_calls = []
        self.update_calls = []
        self.detail_path = None

    def get_data(self):
        return self.data_resp

    def get_savepath_detail(self, path=None, fid=None):
        self.detail_path = path
        return self.savepath_resp

    def delete_file(self, path=None, fid=None):
        self.delete_calls.append({"path": path, "fid": fid})
        return self.delete_resp

    def update_tasklist(self, tasklist):
        self.update_calls.append(tasklist)
        return self.update_resp


# ---------- list_tasks ----------

def test_list_tasks_ok():
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": [
        {"taskname": "电影A", "shareurl": "https://pan.quark.cn/s/x", "savepath": "/媒体库/电影"},
    ]}})
    with patch.object(media_library, "_client", return_value=fake):
        res = list_tasks()
    assert res["success"] is True
    assert len(res["tasks"]) == 1
    assert res["tasks"][0]["taskname"] == "电影A"


def test_list_tasks_qas_error():
    fake = _FakeClient(data_resp={"success": False, "message": "未登录"})
    with patch.object(media_library, "_client", return_value=fake):
        res = list_tasks()
    assert res["success"] is False
    assert res["tasks"] == []
    assert "未登录" in res["message"]


def test_list_tasks_exception_graceful():
    class Boom:
        def get_data(self):
            raise RuntimeError("连接超时")

    with patch.object(media_library, "_client", return_value=Boom()):
        res = list_tasks()
    assert res["success"] is False
    assert "连接超时" in res["message"]


# ---------- list_library ----------

def test_list_library_normalizes_fields():
    fake = _FakeClient(savepath_resp={"success": True, "data": {"fid": 0, "list": [
        {"fid": "abc", "file_name": "电影A.mkv", "dir": False, "size": 1024, "updated_at": 1},
        {"fid": "def", "file_name": "电视剧", "dir": True, "size": 0, "updated_at": 2},
    ]}})
    with patch.object(media_library, "_client", return_value=fake):
        res = list_library(path="/媒体库")
    assert res["success"] is True
    assert [e["name"] for e in res["entries"]] == ["电影A.mkv", "电视剧"]
    assert res["entries"][1]["dir"] is True


def test_list_library_error_returns_empty():
    fake = _FakeClient(savepath_resp={"success": False, "message": "未登录"})
    with patch.object(media_library, "_client", return_value=fake):
        res = list_library(path="/媒体库", use_cache=False)
    assert res["success"] is False
    assert res["entries"] == []


# ---------- delete_entries （双向同步核心） ----------

def test_delete_success_syncs_history():
    fake = _FakeClient(delete_resp={"success": True, "code": 0})
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_history_item", return_value=True) as m:
        res = delete_entries([{"fid": "abc", "name": "电影A"}], sync_history=True)
    assert res["success"] is True
    assert res["deleted"] == 1 and res["failed"] == 0
    assert res["history_synced"] == 1
    m.assert_called_once_with("电影A")


def test_delete_without_history_sync():
    fake = _FakeClient(delete_resp={"success": True, "code": 0})
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_history_item") as m:
        res = delete_entries([{"fid": "abc", "name": "电影A"}], sync_history=False)
    assert res["success"] is True and res["deleted"] == 1
    m.assert_not_called()


def test_delete_uses_title_for_history_when_given():
    fake = _FakeClient(delete_resp={"success": True, "code": 0})
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_history_item", return_value=True) as m:
        delete_entries([{"fid": "abc", "name": "电影A.2024.2160p.mkv", "title": "电影A"}])
    m.assert_called_once_with("电影A")


def test_delete_failure_not_reported_as_success():
    fake = _FakeClient(delete_resp={"success": False, "message": "文件不存在"})
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_history_item") as m:
        res = delete_entries([{"fid": "abc", "name": "电影A"}])
    assert res["success"] is False
    assert res["deleted"] == 0 and res["failed"] == 1
    m.assert_not_called()
    assert "文件不存在" in res["results"][0]["message"]


def test_delete_partial_failure_marks_unsuccessful():
    """部分成功时必须 success=False，避免调用方误判为全量成功。"""

    class MixedClient:
        def __init__(self):
            self.n = 0

        def delete_file(self, path=None, fid=None):
            self.n += 1
            return {"success": self.n == 1, "message": "第二条失败"}

    with patch.object(media_library, "_client", return_value=MixedClient()), \
         patch.object(media_library, "delete_history_item", return_value=False):
        res = delete_entries([{"fid": "1", "name": "A"}, {"fid": "2", "name": "B"}])
    assert res["success"] is False
    assert res["deleted"] == 1 and res["failed"] == 1


def test_delete_rejects_entry_without_fid_or_path():
    fake = _FakeClient()
    with patch.object(media_library, "_client", return_value=fake):
        res = delete_entries([{"name": "电影A"}])
    assert res["success"] is False
    assert res["failed"] == 1
    assert fake.delete_calls == []


def test_delete_batch_limit():
    fake = _FakeClient()
    entries = [{"fid": str(i), "name": "n{}".format(i)} for i in range(MAX_DELETE_BATCH + 1)]
    with patch.object(media_library, "_client", return_value=fake):
        res = delete_entries(entries)
    assert res["success"] is False
    assert "最多删除" in res["message"]
    assert fake.delete_calls == []


def test_delete_accepts_path_when_no_fid():
    fake = _FakeClient(delete_resp={"success": True, "code": 0})
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_history_item", return_value=False):
        res = delete_entries([{"path": "/媒体库/电影A"}])
    assert fake.delete_calls[0]["path"] == "/媒体库/电影A"
    assert res["deleted"] == 1


# ---------- 路由校验（routes_library） ----------

def _make_handler(body=None, params=None):
    """构造一个只保留路由所需最小接口的假 handler。"""
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


def test_route_library_rejects_path_traversal():
    h = _make_handler(params={"path": "/媒体库/../../etc"})
    assert h._handle_library_get("/api/qas/library") is True
    assert ".." in h.sent["error"]
    assert h.status == 400


def test_route_library_get_tasks():
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": [{"taskname": "A"}]}})
    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake):
        assert h._handle_library_get("/api/qas/tasks") is True
    assert h.sent["success"] is True and h.status == 200


def test_route_library_get_unknown_route_not_handled():
    h = _make_handler()
    assert h._handle_library_get("/api/whatever") is False


def test_route_delete_rejects_missing_fid_and_path():
    h = _make_handler()
    assert h._handle_library_post("/api/qas/delete", {"entries": [{"name": "A"}]}) is True
    assert "fid" in h.sent["error"] and h.status == 400


def test_route_delete_rejects_empty_entries():
    h = _make_handler()
    assert h._handle_library_post("/api/qas/delete", {"entries": []}) is True
    assert h.status == 400


def test_route_delete_passes_sync_switch_to_backend():
    """路由层：/api/qas/delete 的 sync_tasks 必须真的传给删除实现（默认关）。"""
    fake = _FakeClient(delete_resp={"success": True, "code": 0})
    seen = {}

    def fake_delete(entries, sync_history=True, source=None,
                    sync_tasks=False, sync_tasks_path_filter=""):
        seen["sync_tasks"] = sync_tasks
        seen["path_filter"] = sync_tasks_path_filter
        return {"success": True, "deleted": 1, "failed": 0, "history_synced": 0,
                "results": [], "source": source, "message": ""}

    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_entries_by_source",
                      side_effect=fake_delete):
        h._handle_library_post("/api/qas/delete",
                               {"entries": [{"fid": "1", "name": "A"}]})
        assert seen["sync_tasks"] is False, "不传开关时必须当作关闭"
        h._handle_library_post("/api/qas/delete",
                               {"entries": [{"fid": "1", "name": "A"}],
                                "sync_tasks": True, "sync_tasks_path_filter": "/影视"})
    assert seen["sync_tasks"] is True and seen["path_filter"] == "/影视"


def test_route_delete_rejects_bad_sync_path():
    fake = _FakeClient(delete_resp={"success": True, "code": 0})
    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake):
        h._handle_library_post("/api/qas/delete",
                               {"entries": [{"fid": "1", "name": "A"}],
                                "sync_tasks": True,
                                "sync_tasks_path_filter": "/影视/../etc"})
    assert h.status == 400 and ".." in h.sent["error"]


def test_route_delete_ok():
    fake = _FakeClient(delete_resp={"success": True, "code": 0})
    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_history_item", return_value=True):
        assert h._handle_library_post("/api/qas/delete", {"entries": [{"fid": "1", "name": "A"}]}) is True
    assert h.sent["success"] is True and h.status == 200


# ---------- 读取超时预算（issue #27「网盘文件模块访问有点慢」） ----------

def test_read_paths_use_short_timeout():
    """读接口（任务/目录）必须走收窄后的超时预算，删除保持原预算。

    根因：QASClient 默认 timeout=15，叠加 http_get 的 3 次重试后，
    单次目录请求最坏等待 ≈ 32s，前端只能一直「加载中」。
    这里锁死「读接口不用默认超时」，防止回归把等待时间再拉长。
    """
    seen = []

    def fake_client(timeout=15, retries=None):
        seen.append((timeout, retries))
        return _FakeClient()

    media_library.invalidate_library_cache()
    with patch.object(media_library, "_client", side_effect=fake_client), \
         patch.object(media_library, "delete_history_item", return_value=False):
        list_tasks()
        assert seen[-1] == (media_library.LIBRARY_READ_TIMEOUT, 1), \
            "读接口必须用收窄超时且不做底层重试（重试只会把等待翻倍）"
        list_library(path="/")
        assert seen[-1] == (media_library.LIBRARY_READ_TIMEOUT, 1)
        delete_entries([{"fid": "1", "name": "A"}])
        assert seen[-1][0] == 15, "删除是破坏性操作，不应被收窄超时"


def test_read_timeout_is_smaller_than_default():
    assert 0 < media_library.LIBRARY_READ_TIMEOUT < 15


def test_read_client_forwards_timeout_to_qasclient():
    """_read_client 必须把收窄后的超时真正透传给底层 HTTP 客户端。"""
    from api_client import QASClient
    cfg = type("Cfg", (), {"qas": "http://127.0.0.1:1", "qas_token": "t"})()
    with patch.object(media_library.ConfigManager, "get_instance",
                      staticmethod(lambda: cfg)):
        c = media_library._read_client()
    assert isinstance(c, QASClient)
    assert c.timeout == media_library.LIBRARY_READ_TIMEOUT


def test_route_get_returns_502_when_qas_down():
    """QAS 不可达时路由必须给出明确 502，而不是让请求悬停。"""
    class Boom:
        def get_savepath_detail(self, path=None, fid=None):
            raise TimeoutError("connect timed out")

    h = _make_handler(params={"path": "/"})
    with patch.object(media_library, "_client", return_value=Boom()):
        assert h._handle_library_get("/api/qas/library") is True
    assert h.status == 502
    assert h.sent["success"] is False


# ---------- delete_tasks：删除 QAS 任务条目（issue #27 跟进） ----------
#
# 用户确认 QAS 为自部署服务，因此「改任务配置」只影响自己的任务表。
# 风险点不在「能不能改」，而在「怎么改不会把别的任务一起抹掉」——
# QAS `/update` 是整表替换语义，以下用例围绕这一点锁死行为。


def _tasks(*names):
    return [{"taskname": n, "shareurl": "https://pan.quark.cn/s/{}".format(n),
             "savepath": "/媒体库/{}".format(n), "pattern": "$TV", "replace": "",
             "addition": {"auto_unarchive": {"enable": True}}} for n in names]


def test_delete_task_removes_only_target():
    """核心：删除 1 条任务，写回的列表必须保留其余任务且字段原样。"""
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A", "B", "C")}})
    with patch.object(media_library, "_client", return_value=fake):
        res = delete_tasks([{"taskname": "B", "savepath": "/媒体库/B"}])
    assert res["success"] is True and res["deleted"] == 1
    written = [t["taskname"] for t in fake.update_calls[0]]
    assert written == ["A", "C"], "整表写回时必须保留其它任务"
    # addition（任务级插件配置）不得被清洗，否则解压/订阅行为会静默改变
    assert fake.update_calls[0][0]["addition"]["auto_unarchive"]["enable"] is True
    assert fake.update_calls[0][0]["pattern"] == "$TV"
    assert [t["taskname"] for t in res["tasks"]] == ["A", "C"]


def test_delete_task_by_index():
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A", "B")}})
    with patch.object(media_library, "_client", return_value=fake):
        res = delete_tasks([{"index": 0}])
    assert res["success"] is True
    assert [t["taskname"] for t in fake.update_calls[0]] == ["B"]


def test_delete_task_same_name_distinguished_by_savepath():
    """同名不同路径的两条任务必须只删指定的那条（键为 taskname+savepath）。"""
    tasks = [
        {"taskname": "同名", "savepath": "/A", "shareurl": "u1"},
        {"taskname": "同名", "savepath": "/B", "shareurl": "u2"},
    ]
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": tasks}})
    with patch.object(media_library, "_client", return_value=fake):
        res = delete_tasks([{"taskname": "同名", "savepath": "/B"}])
    assert res["success"] is True and res["deleted"] == 1
    assert [t["savepath"] for t in fake.update_calls[0]] == ["/A"]


def test_delete_task_missing_target_aborts_whole_batch():
    """有任一条定位失败 → 整批不写回（半途只删一部分比报错更危险）。"""
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A")}})
    with patch.object(media_library, "_client", return_value=fake):
        res = delete_tasks([
            {"taskname": "A", "savepath": "/媒体库/A"},
            {"taskname": "不存在", "savepath": "/x"},
        ])
    assert res["success"] is False and res["deleted"] == 0
    assert fake.update_calls == [], "定位失败时不得向 QAS 写回任何东西"
    assert "未做任何改动" in res["message"]


def test_delete_task_writeback_failure_not_reported_as_success():
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A")}},
                       update_resp={"success": False, "message": "配置更新失败"})
    with patch.object(media_library, "_client", return_value=fake):
        res = delete_tasks([{"taskname": "A", "savepath": "/媒体库/A"}])
    assert res["success"] is False and res["deleted"] == 0
    assert "配置更新失败" in res["message"]


def test_delete_task_qas_read_error_graceful():
    class Boom:
        def get_data(self):
            raise TimeoutError("connect timed out")

    with patch.object(media_library, "_client", return_value=Boom()):
        res = delete_tasks([{"taskname": "A"}])
    assert res["success"] is False
    assert "读取 QAS 任务失败" in res["message"]


def test_delete_task_rejects_empty_and_oversized_and_invalid():
    fake = _FakeClient()
    with patch.object(media_library, "_client", return_value=fake):
        assert delete_tasks([])["success"] is False
        assert delete_tasks([{}])["success"] is False
        many = [{"taskname": "T{}".format(i)} for i in range(MAX_TASK_DELETE_BATCH + 1)]
        res = delete_tasks(many)
    assert "最多删除" in res["message"]
    assert fake.update_calls == []


def test_delete_task_updates_qas_cache():
    """删完必须同步 transfer 的任务缓存，否则重新转存仍被判为「已转存」而跳过。"""
    import transfer
    transfer.init_qas_cache  # 触发模块导入
    transfer.add_to_qas("A")
    transfer.add_to_qas("B")
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A", "B")}})
    with patch.object(media_library, "_client", return_value=fake):
        delete_tasks([{"taskname": "A", "savepath": "/媒体库/A"}])
    assert transfer.is_in_qas("A") is False, "被删任务仍留在 QAS 缓存里"
    assert transfer.is_in_qas("B") is True, "不应误清未删除任务的缓存"
    transfer.remove_from_qas("B")


def test_remove_from_qas_semantics():
    import transfer
    transfer.add_to_qas("X")
    assert transfer.remove_from_qas("X") is True
    assert transfer.remove_from_qas("X") is False
    assert transfer.remove_from_qas("") is False


# ---------- 同名任务联动（issue #57） ----------
#
# 用户诉求：「给 OpenList 模块增加一个开关用于删除 OpenList 指定文件夹下的
# 资源库文件时同步删除同名自动夸克转存任务」。
# 这里锁住三件事，缺任何一环这个功能都会变成「看起来做了其实没通」或者更糟
# ——「看起来开了、其实删了不该删的」：
#   1. 默认关：不传 sync_tasks 时绝不碰 QAS 任务表；
#   2. 开了之后只删「同名」且落在路径范围内的任务，绝不误伤别的任务；
#   3. 联动失败/未命中**不得改写**网盘删除的结论（文件确实已经删了）。


def test_match_tasks_by_names_only_takes_same_name():
    """只挑同名的：既不能漏（功能没用），也不能把「都在同一个目录」当同名（误删）。"""
    tasks = [
        {"taskname": "示例作品A", "savepath": "/影视/电影"},
        {"taskname": "[已完结]示例作品A", "savepath": "/影视/电影"},
        {"taskname": "别的作品", "savepath": "/影视/电影"},
    ]
    hits = media_library.match_tasks_by_names(tasks, ["示例作品A"])
    assert [h["taskname"] for h in hits] == ["示例作品A", "[已完结]示例作品A"]
    assert "别的作品" not in [h["taskname"] for h in hits]


def test_match_tasks_by_names_respects_path_filter():
    """路径过滤必须收窄范围：网盘侧删的是某个目录下的文件，不该撞到别处的同名任务。"""
    tasks = [
        {"taskname": "示例作品A", "savepath": "/影视/电影"},
        {"taskname": "示例作品A", "savepath": "/下载/临时"},
    ]
    hits = media_library.match_tasks_by_names(tasks, ["示例作品A"], path_filter="/影视")
    assert [h["savepath"] for h in hits] == ["/影视/电影"]


def test_sync_tasks_by_names_no_match_writes_nothing():
    """一条都没撞上时**不得写 QAS 任务表**：一次多余的整表写回是纯风险。"""
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("别的作品")}})
    with patch.object(media_library, "_client", return_value=fake):
        res = media_library.sync_tasks_by_names(["示例作品A"], path_filter="/影视")
    assert res["success"] is True and res["matched"] == 0
    assert res["deleted"] == 0
    assert fake.update_calls == [], "没命中却写回了 QAS 任务表"
    assert "未匹配到同名任务" in res["message"]


def test_sync_tasks_by_names_deletes_only_matched():
    fake = _FakeClient(data_resp={"success": True, "data": {
        "tasklist": _tasks("示例作品A", "别的作品")}})
    with patch.object(media_library, "_client", return_value=fake):
        res = media_library.sync_tasks_by_names(["示例作品A"], path_filter="/媒体库")
    assert res["success"] is True and res["matched"] == 1 and res["deleted"] == 1
    assert [t["taskname"] for t in fake.update_calls[0]] == ["别的作品"]
    # 命中信息要能被前端/日志核对（否则用户看到「删了 1 条任务」不知道是哪条）
    assert res["matched_tasks"][0]["taskname"] == "示例作品A"


def test_sync_tasks_by_names_over_limit_aborts():
    """同名任务多到超上限时整批不动 —— 不做「删一半」的不可逆操作。"""
    names = ["同名作品-{}".format(i) for i in range(5)]
    fake = _FakeClient(data_resp={"success": True, "data": {
        "tasklist": _tasks(*names)}})
    with patch.object(media_library, "_client", return_value=fake):
        res = media_library.sync_tasks_by_names(["同名作品"], max_delete=2)
    assert res["success"] is False and res["deleted"] == 0
    assert fake.update_calls == []
    assert "上限" in res["message"]


def test_sync_tasks_by_names_reports_qas_down():
    class Boom:
        def get_data(self):
            raise TimeoutError("connect timed out")

    with patch.object(media_library, "_client", return_value=Boom()):
        res = media_library.sync_tasks_by_names(["A"])
    assert res["success"] is False and "读取 QAS 任务失败" in res["message"]


def test_openlist_delete_without_switch_never_touches_tasks():
    """默认（不传 sync_tasks）：删除路径不得触碰 QAS 任务链路。"""
    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "remove",
                      return_value={"success": True, "deleted": 1, "failed": 0,
                                    "results": [], "message": ""}), \
         patch.object(media_library, "sync_tasks_by_names") as m:
        res = media_library.delete_entries_by_source(
            [{"name": "示例作品A.mkv", "dir": "/影视/电影"}],
            sync_history=False, source="openlist")
    assert res["success"] is True
    m.assert_not_called()
    assert "tasks_result" not in res, "开关关闭时不应返回任务联动结果"


def test_openlist_delete_with_switch_syncs_same_name_tasks():
    """开了开关：按「条目名 + 当前目录范围」联动删同名任务，结果单独挂 tasks_result。"""
    seen = {}

    def fake_sync(names, path_filter="", tasks=None, max_delete=None):
        seen["names"] = list(names)
        seen["path_filter"] = path_filter
        return {"success": True, "deleted": 1, "failed": 0, "results": [],
                "matched": 1, "message": ""}

    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "remove",
                      return_value={"success": True, "deleted": 1, "failed": 0,
                                    "results": [], "message": ""}), \
         patch.object(media_library, "sync_tasks_by_names", side_effect=fake_sync):
        res = media_library.delete_entries_by_source(
            [{"name": "示例作品A.mkv", "dir": "/影视/电影"}],
            sync_history=False, source="openlist",
            sync_tasks=True, sync_tasks_path_filter="/影视/电影")
    assert res["deleted"] == 1 and res["success"] is True
    assert seen == {"names": ["示例作品A.mkv"], "path_filter": "/影视/电影"}
    assert res["tasks_result"]["matched"] == 1


def test_openlist_delete_sync_failure_does_not_flip_netdisk_result():
    """联动炸了也不能把「网盘已删成功」改写成失败 —— 那会让用户重复删除。"""
    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "remove",
                      return_value={"success": True, "deleted": 1, "failed": 0,
                                    "results": [], "message": ""}), \
         patch.object(media_library, "sync_tasks_by_names",
                      side_effect=RuntimeError("QAS 挂了")):
        res = media_library.delete_entries_by_source(
            [{"name": "示例作品A.mkv", "dir": "/影视/电影"}],
            sync_history=False, source="openlist", sync_tasks=True)
    assert res["success"] is True and res["deleted"] == 1, \
        "网盘删除已经落地，联动失败不得改写这个事实"
    assert res["tasks_result"]["success"] is False
    assert "QAS 挂了" in res["tasks_result"]["message"]


def test_openlist_delete_failure_does_not_sync_tasks():
    """网盘侧压根没删成时，绝不能顺手去删任务（否则用户以为只是删除失败）。"""
    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "remove",
                      return_value={"success": False, "deleted": 0, "failed": 1,
                                    "results": [], "message": "权限不足"}), \
         patch.object(media_library, "sync_tasks_by_names") as m:
        res = media_library.delete_entries_by_source(
            [{"name": "示例作品A.mkv", "dir": "/影视/电影"}],
            sync_history=False, source="openlist", sync_tasks=True)
    assert res["success"] is False
    m.assert_not_called()
    assert "tasks_result" not in res


def test_sync_library_entries_by_names_matches_in_scanned_dirs():
    """反方向（删任务 → 删网盘同名条目）：命中项必须来自扫描结果里的真实目录。"""
    def fake_scan(root, max_depth=2, max_dirs=40):
        return ({"/影视/电影": {"示例作品A", "别的作品"}}, False)

    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "scan_dir_names", side_effect=fake_scan), \
         patch.object(media_library.openlist, "remove",
                      return_value={"success": True, "deleted": 1, "failed": 0,
                                    "results": [], "message": ""}) as rm, \
         patch.object(media_library, "invalidate_library_cache"):
        res = media_library.sync_library_entries_by_names(
            ["示例作品A"], path_filter="/影视/电影")
    assert res["success"] is True and res["deleted"] == 1
    assert rm.call_args.args[0] == ["示例作品A"]
    assert rm.call_args.kwargs["dir_path"] == "/影视/电影"


def test_sync_library_entries_by_names_no_match_is_not_an_error():
    """网盘上没有同名条目是**正常结果**，不能报成失败（否则用户以为联动坏掉了）。"""
    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "scan_dir_names",
                      return_value=({"/影视/电影": {"别的作品"}}, False)), \
         patch.object(media_library.openlist, "remove") as rm:
        res = media_library.sync_library_entries_by_names(["示例作品A"], path_filter="/影视")
    assert res["success"] is True and res["deleted"] == 0
    rm.assert_not_called()
    assert "未匹配到同名条目" in res["message"]


def test_sync_library_entries_rejects_path_traversal():
    with patch.object(media_library.openlist, "is_configured", return_value=True):
        res = media_library.sync_library_entries_by_names(["A"], path_filter="/影视/../etc")
    assert res["success"] is False and ".." in res["message"]


# ---------- 路由：POST /api/qas/delete_tasks ----------

def test_route_delete_tasks_ok():
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A")}})
    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake):
        assert h._handle_library_post(
            "/api/qas/delete_tasks",
            {"tasks": [{"taskname": "A", "savepath": "/媒体库/A"}]}) is True
    assert h.sent["success"] is True and h.status == 200


def test_route_delete_tasks_sync_switch_default_off():
    """路由层：不传 sync_tasks 时绝不触发网盘联动（开关默认关的最后一公里）。"""
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A")}})
    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "sync_library_entries_by_names") as m:
        assert h._handle_library_post(
            "/api/qas/delete_tasks",
            {"tasks": [{"taskname": "A", "savepath": "/媒体库/A"}]}) is True
    assert h.sent["success"] is True
    m.assert_not_called()
    assert "tasks_result" not in h.sent


def test_route_delete_tasks_sync_switch_passes_names_and_scope():
    """路由层：勾了开关才把任务名与路径范围交给联动实现。"""
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A")}})
    h = _make_handler()
    seen = {}

    def fake_sync(names, path_filter=""):
        seen["names"] = list(names)
        seen["path_filter"] = path_filter
        return {"success": True, "deleted": 1, "failed": 0, "results": [],
                "matched_names": ["A"], "message": ""}

    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "sync_library_entries_by_names",
                      side_effect=fake_sync):
        assert h._handle_library_post(
            "/api/qas/delete_tasks",
            {"tasks": [{"taskname": "A", "savepath": "/媒体库/A"}],
             "sync_tasks": True, "sync_tasks_path_filter": "/媒体库"}) is True
    assert seen == {"names": ["A"], "path_filter": "/媒体库"}
    assert h.sent["tasks_result"]["deleted"] == 1
    assert h.sent["success"] is True, "联动结果不得改写任务删除的结论"


def test_route_delete_tasks_rejects_bad_sync_path():
    """联动范围来自前端，必须挡掉 `..`（否则会扫到浏览范围之外）。"""
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A")}})
    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake):
        h._handle_library_post(
            "/api/qas/delete_tasks",
            {"tasks": [{"taskname": "A", "savepath": "/媒体库/A"}],
             "sync_tasks": True, "sync_tasks_path_filter": "/媒体库/../etc"})
    assert h.status == 400 and ".." in h.sent["error"]


def test_route_delete_tasks_rejects_empty_and_invalid():
    h = _make_handler()
    assert h._handle_library_post("/api/qas/delete_tasks", {"tasks": []}) is True
    assert h.status == 400
    h2 = _make_handler()
    assert h2._handle_library_post("/api/qas/delete_tasks", {"tasks": [{"savepath": "/x"}]}) is True
    assert "taskname" in h2.sent["error"] and h2.status == 400


def test_route_delete_tasks_returns_502_when_qas_down():
    class Boom:
        def get_data(self):
            raise RuntimeError("QAS down")

    h = _make_handler()
    with patch.object(media_library, "_client", return_value=Boom()):
        assert h._handle_library_post("/api/qas/delete_tasks", {"tasks": [{"taskname": "A"}]}) is True
    assert h.status == 502 and h.sent["success"] is False


# ---------- 媒体库根目录（issue #27） ----------
#
# 需求：设置里能指定「资源根目录」，媒体库直接以它作为浏览起点，
# 而不是永远从网盘根 `/` 开始翻找。

def _settings_with_root(root):
    from config import ConfigManager
    cfg = ConfigManager.get_instance()
    return patch.object(cfg, "get_settings", return_value={"savepaths": {"library_root": root}})


def test_get_library_root_defaults_when_unset():
    assert media_library.get_library_root() == "/"


def test_get_library_root_reads_setting_and_normalizes():
    for raw, expect in [("/媒体库/", "/媒体库"), ("/影视", "/影视"), ("/", "/")]:
        with _settings_with_root(raw):
            assert media_library.get_library_root() == expect, raw


def test_get_library_root_falls_back_on_invalid():
    for bad in ["", "media", "  ", "/a/../b", None, 123]:
        with _settings_with_root(bad):
            assert media_library.get_library_root() == "/", bad


def test_route_library_uses_configured_root_when_path_absent():
    fake = _FakeClient(data_resp={"success": True, "data": {"list": []}})
    h = _make_handler()  # 无 path 参数
    with patch.object(media_library, "_client", return_value=fake), \
         _settings_with_root("/媒体库"):
        assert h._handle_library_get("/api/qas/library") is True
    assert h.status == 200
    assert fake.detail_path == "/媒体库", "未传 path 时应落到配置的媒体库根目录"


def test_route_library_explicit_path_wins_over_root():
    fake = _FakeClient(data_resp={"success": True, "data": {"list": []}})
    h = _make_handler(params={"path": "/其它"})
    with patch.object(media_library, "_client", return_value=fake), \
         _settings_with_root("/媒体库"):
        assert h._handle_library_get("/api/qas/library") is True
    assert fake.detail_path == "/其它", "显式 path 必须优先于配置根目录"


# ---------- 打开速度（issue #27：「每次打开都要等几秒」） ----------
#
# 上一轮只把「单次尝试超时」从 15s 收到 6s，但**重试次数没变**：
# utils.http_get 默认 retries=3、HTTP_RETRY_TOTAL=12s，于是单次目录请求最坏
# 6s + 退避 + 6s ≈ 12.5s（预算耗尽），全部压给用户。这里锁死三项对策：
#   1. 读接口不做底层重试（retries=1），失败快速返回；
#   2. 读预算必须大于单次尝试超时，否则「服务慢」会被误报成「不可达」；
#   3. 目录响应进短命缓存，重复打开不再打网络（但失败不缓存、删除后作废）。

def test_read_client_disables_inner_retry():
    """读接口不得让「内部重试 × 外层重试」相乘放大等待。"""
    from api_client import QASClient
    cfg = type("Cfg", (), {"qas": "http://127.0.0.1:1", "qas_token": "t"})()
    with patch.object(media_library.ConfigManager, "get_instance",
                      staticmethod(lambda: cfg)):
        c = media_library._read_client()
        assert isinstance(c, QASClient)
        assert c.retries == 1, "读接口必须只尝试一次（重试的是同一个卡住的上游）"
        assert media_library._client().retries is None, \
            "删除等破坏性操作应保留 utils 默认重试（需要更完整的等待）"


def test_read_budget_outlasts_single_attempt():
    """总预算必须 > 单次尝试超时，否则真实「服务慢」会被误报成「不可达」。"""
    assert media_library.LIBRARY_READ_BUDGET > media_library.LIBRARY_READ_TIMEOUT
    assert media_library.LIBRARY_READ_BUDGET <= 15, "总预算不应超过默认单次超时"


def test_qasclient_forwards_retries_to_http():
    """QASClient 必须把 retries 真正透传到底层 http_get/http_post。"""
    from unittest.mock import MagicMock
    import api_client
    client = media_library.QASClient("http://127.0.0.1:1", "t", timeout=3)
    client.retries = 1
    with patch.object(api_client, "http_get", MagicMock(return_value={})) as g:
        client.get("/data")
    assert g.call_args.kwargs.get("retries") == 1
    assert g.call_args.kwargs.get("timeout") == 3
    with patch.object(api_client, "http_post", MagicMock(return_value={})) as p:
        client.post("/update", {})
    assert p.call_args.kwargs.get("retries") == 1
    # 默认（未指定 retries）时不得给底层传 retries，保持原有 3 次重试语义
    plain = media_library.QASClient("http://127.0.0.1:1", "t", timeout=3)
    with patch.object(api_client, "http_get", MagicMock(return_value={})) as g2:
        plain.get("/data")
    assert "retries" not in g2.call_args.kwargs


def _resp(entries):
    return {"success": True, "data": {"fid": 0, "list": entries}}


def test_library_cache_hit_avoids_network():
    """重复打开同一目录必须命中缓存，不再打网络。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([{"fid": "1", "file_name": "A.mkv"}]))
    with patch.object(media_library, "_client", return_value=fake):
        r1 = list_library(path="/媒体库")
        r2 = list_library(path="/媒体库")
    assert r1["success"] is True and r2["entries"] == r1["entries"]
    assert r2["path"] == r1["path"], "缓存返回的结构必须与实时请求一致"
    calls = [c for c in [fake.detail_path] if c is not None]
    assert len(calls) == 1, "第二次调用不应再打网络（实测调用次数 {}）".format(len(calls))


def test_library_cache_can_be_bypassed():
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([]))
    with patch.object(media_library, "_client", return_value=fake):
        list_library(path="/x", use_cache=False)
        list_library(path="/x", use_cache=False)
    assert fake.detail_path == "/x"


def test_library_cache_not_written_on_failure():
    """只缓存成功结果：失败要能真的重试。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp={"success": False, "message": "未登录"})
    with patch.object(media_library, "_client", return_value=fake):
        r1 = list_library(path="/媒体库", use_cache=False)
    assert r1["success"] is False
    fake.savepath_resp = _resp([{"fid": "1", "file_name": "A.mkv"}])
    with patch.object(media_library, "_client", return_value=fake):
        r2 = list_library(path="/媒体库")
    assert r2["success"] is True and len(r2["entries"]) == 1, "失败不得进缓存"


def test_library_cache_invalidated_after_delete():
    """删除后必须作废缓存，否则用户删完再看同一目录还会看到已删条目。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([{"fid": "1", "file_name": "A.mkv"}]),
                       delete_resp={"success": True, "code": 0})
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library, "delete_history_item", return_value=False):
        list_library(path="/媒体库")           # 写入缓存
        delete_entries([{"fid": "1", "name": "A"}])
    assert media_library._lib_cache_get("path:/媒体库") is None, "删除后缓存未作废"


def test_library_cache_expires(monkeypatch):
    """TTL 到期后必须重新打网络（缓存只是「短命」，不能变成长期陈旧视图）。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([]))
    with patch.object(media_library, "_client", return_value=fake):
        list_library(path="/媒体库")
        assert media_library._lib_cache_get("path:/媒体库") is not None
        # 直接把缓存时间戳往回拨，模拟 TTL 到期
        media_library._lib_cache["path:/媒体库"]["ts"] -= media_library.LIBRARY_CACHE_TTL + 1
        assert media_library._lib_cache_get("path:/媒体库") is None


def test_route_library_refresh_param_bypasses_cache():
    """前端「刷新」按钮带 refresh=1，必须绕过后端缓存。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([]))
    h = _make_handler(params={"path": "/媒体库", "refresh": "1"})
    with patch.object(media_library, "_client", return_value=fake):
        h._handle_library_get("/api/qas/library")
        assert h.status == 200
        assert media_library._lib_cache_get("path:/媒体库") is None, \
            "refresh=1 的响应不应写回缓存（否则普通打开会拿到「刚强制刷新过」的旧数据）"
        # 普通打开（不带 refresh）仍应写入缓存
        h2 = _make_handler(params={"path": "/媒体库"})
        h2._handle_library_get("/api/qas/library")
        assert media_library._lib_cache_get("path:/媒体库") is not None


# ---------------------------------------------------------------- 合并 OpenList 数据源后的缓存语义
# 背景：「媒体库打开慢」(#42) 与「接入 OpenList 数据源」(#43) 两轮改动在同一个
# 目录读取链路上交汇 —— 前者引入后端 30s 目录缓存与 refresh 绕过，后者把目录
# 读取改为「OpenList 优先、失败回落 QAS」。合并后必须保证两件事不互相抵消：
#   1. refresh=1（前端刷新）在 OpenList 与 QAS 两级上都要生效；
#   2. 回落 QAS 时同样遵守 refresh 语义，不能「装了 OpenList 之后 QAS 缓存就不受控」。


def test_list_library_entries_refresh_bypasses_qas_cache_on_fallback():
    """OpenList 未配置 → 回落 QAS；此时 refresh 仍必须能绕过 QAS 目录缓存。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([]))
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library.openlist, "is_configured", return_value=False):
        media_library.list_library_entries(path="/媒体库")             # 普通打开 → 写缓存
        assert media_library._lib_cache_get("path:/媒体库") is not None
        media_library.invalidate_library_cache()
        res = media_library.list_library_entries(path="/媒体库", refresh=True)
        assert res["source"] == "qas" and res["fallback"] is False
        assert media_library._lib_cache_get("path:/媒体库") is None, \
            "refresh=True 时 QAS 侧不应写回缓存（否则下次普通打开会拿到强制刷新的旧数据）"


def test_list_library_entries_qas_fallback_uses_cache_without_refresh():
    """回落 QAS 时的普通打开仍应命中缓存（缓存不是被 OpenList 的引入废掉的）。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([]))
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library.openlist, "is_configured", return_value=False):
        media_library.list_library_entries(path="/媒体库")
        res = media_library.list_library_entries(path="/媒体库")
    assert res["success"] and res["source"] == "qas"
    assert media_library._lib_cache_get("path:/媒体库") is not None


def test_list_library_entries_openlist_failure_falls_back_and_labels_source():
    """OpenList 配了但不可用 → 回落 QAS，并如实标注 fallback=True（用户要能归因）。"""
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([]))
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "list_dir",
                      return_value={"success": False, "message": "OpenList 不可达"}):
        res = media_library.list_library_entries(path="/媒体库", refresh=True)
    assert res["success"] is True
    assert res["source"] == "qas" and res["fallback"] is True


def test_fallback_forwards_timeout_kind_and_backoff_flag():
    """回落时必须把「失败归类」与「是否在自动退避」一起带给前端（issue #47）。

    只有一句「已回落」，前端无从区分「慢（等一会自动恢复）」与「配错（去改设置）」；
    而退避窗口是**自动恢复期**，前端必须据此告诉用户「不用改配置」——
    否则用户会在 20 秒的自动恢复期里跑去翻设置，把本来正确的配置改坏。
    """
    media_library.invalidate_library_cache()
    fake = _FakeClient(savepath_resp=_resp([]))
    with patch.object(media_library, "_client", return_value=fake), \
         patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "list_dir",
                      return_value={"success": False, "message": "OpenList 请求失败: timed out",
                                    "kind": "timeout", "backoff": True}):
        res = media_library.list_library_entries(path="/媒体库", refresh=True)
    assert res["fallback"] is True
    assert res["fallback_kind"] == "timeout", "必须透传失败归类（超时 ≠ 配错）"
    assert res["fallback_backoff"] is True, "必须透传退避标记（自动恢复 ≠ 需要改配置）"


def test_openlist_success_still_reports_openlist_source():
    """OpenList 成功时必须如实标 source=openlist 且 fallback=False（不能被缓存改写）。"""
    media_library.invalidate_library_cache()
    with patch.object(media_library.openlist, "is_configured", return_value=True), \
         patch.object(media_library.openlist, "list_dir",
                      return_value={"success": True, "path": "/媒体库",
                                    "entries": [{"name": "电影", "dir": True}]}):
        res = media_library.list_library_entries(path="/媒体库", refresh=True)
    assert res["source"] == "openlist" and res["fallback"] is False
    assert res["entries"] == [{"name": "电影", "dir": True}]
