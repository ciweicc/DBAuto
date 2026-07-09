# routes_ai.py — AI 诊断 & 对话 & 转存路由 Mixin
from ai_assistant import (
    diagnose, test_connection, is_ai_enabled, chat,
    _detect_transfer_intent, _extract_transfer_info,
)
from storage import get_exec_record_by_id
from transfer import is_transfer_running
from utils import log
from validator import validate_string


class AIRouteMixin:
    """AI 相关路由"""

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

            # 检测最后一条用户消息是否有转存意图
            last_user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user_msg = m.get("content", "")
                    break

            action = None
            if last_user_msg and _detect_transfer_intent(last_user_msg):
                log("检测到转存意图: {}".format(last_user_msg))
                extract_result = _extract_transfer_info(last_user_msg)
                if extract_result.get("success"):
                    action = {
                        "type": "transfer",
                        "title": extract_result["title"],
                        "category": extract_result["category"],
                    }
                    log("提取转存信息: title={}, category={}".format(
                        action["title"], action["category"]))
                else:
                    log("转存信息提取失败: {}".format(extract_result.get("error", "")))

            # 正常对话
            result = chat(messages)

            # 如果有转存 action，附加到返回结果中
            if action and result.get("success"):
                result["action"] = action

            self._send_json(result)
            return True

        if route == "/api/ai/transfer":
            # AI 对话中提取的转存请求，前端点击按钮后调用
            title = body.get("title", "").strip()
            category = body.get("category", "movie").strip()

            ok, msg = validate_string(title, min_len=1, max_len=200, allow_empty=False)
            if not ok:
                self._send_json({"success": False, "error": "title: {}".format(msg)}, 400)
                return True

            if is_transfer_running():
                self._send_json({"success": False, "error": "当前有转存任务正在进行，请稍后再试"})
                return True

            log("AI 对话触发转存: title={}, category={}".format(title, category))

            # 返回提取的信息，让前端去调用 /api/transfer_one
            self._send_json({
                "success": True,
                "title": title,
                "category": category,
            })
            return True

        if route == "/api/ai/test":
            result = test_connection()
            self._send_json(result)
            return True

        return False
