FROM registry.aliyuncs.com/python:3.11-slim

WORKDIR /app

# 配置 apt 清华镜像源
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list && \
    apt-get update && \
    rm -rf /var/lib/apt/lists/*

# 配置 pip 清华镜像源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY app_modules/ ./app_modules/
COPY static/ ./static/
COPY VERSION ./
COPY docker-entrypoint.sh ./

RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

ENV DATA_DIR=/data/douban-history
ENV PORT=3001
ENV TZ=Asia/Shanghai

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3001/health').read()"

ENTRYPOINT ["./docker-entrypoint.sh"]
