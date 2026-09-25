FROM python:3.12-slim

# Fail fast and stream logs instead of buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

# The default SQLite database lives on a volume so it survives container restarts.
ENV CACHE_SERVICE_DATABASE_URL=sqlite+aiosqlite:////data/cache_service.db
RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "cache_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
