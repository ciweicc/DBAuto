#!/usr/bin/env python3
"""
DBAuto MCP Server — 将豆瓣自动转存系统的能力暴露为 MCP Tools
供 Claude Desktop / Cursor / VS Code 等 AI 客户端调用

两种运行模式:
  1. stdio 模式（默认）: python mcp_server.py
     适用于 Claude Desktop 等本地客户端
  2. SSE 模式: python mcp_server.py --sse --port 8765
     适用于远程/网络客户端

配置示例（Claude Desktop claude_desktop_config.json）:
{
  "mcpServers": {
    "dbauto": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/DBAuto",
      "env": {
        "DATA_DIR": "/data/douban-history"
      }
    }
  }
}
"""
import sys
import os
import json
import asyncio
import argparse

# 确保 app_modules 在搜索路径中
_app_modules = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_modules")
if _app_modules not in sys.path:
    sys.path.insert(0, _app_modules)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions

# 导入项目模块
from config import CATEGORIES, load_config, load_settings, ConfigManager
from douban import get_douban_list
from transfer import (
    search_pansou,
    transfer_one as _do_transfer_one,
    is_transfer_running,
    transfer_status,
    transfer_lock,
    check_expired_tasks,
    validate_share_link,
)
from storage import load_history, load_exec_history

server = Server("dbauto")


# ---------------------------------------------------------------------------
# 同步业务函数（在 asyncio.to_thread 中调用）
# ---------------------------------------------------------------------------

def _sync_search_and_transfer(title, savepath, category):
    """搜索资源并转存单部影视"""
    # 1. 搜索资源
    results = search_pansou(title, category)
    if not results:
        return {"status": "not_found", "msg": "未找到资源: {}".format(title)}

    # 2. 选取第一个有效资源
    chosen = results[0]
    url = chosen.get("url", "")
    note = chosen.get("title", chosen.get("note", ""))
    if not url:
        return {"status": "no_url", "msg": "搜索结果无有效链接", "results": results[:5]}

    # 3. 验证链接
    valid, msg = validate_share_link(url)
    if not valid:
        # 尝试其他结果
        for r in results[1:5]:
            alt_url = r.get("url", "")
            if not alt_url:
                continue
            valid2, msg2 = validate_share_link(alt_url)
            if valid2:
                url = alt_url
                note = r.get("title", r.get("note", ""))
                valid = True
                break
        if not valid:
            return {"status": "invalid_link", "msg": "链接无效: {}".format(msg), "results": results[:5]}

    # 4. 执行转存
    res = _do_transfer_one(title, url, savepath, "", "", category)
    res["searched_url"] = url
    res["resource_name"] = note
    return res


def _sync_transfer_by_url(title, shareurl, savepath, category):
    """通过分享链接直接转存"""
    valid, msg = validate_share_link(shareurl)
    if not valid:
        return {"status": "invalid_link", "msg": "链接无效: {}".format(msg)}
    return _do_transfer_one(title, shareurl, savepath, "", "", category)


def _sync_get_transfer_status():
    with transfer_lock:
        return {
            "running": transfer_status.get("running", False),
            "start_time": transfer_status.get("start_time"),
            "stats": transfer_status.get("stats", {}),
            "summary": transfer_status.get("summary"),
        }


def _sync_get_history(category=None):
    history = load_history()
    items = []
    for title, info in history.items():
        if category and info.get("category") != category:
            continue
        items.append({"title": title, **info})
    return {"total": len(items), "items": items}


def _sync_get_exec_history(limit=20):
    data = load_exec_history()
    return data[:limit] if isinstance(data, list) else []


def _sync_check_expired(limit=50):
    expired = check_expired_tasks(limit)
    return {
        "expired_count": len(expired) if isinstance(expired, list) else 0,
        "expired": expired if isinstance(expired, list) else [],
    }


