FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app_modules/ ./app_modules/
COPY static/ ./static/

ENV DATA_DIR=/data/douban-history
ENV PORT=3001

EXPOSE 3001

CMD ["python", "main.py"]
