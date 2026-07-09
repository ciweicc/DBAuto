# ai_assistant.py — AI 错误诊断引擎
# 兼容 OpenAI API 格式（DeepSeek / Moonshot / 智谱 / 通义千问 / OpenAI 等）
import json
import logging
import requests
from config import ConfigManager

logger = logging.getLogger(__name__)


def _get_ai_config():
    """从系统配置中获取 AI 供应商配置"""
    cfg = ConfigManager.get_instance().get_config()
    return {
        "base_url": cfg.get("ai_base_url", ""),
        "api_key": cfg.get("ai_api_key", ""),
        "model": cfg.get("ai_model", "deepseek-chat"),
    }


def is_ai_enabled():
    """检查 AI 诊断是否已配置"""
    c = _get_ai_config()
    return bool(c["base_url"] and c["api_key"])


def _build_diagnosis_prompt(exec_record):
    """根据执行历史记录构建诊断 prompt"""
    detail = exec_record.get("detail", "")
    exec_type = exec_record.get("type", "")
    status = exec_record.get("status", "")
    data = exec_record.get("data", {}) or {}

    # 收集失败结果
    failed_items = []
    all_items = []

    if exec_type == "transfer" and data.get("results"):
        for r in data["results"]:
            all_items.append(r)
            if r.get("status") not in ("ok", "done", "skipped", "exists"):
                failed_items.append(r)
    elif exec_type == "expired_check" and data.get("expired"):
        for r in data.get("expired", []):
            failed_items.append({"title": r.get("title", ""), "status": "expired", "msg": r.get("msg", "")})

    # 构建上下文
    context_lines = [
        "## 系统信息",
        "这是一个「豆瓣榜单自动追踪转存」系统，工作流程：",
        "1. 从豆瓣 API 获取热门影视榜单",
        "2. 通过 PanSou 搜索夸克网盘资源链接",
        "3. 调用 QAS (夸克自动转存) 将资源转存到用户网盘",
        "4. 定期检测已转存链接是否失效",
        "",
        "## 本次执行记录",
        f"- 类型: {exec_type}",
        f"- 状态: {status}",
        f"- 概要: {detail}",
        f"- 失败数量: {len(failed_items)}",
        "",
        "## 失败详情",
    ]

    if failed_items:
        for item in failed_items[:20]:
            title = item.get("title", "未知")
            err_status = item.get("status", "")
            msg = item.get("msg", "")
            context_lines.append(f"- 标题: {title} | 状态: {err_status} | 信息: {msg}")
    else:
        context_lines.append("(无明确失败项)")

    context_lines.extend([
        "",
        "## 常见错误原因参考",
        "- not_found: PanSou 未搜索到资源，可能是影片太新/太冷门，或搜索服务不可用",
        "- error: QAS 转存失败，可能是 Cookie 过期、分享链接已失效、网络异常、保存目录无权限",
        "- expired: 分享链接已被分享者删除或举报下架",
        "- invalid: 新链接验证不通过，可能是提取码错误或链接已失效",
        "- update_fail: QAS API 调用失败，可能是 Token 过期或 QAS 服务不可用",
        "",
        "## 请你作为运维专家，分析以下问题：",
        "1. **根因分析**：判断最可能的失败原因",
        "2. **修复建议**：给出具体的操作步骤",
        "3. **预防措施**：如何避免此类问题再次发生",
        "",
        "请用简洁的中文回答，使用 Markdown 格式。",
    ])

    return "\n".join(context_lines)


