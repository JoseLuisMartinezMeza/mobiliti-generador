FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV HOME=/tmp
ENV NUMBA_CACHE_DIR=/tmp/numba-cache
ENV U2NET_HOME=/tmp/.u2net
ENV QUOTE_ENGINE=python
ENV WORKER_POLL_SECONDS=10
ENV WORKER_STALE_MINUTES=30
ENV MAX_QUOTE_OUTPUT_MB=100
ENV TEMPLATE_PATH="/app/templates/Formato Cotizacion 2026 Oficial.xlsx"

WORKDIR /app

COPY mobiliti_saas/worker/requirements.lock /app/requirements.lock
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && python -m pip install --upgrade pip==26.1.2 \
    && pip install -r /app/requirements.lock \
    && groupadd --system --gid 10001 mobiliti \
    && useradd --system --uid 10001 --gid 10001 --no-create-home mobiliti

COPY mobiliti_saas/worker /app/mobiliti_saas/worker
COPY mobiliti_saas/quote_engine /app/mobiliti_saas/quote_engine
COPY pdf_quotation_import /app/pdf_quotation_import
COPY mobiliti_saas/worker/templates /app/templates

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import json, os, urllib.request; port=os.environ.get('PORT', '10000'); r=urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=5); data=json.load(r); raise SystemExit(0 if data.get('ok') and data.get('isolated_jobs') else 1)"

CMD ["python", "mobiliti_saas/worker/render_web_worker.py"]
