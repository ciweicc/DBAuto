#!/bin/bash
set -e

# 数据目录，来自环境变量 DATA_DIR（compose 中固定为 /data/douban-history），给出默认值以防未注入。
DATA_DIR="${DATA_DIR:-/data/douban-history}"

# 确保数据目录存在（宿主机未预建时也能启动）。
mkdir -p "$DATA_DIR"

# 根据启动参数决定运行命令。
# 支持密码重置: docker run --rm ... --reset-password 新密码
if [ "$1" = "--reset-password" ]; then
    shift
    CMD=(python reset_password.py "$@")
else
    CMD=(python main.py)
fi

# 容器默认以 root 启动（见 Dockerfile：已移除 USER 指令）。
# 绑定挂载目录的属主来自宿主机（通常为 root:root），appuser(uid 10001) 无写入权限，
# 因此先以 root 修正挂载目录属主，再用 setpriv 降权运行应用。
# setpriv 属于 util-linux，Debian slim 默认自带，无需 apt 安装（不使用 gosu/su 以免引入不确定依赖）。
if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser "$DATA_DIR"
    exec setpriv --reuid=10001 --regid=10001 --init-groups "${CMD[@]}"
fi

# 防御性分支：若 entrypoint 非以 root 运行（例如镜像被外部以非 root USER 覆盖），
# 不尝试降权，直接以当前身份 exec 应用，保持向后兼容。
exec "${CMD[@]}"
