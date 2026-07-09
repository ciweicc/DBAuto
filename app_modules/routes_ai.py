# routes_ai.py — AI 诊断路由 Mixin
from ai_assistant import diagnose, test_connection, is_ai_enabled, chat
from storage import get_exec_record_by_id
from utils import log
from validator import validate_string


class AIRouteMixin:
    """AI 诊断相关路由"""

    def _handle_ai_get(self, route):
        if route == "/api/ai/status":
            self._send_json({"enabled": is_ai_enabled()})
            return True

        return False

    def _handle_ai_post(self, route, body):
        if route == "/api/ai/diagnose":
            record_id = body.get("id", "").strip()

            ok, msg = validate_string(record_id, min_len=1, max_len=50, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "error": "id: {}".format(msg)}, 400)
                return True

            # 从执行历史中查找记录
            target = get_exec_record_by_id(record_id)

            if not target:
                self._send_json({"success": False, "error": "未找到执行记录: {}".format(record_id)}, 404)
                return True

            log("AI 诊断请求: id={}, type={}, status={}".format(record_id, target.get("type"), target.get("status")))

            result = diagnose(target)
            self._send_json(result)
            return True

        if route == "/api/ai/chat":
            messages = body.get("messages", [])
            if not isinstance(messages, list) or not messages:
                self._send_json({"success": False, "error": "消息不能为空"}, 400)
                return True

            # 验证每条消息
            for msg in messages:
                content = msg.get("content", "")
                ok, msg_err = validate_string(content, min_len=1, max_len=2000, allow_empty=False)
                if not ok:
                    self._send_json({"success": False, "error": "消息内容: {}".format(msg_err)}, 400)
                    return True

            result = chat(messages)
            self._send_json(result)
            return True

        if route == "/api/ai/test":
            result = test_connection()
            self._send_json(result)
            return True

        return False
