FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system codescribe \
    && adduser --system --ingroup codescribe codescribe \
    && python -m pip install --upgrade pip

COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

RUN python -m pip install "."

RUN chmod +x /app/scripts/start-api.sh \
    && chown -R codescribe:codescribe /app

USER codescribe

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["/app/scripts/start-api.sh"]
