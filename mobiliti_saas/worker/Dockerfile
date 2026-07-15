FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV QUOTE_ENGINE=python
ENV WORKER_POLL_SECONDS=10
ENV WORKER_STALE_MINUTES=30
ENV MAX_QUOTE_OUTPUT_MB=100
ENV TEMPLATE_PATH="/app/templates/Formato Cotizacion 2026 GDL.xlsx"

WORKDIR /app

COPY mobiliti_saas/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY mobiliti_saas/worker /app/mobiliti_saas/worker
COPY mobiliti_saas/quote_engine /app/mobiliti_saas/quote_engine
COPY pdf_quotation_import /app/pdf_quotation_import
COPY mobiliti_saas/worker/templates /app/templates

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import json, urllib.request; r=urllib.request.urlopen('http://127.0.0.1:10000/health', timeout=5); data=json.load(r); raise SystemExit(0 if data.get('ok') and data.get('isolated_jobs') else 1)"

CMD ["python", "mobiliti_saas/worker/quote_worker.py"]
