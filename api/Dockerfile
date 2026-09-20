# Image for both services: the API runs the default CMD, the worker overrides it with
#   python -m arq app.worker.WorkerSettings
# One image means one dependency set and one model cache to reason about.

FROM python:3.14-slim

# onnxruntime links against libgomp. Everything else in the tree ships manylinux
# wheels, so no compiler is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Installed editable on purpose: `app.config` derives its default storage paths from
# `__file__`, so a copy living in site-packages would resolve them to the wrong tree.
# Editable keeps the package pointing at /app, where the volume is mounted.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -e .

# Needed in the image so migrations can run against the deployed code:
#   docker compose run --rm api python -m alembic upgrade head
COPY alembic.ini ./
COPY alembic ./alembic

# Paths are set explicitly rather than inherited from the config defaults, so the
# volume mount point is obvious from the Dockerfile alone.
ENV UPLOAD_DIR=/app/storage/uploads \
    MODEL_CACHE_DIR=/app/storage/models \
    PYTHONPATH=/app

# Never root. The storage tree is owned by this user because both the API (which writes
# uploads) and the worker (which writes the model cache) run as it.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/storage/uploads /app/storage/models \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Deliberately hits /openapi.json, not /api/health: health probes Postgres, Redis and
# Pinecone, and running that every 30s would mean a constant trickle of calls to a
# third-party API just to prove the process is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
