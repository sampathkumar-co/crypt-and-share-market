# syntax=docker/dockerfile:1.7

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.13-slim AS runtime

ARG VCS_REF="unknown"
ARG BUILD_DATE="unknown"

LABEL org.opencontainers.image.title="Dual Market AI Bot" \
      org.opencontainers.image.description="Paper-only crypto and Indian equity research dashboard" \
      org.opencontainers.image.source="https://github.com/sampathkumar-co/crypt-and-share-market" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    TRADEBOT_ALLOW_PUBLIC=true \
    TRADEBOT_ENABLE_MUTATIONS=false \
    TRADEBOT_DATA_DIR=/app/data \
    TRADEBOT_REPORTS_DIR=/var/lib/tradebot/reports \
    TRADEBOT_STATE_DIR=/var/lib/tradebot/paper_state

RUN groupadd --system --gid 10001 tradebot \
    && useradd --system --uid 10001 --gid tradebot --home-dir /nonexistent --shell /usr/sbin/nologin tradebot

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels
COPY data ./data
RUN mkdir -p /var/lib/tradebot/reports /var/lib/tradebot/paper_state \
    && chown -R tradebot:tradebot /var/lib/tradebot

USER tradebot:tradebot
EXPOSE 8000
VOLUME ["/var/lib/tradebot"]
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "from urllib.request import urlopen; r=urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT','8000') + '/ready', timeout=3); raise SystemExit(0 if r.status == 200 else 1)"

CMD ["sh", "-c", "exec tradebot serve-dashboard --host 0.0.0.0 --port ${PORT:-8000} --public"]
