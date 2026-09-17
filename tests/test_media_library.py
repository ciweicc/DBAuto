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

    def get_data(self):
        return self.data_resp

    def get_savepath_detail(self, path=None, fid=None):
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
        res = list_library(path="/媒体库")
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

    def fake_client(timeout=15):
        seen.append(timeout)
        return _FakeClient()

    with patch.object(media_library, "_client", side_effect=fake_client), \
         patch.object(media_library, "delete_history_item", return_value=False):
        list_tasks()
        assert seen[-1] == media_library.LIBRARY_READ_TIMEOUT
        list_library(path="/")
        assert seen[-1] == media_library.LIBRARY_READ_TIMEOUT
        delete_entries([{"fid": "1", "name": "A"}])
        assert seen[-1] == 15, "删除是破坏性操作，不应被收窄超时"


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


# ---------- 路由：POST /api/qas/delete_tasks ----------

def test_route_delete_tasks_ok():
    fake = _FakeClient(data_resp={"success": True, "data": {"tasklist": _tasks("A")}})
    h = _make_handler()
    with patch.object(media_library, "_client", return_value=fake):
        assert h._handle_library_post(
            "/api/qas/delete_tasks",
            {"tasks": [{"taskname": "A", "savepath": "/媒体库/A"}]}) is True
    assert h.sent["success"] is True and h.status == 200


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
