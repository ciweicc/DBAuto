FROM python:3.11-slim

RUN echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main non-free-firmware\n\
deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main non-free-firmware\n\
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main non-free-firmware\n\
deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main non-free-firmware\n\
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-backports main non-free-firmware\n\
deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-backports main non-free-firmware' > /etc/apt/sources.list && \
    rm -f /etc/apt/sources.list.d/*.sources && \
    apt-get update && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY main.py ./
COPY app_modules/ ./app_modules/
COPY static/ ./static/
COPY docker-entrypoint.sh ./

RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

ENV DATA_DIR=/data/douban-history
ENV PORT=3001
ENV TZ=Asia/Shanghai

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3001/health').read()"

ENTRYPOINT ["./docker-entrypoint.sh"]