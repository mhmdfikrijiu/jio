FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Jakarta

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY jiofarm ./jiofarm
COPY .env.example ./.env.example

RUN useradd -m -u 1000 jio && mkdir -p /data && chown jio:jio /data
USER jio

ENV DB_PATH=/data/results.db

CMD ["python", "-m", "jiofarm", "run", "--db", "/data/results.db"]
