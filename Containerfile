# NAS usage browser - stdlib-only Python, no pip install needed.
FROM docker.io/library/python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
COPY app/ /app/

RUN useradd --system --uid 10001 --no-create-home nasbrowser
USER 10001

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["python", "server.py"]