def _sync_get_dashboard_stats():
    from datetime import datetime, timezone, timedelta
    TZ = timezone(timedelta(hours=8))
    now = datetime.now(TZ)
    today_str = now.strftime("%Y-%m-%d")

    history = load_history()
    today_count = sum(1 for v in history.values() if v.get("date") == today_str)

    week_ago = now - timedelta(days=7)
    week_ok = 0
    week_fail = 0
    for v in history.values():
        try:
            d = datetime.strptime(v.get("date", ""), "%Y-%m-%d")
            if d >= week_ago:
                if v.get("status") in ("ok", "done"):
                    week_ok += 1
                else:
                    week_fail += 1
        except (ValueError, TypeError):
            pass

    exec_hist = load_exec_history()
    last_status = None
    last_time = None
    if exec_hist:
        last = exec_hist[0]
        last_status = last.get("status")
        last_time = last.get("time")

    return {
        "today_count": today_count,
        "week_ok": week_ok,
        "week_fail": week_fail,
        "week_total": week_ok + week_fail,
        "total_history": len(history),
        "last_status": last_status,
        "last_time": last_time,
    }


def _sync_get_schedule():
    from scheduler import _next_fire_time, _now_local
    settings = load_settings()
    t = settings.get("transfer", {})
    e = settings.get("expired_check", {})

    def _fmt_next(time_str, cron_str):
        if not time_str and not cron_str:
            return None
        nft = _next_fire_time(time_str, cron_str)
        return nft.strftime("%Y-%m-%d %H:%M") if nft else None

    return {
        "transfer": {
            "enabled": t.get("enabled", False),
            "time": t.get("time", ""),
            "cron": t.get("cron", ""),
            "limit": t.get("limit", 5),
            "next_run": _fmt_next(t.get("time"), t.get("cron")) if t.get("enabled") else None,
        },
        "expired_check": {
            "enabled": e.get("enabled", False),
            "time": e.get("time", ""),
            "cron": e.get("cron", ""),
            "next_run": _fmt_next(e.get("time"), e.get("cron")) if e.get("enabled") else None,
        },
    }


