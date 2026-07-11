#!/bin/bash
set -e

mkdir -p "$DATA_DIR"

# 支持密码重置: docker run --rm ... --reset-password 新密码
if [ "$1" = "--reset-password" ]; then
    shift
    exec python reset_password.py "$@"
fi

# 支持 MCP 服务模式: docker run ... --mcp [--sse] [--port 8765]
if [ "$1" = "--mcp" ]; then
    shift
    exec python mcp_server.py "$@"
fi

exec python main.py
