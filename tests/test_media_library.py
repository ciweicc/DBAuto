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
from media_library import list_tasks, list_library, delete_entries, MAX_DELETE_BATCH


class _FakeClient:
    """按调用记录返回预置响应的 QAS 假客户端。"""

    def __init__(self, data_resp=None, savepath_resp=None, delete_resp=None):
        self.data_resp = data_resp or {"success": True, "data": {"tasklist": []}}
        self.savepath_resp = savepath_resp or {"success": True, "data": {"fid": 0, "list": []}}
        self.delete_resp = delete_resp or {"success": True, "code": 0}
        self.delete_calls = []

    def get_data(self):
        return self.data_resp

    def get_savepath_detail(self, path=None, fid=None):
        return self.savepath_resp

    def delete_file(self, path=None, fid=None):
        self.delete_calls.append({"path": path, "fid": fid})
        return self.delete_resp


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
