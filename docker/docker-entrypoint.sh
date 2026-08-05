#!/bin/bash
set -e

mkdir -p "$DATA_DIR"

# 支持密码重置: docker run --rm ... --reset-password 新密码
if [ "$1" = "--reset-password" ]; then
    shift
    exec python reset_password.py "$@"
fi

exec python main.py
