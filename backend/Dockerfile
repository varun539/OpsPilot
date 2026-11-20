# syntax=docker/dockerfile:1
#
# OpsPilot backend image.
#
# Build context: repository root (see compose.yaml), because requirements.txt
# and the `backend` package are both copied from there.
#
# The image deliberately runs a single Uvicorn worker: application state lives
# in SQLite databases that do not support multi-process writes safely.

FROM python:3.14.2-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt requirements-embeddings.txt constraints-embeddings-cpu.txt ./

# CPU-only PyTorch is installed from the PyTorch CPU wheel index first; the
# constraint file then prevents any later resolution from replacing it with the
# default CUDA wheel. The embedding stack is required by the live AI runtime
# and by the `python -m backend.app.indexing` command.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/venv/bin/pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.15.0+cpu" \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements-embeddings.txt


FROM python:3.14.2-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    OPSPILOT_HOST=0.0.0.0 \
    OPSPILOT_APP_DB=/var/lib/opspilot/app/opspilot_app.sqlite \
    OPSPILOT_CHECKPOINT_DB=/var/lib/opspilot/checkpoints/opspilot_checkpoints.sqlite \
    OPSPILOT_CHROMA_DIR=/var/lib/opspilot/chroma \
    HF_HOME=/var/lib/opspilot/models \
    SENTENCE_TRANSFORMERS_HOME=/var/lib/opspilot/models

WORKDIR /app

# Dedicated unprivileged runtime user. The persistent directories are created
# in the image so newly initialized named volumes inherit their ownership.
RUN groupadd --gid 10001 opspilot \
    && useradd --uid 10001 --gid opspilot --no-create-home \
        --home-dir /app --shell /usr/sbin/nologin opspilot \
    && mkdir -p /var/lib/opspilot/app \
        /var/lib/opspilot/checkpoints \
        /var/lib/opspilot/chroma \
        /var/lib/opspilot/models \
        /var/lib/opspilot/backups \
    && chown -R opspilot:opspilot /var/lib/opspilot

COPY --from=builder /opt/venv /opt/venv
COPY --chown=opspilot:opspilot backend ./backend
COPY --chown=opspilot:opspilot scripts ./scripts

# Import the production application (not the test harness) so broken imports
# fail the build. Temporary database paths keep runtime data out of the layer.
RUN OPSPILOT_APP_DB=/tmp/opspilot-image-check-app.sqlite \
    OPSPILOT_CHECKPOINT_DB=/tmp/opspilot-image-check-checkpoint.sqlite \
    python -c "import backend.app.main" \
    && rm -f /tmp/opspilot-image-check-app.sqlite \
        /tmp/opspilot-image-check-checkpoint.sqlite \
        /tmp/opspilot-image-check-app.sqlite-wal \
        /tmp/opspilot-image-check-app.sqlite-shm \
        /tmp/opspilot-image-check-checkpoint.sqlite-wal \
        /tmp/opspilot-image-check-checkpoint.sqlite-shm

USER opspilot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
