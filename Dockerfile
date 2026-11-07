FROM python:3.12-slim

LABEL org.opencontainers.image.title="Yunshu ERP"
LABEL org.opencontainers.image.description="AI-native modern ERP with real business transactions"
LABEL org.opencontainers.image.source="https://github.com/clawgeek/modern-erp-system"

WORKDIR /app
COPY erp_core.py server.py ./
COPY public ./public
RUN mkdir -p /app/data && python -c "import erp_core; erp_core.init_db()"

ENV ERP_DB_PATH=/app/data/erp.db
ENV PYTHONUNBUFFERED=1
EXPOSE 8766

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8766/api/health',timeout=3).status==200 else 1)"

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8766"]
