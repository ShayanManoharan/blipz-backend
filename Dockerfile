FROM python:3.13-slim

WORKDIR /app

# Runs as a non-root user in production — no reason this process needs root, and most
# hosts (Render, Fly.io, Railway) run containers this way by default anyway.
RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness probe used by `docker run --health-cmd`/local tooling; hosted platforms
# generally use their own HTTP health-check pointed at GET /health instead (see
# render.yaml), but this keeps `docker inspect`/`docker ps` meaningful too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# $PORT is set by Render/Railway (and similar PaaS hosts) to whatever port they expect
# the container to listen on — falls back to 8000 for local `docker run`/Fly.io (which
# is told the port directly via fly.toml instead of an env var).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