def diagnose(exec_record):
    """
    调用 AI 分析执行记录中的错误

    Args:
        exec_record: 执行历史记录 dict，包含 type/detail/status/data 等字段

    Returns:
        dict: {"success": bool, "diagnosis": str, "error": str}
    """
    ai_config = _get_ai_config()
    if not (ai_config["base_url"] and ai_config["api_key"]):
        return {
            "success": False,
            "error": "AI 诊断未配置，请在设置页面填写 AI 供应商信息",
        }

    prompt = _build_diagnosis_prompt(exec_record)

    # 构建 OpenAI 兼容请求
    url = ai_config["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ai_config["api_key"],
    }
    payload = {
        "model": ai_config["model"],
        "messages": [
            {
                "role": "system",
                "content": "你是一个网盘自动化转存系统的运维诊断助手。用户会提供转存任务的执行记录和失败信息，你需要分析根因并给出可操作的修复建议。回答要简洁实用，用中文 Markdown 格式。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.3,
        "max_tokens": 1500,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"success": False, "error": "AI 返回为空"}
        diagnosis = choices[0].get("message", {}).get("content", "").strip()
        if not diagnosis:
            return {"success": False, "error": "AI 未返回有效内容"}
        return {"success": True, "diagnosis": diagnosis}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "AI 请求超时（60s），请稍后重试"}
    except requests.exceptions.ConnectionError as e:
        return {"success": False, "error": f"无法连接 AI 服务: {e}"}
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "?"
        try:
            err_body = e.response.json() if e.response else {}
            err_msg = err_body.get("error", {}).get("message", str(e))
        except Exception:
            err_msg = str(e)
        return {"success": False, "error": f"AI 服务返回错误 ({status_code}): {err_msg}"}
    except Exception as e:
        logger.exception("AI 诊断异常")
        return {"success": False, "error": f"诊断异常: {e}"}


def chat(messages):
    """
    AI 多轮对话

    Args:
        messages: 对话历史列表, [{"role": "user", "content": "..."}, ...]

    Returns:
        dict: {"success": bool, "reply": str, "error": str}
    """
    ai_config = _get_ai_config()
    if not (ai_config["base_url"] and ai_config["api_key"]):
        return {
            "success": False,
            "error": "AI 未配置，请在设置页面填写 AI 供应商信息",
        }

    if not messages or not isinstance(messages, list):
        return {"success": False, "error": "消息内容不能为空"}

    # 限制历史消息数量，防止 token 过多
    if len(messages) > 20:
        messages = messages[-20:]

    url = ai_config["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ai_config["api_key"],
    }

    system_prompt = {
        "role": "system",
        "content": (
            "你是「豆瓣自动转存」系统的 AI 助手。这个系统的功能是：\n"
            "1. 从豆瓣获取热门影视榜单\n"
            "2. 通过 PanSou 搜索夸克网盘资源\n"
            "3. 调用 QAS (夸克自动转存) 转存到用户网盘\n"
            "4. 定期检测失效链接并自动修复\n\n"
            "你可以帮助用户解答系统使用问题、排查错误、提供建议。"
            "回答要简洁实用，用中文，可以使用 Markdown 格式。"
        ),
    }

    payload = {
        "model": ai_config["model"],
        "messages": [system_prompt] + messages,
        "temperature": 0.5,
        "max_tokens": 2000,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"success": False, "error": "AI 返回为空"}
        reply = choices[0].get("message", {}).get("content", "").strip()
        if not reply:
            return {"success": False, "error": "AI 未返回有效内容"}
        return {"success": True, "reply": reply}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "AI 请求超时（60s），请稍后重试"}
    except requests.exceptions.ConnectionError as e:
        return {"success": False, "error": f"无法连接 AI 服务: {e}"}
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "?"
        try:
            err_body = e.response.json() if e.response else {}
            err_msg = err_body.get("error", {}).get("message", str(e))
        except Exception:
            err_msg = str(e)
        return {"success": False, "error": f"AI 服务返回错误 ({status_code}): {err_msg}"}
    except Exception as e:
        logger.exception("AI 对话异常")
        return {"success": False, "error": f"对话异常: {e}"}


# 转存意图识别的关键词
_TRANSFER_KEYWORDS = ["转存", "存", "下载", "保存到网盘", "帮我找", "搜一下", "搜索"]


def _detect_transfer_intent(message):
    """快速检测用户消息是否包含转存意图"""
    for kw in _TRANSFER_KEYWORDS:
        if kw in message:
            return True
    return False


def _extract_transfer_info(message):
    """
    用 AI 从用户消息中提取转存信息（片名 + 类型）

    Returns:
        dict: {"success": bool, "title": str, "category": str, "error": str}
    """
    ai_config = _get_ai_config()
    if not (ai_config["base_url"] and ai_config["api_key"]):
        return {"success": False, "error": "AI 未配置"}

    url = ai_config["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ai_config["api_key"],
    }

    prompt = (
        "从以下用户消息中提取要转存的影视名称和类型。\n"
        "规则：\n"
        '1. 只返回 JSON 格式：{"title": "影视名称", "category": "movie 或 tv"}\n'
        "2. movie = 电影，tv = 电视剧/综艺/动漫\n"
        "3. 如果无法确定是电影还是电视剧，默认 movie\n"
        "4. title 只保留影视名称，不要包含其他描述\n"
        "5. 不要输出任何其他内容，只输出 JSON\n\n"
        f"用户消息：{message}"
    )

    payload = {
        "model": ai_config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 200,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"success": False, "error": "AI 返回为空"}
        content = choices[0].get("message", {}).get("content", "").strip()
        # 尝试提取 JSON
        import re
        json_match = re.search(r'\{[^}]+\}', content)
        if not json_match:
            return {"success": False, "error": "AI 未返回有效信息"}
        info = json.loads(json_match.group())
        title = info.get("title", "").strip()
        category = info.get("category", "movie").strip()
        if not title:
            return {"success": False, "error": "未能提取到影视名称"}
        if category not in ("movie", "tv"):
            category = "movie"
        return {"success": True, "title": title, "category": category}
    except Exception as e:
        logger.exception("提取转存信息异常")
        return {"success": False, "error": f"提取异常: {e}"}


def test_connection():
    """测试 AI 供应商连接是否正常"""
    ai_config = _get_ai_config()
    if not (ai_config["base_url"] and ai_config["api_key"]):
        return {"success": False, "error": "未配置 AI 服务"}

    url = ai_config["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ai_config["api_key"],
    }
    payload = {
        "model": ai_config["model"],
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 10,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        return {"success": True, "message": f"连接正常，模型: {ai_config['model']}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
