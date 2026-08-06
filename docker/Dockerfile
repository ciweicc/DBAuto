# 基础镜像使用浮动 tag。原固定 digest（python:3.11-slim@sha256:78b39...）对应的
# manifest 已从 Docker Hub 移除，原样构建会报 “manifest verification failed” 而直接失败。
# 如需可复现构建，日后可在 Docker Hub 重新查询 python:3.11-slim 的当前 digest 并固定到下方。
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY scripts/reset_password.py ./reset_password.py
COPY app_modules/ ./app_modules/
COPY static/ ./static/
COPY pyproject.toml ./
COPY docker/docker-entrypoint.sh ./docker-entrypoint.sh

RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

LABEL org.opencontainers.image.title="DBAuto" \
      org.opencontainers.image.description="豆瓣自动转存工具" \
      org.opencontainers.image.licenses="AGPL-3.0"

ENV DATA_DIR=/data/douban-history
ENV PORT=3001
ENV TZ=Asia/Shanghai

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3001/health').read()"

# 创建非特权用户 appuser(uid 10001)，用于运行应用以降低容器被攻破后的影响面。
# 端口 3001 为非特权端口，无需 CAP_NET_BIND_SERVICE。
# 构建期预先建数据目录并将 /data、/app 属主改为 appuser。
# 注意：宿主机 ./data 绑定挂载会在运行期整体遮掉镜像内的 /data/douban-history，
# 其属主来自宿主机（通常为 root:root），构建期的 chown 对该挂载目录无效，
# 因此必须在 entrypoint 启动（以 root 身份）时修正挂载目录属主后降权运行（见 docker-entrypoint.sh）。
# 此处不再使用 USER 指令：容器以 root 启动 entrypoint，由 entrypoint 修正属主后
# 用 setpriv 降权到 appuser 再 exec 应用。
RUN useradd --create-home --uid 10001 --user-group appuser \
    && mkdir -p /data/douban-history \
    && chown -R appuser:appuser /data /app

ENTRYPOINT ["./docker-entrypoint.sh"]
