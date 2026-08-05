# 锁定到不可变 digest，避免基础镜像浮动导致构建不可复现 / 供应链漂移。
# 更新方式：在 Docker Hub 查询 python:3.11-slim 的最新 digest 后替换下方 @sha256。
FROM python:3.11-slim@sha256:78b39ef14d8e2b4d71f8dc304f1328c37df95fe0ef99477c2ae6bd3d03784553

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY reset_password.py ./
COPY app_modules/ ./app_modules/
COPY static/ ./static/
COPY VERSION ./
COPY docker-entrypoint.sh ./

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

# 以非特权用户运行，降低容器被攻破后的影响面（缓解容器逃逸/提权）。
# 端口 3001 为非特权端口，无需 CAP_NET_BIND_SERVICE。
# 切换 USER 前：建数据目录并将 /data、/app 属主改为 appuser
# （entrypoint 需 mkdir $DATA_DIR，应用运行时可能写这两处）。
RUN useradd --create-home --uid 10001 --user-group appuser \
    && mkdir -p /data/douban-history \
    && chown -R appuser:appuser /data /app
USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
