FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    pip install -r requirements.txt

COPY main.py app_meta.py auth.py database.py vector_store.py llm_service.py memory_categories.py settings.py temporal_service.py backfill_date_mentions.py search_service.py inspiration_rendering.py ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Keep mutable state outside the application code. The existing application
# paths resolve through these links without changing business logic.
RUN mkdir -p /data/chroma_data && \
    ln -s /data/memory.db /app/memory.db && \
    ln -s /data/chroma_data /app/chroma_data && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# SQLite and local ChromaDB are intentionally run as one application worker.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "main:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