# ---------------------------------------------------------------------------
# MCP Tool 定义
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_categories",
            description="获取豆瓣榜单分类列表，包含电影（热门/最新/高分/冷门佳片）、电视剧、综艺的可用分类和子类型",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_douban_list",
            description="从豆瓣获取影视榜单数据。返回标题、评分、年份等信息",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "榜单路径: movie/hot(热门电影), movie/latest(最新电影), movie/top(豆瓣高分), movie/underrated(冷门佳片), tv/drama(热门剧集), tv/variety(热门综艺)",
                    },
                    "sub_type": {
                        "type": "string",
                        "description": "子类型。电影: 全部/华语/欧美/韩国/日本; 电视剧: 综合/国产剧/欧美剧/日剧/韩剧/动画/纪录片; 综艺: 综合/国内/国外",
                        "default": "全部",
                    },
                    "limit": {"type": "integer", "description": "返回数量上限", "default": 20},
                    "min_rating": {"type": "number", "description": "最低评分(0-10)", "default": 0},
                    "sort_by": {"type": "string", "description": "排序: rating(评分) / year(年份)", "default": "rating"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="search_resources",
            description="通过 PanSou 搜索夸克网盘资源。返回资源标题和分享链接",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，如电影名、电视剧名"},
                },
                "required": ["keyword"],
            },
        ),
        Tool(
            name="transfer_one",
            description="搜索并转存单部影视资源到夸克网盘。系统自动搜索资源、验证链接、执行转存",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "影视名称"},
                    "savepath": {"type": "string", "description": "保存路径，如 /电影/2024", "default": "/批量转存/MCP转存"},
                    "category": {"type": "string", "description": "分类: movie(电影) / tv(电视剧/综艺)", "default": "movie"},
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="transfer_by_url",
            description="通过分享链接直接转存资源到夸克网盘（已知链接时使用）",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务名称"},
                    "shareurl": {"type": "string", "description": "夸克网盘分享链接"},
                    "savepath": {"type": "string", "description": "保存路径", "default": "/批量转存/MCP转存"},
                    "category": {"type": "string", "description": "分类: movie / tv", "default": "movie"},
                },
                "required": ["title", "shareurl"],
            },
        ),
        Tool(
            name="get_transfer_status",
            description="获取当前转存任务的状态和进度",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_history",
            description="获取转存历史记录列表",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "按分类筛选: movie/tv/variety，不填返回全部"},
                },
            },
        ),
        Tool(
            name="get_exec_history",
            description="获取执行历史记录（转存和失效检测的执行日志）",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回数量", "default": 20},
                },
            },
        ),
        Tool(
            name="check_expired",
            description="检测已转存任务的分享链接是否失效。返回失效的任务列表",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "检测数量上限", "default": 50},
                },
            },
        ),
        Tool(
            name="get_dashboard_stats",
            description="获取仪表盘统计数据：今日转存量、近7天统计、上次执行状态等",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_schedule",
            description="获取定时任务设置和下次执行时间",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "get_categories":
            result = CATEGORIES

        elif name == "get_douban_list":
            result = await asyncio.to_thread(
                get_douban_list,
                arguments.get("path", ""),
                arguments.get("sub_type", "全部"),
                arguments.get("limit", 20),
                min_rating=arguments.get("min_rating", 0),
                sort_by=arguments.get("sort_by", "rating"),
            )

        elif name == "search_resources":
            result = await asyncio.to_thread(
                search_pansou, arguments.get("keyword", ""), "movie"
            )

        elif name == "transfer_one":
            if is_transfer_running():
                result = {"error": "当前有转存任务正在进行，请稍后再试"}
            else:
                result = await asyncio.to_thread(
                    _sync_search_and_transfer,
                    arguments.get("title", ""),
                    arguments.get("savepath", "/批量转存/MCP转存"),
                    arguments.get("category", "movie"),
                )

        elif name == "transfer_by_url":
            if is_transfer_running():
                result = {"error": "当前有转存任务正在进行，请稍后再试"}
            else:
                result = await asyncio.to_thread(
                    _sync_transfer_by_url,
                    arguments.get("title", ""),
                    arguments.get("shareurl", ""),
                    arguments.get("savepath", "/批量转存/MCP转存"),
                    arguments.get("category", "movie"),
                )

        elif name == "get_transfer_status":
            result = await asyncio.to_thread(_sync_get_transfer_status)

        elif name == "get_history":
            result = await asyncio.to_thread(
                _sync_get_history, arguments.get("category")
            )

        elif name == "get_exec_history":
            result = await asyncio.to_thread(
                _sync_get_exec_history, arguments.get("limit", 20)
            )

        elif name == "check_expired":
            result = await asyncio.to_thread(
                _sync_check_expired, arguments.get("limit", 50)
            )

        elif name == "get_dashboard_stats":
            result = await asyncio.to_thread(_sync_get_dashboard_stats)

        elif name == "get_schedule":
            result = await asyncio.to_thread(_sync_get_schedule)

        else:
            result = {"error": "未知工具: {}".format(name)}

    except Exception as e:
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2, default=str))]


# ---------------------------------------------------------------------------
# 运行入口
# ---------------------------------------------------------------------------

async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="dbauto",
                server_version="1.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


async def run_sse(port):
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="dbauto",
                    server_version="1.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
        return None

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
    print("DBAuto MCP Server (SSE) running on http://0.0.0.0:{}/sse".format(port), file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=port)


def main():
    parser = argparse.ArgumentParser(description="DBAuto MCP Server")
    parser.add_argument("--sse", action="store_true", help="使用 SSE 模式（默认 stdio）")
    parser.add_argument("--port", type=int, default=8765, help="SSE 模式端口")
    args = parser.parse_args()

    # 初始化配置
    ConfigManager.get_instance()
    load_config()

    if args.sse:
        asyncio.run(run_sse(args.port))
    else:
        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
