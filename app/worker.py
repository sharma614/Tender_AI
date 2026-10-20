"""
app/worker.py — Celery application factory.

Broker: Redis (Upstash `rediss://` in production, local `redis://` in dev).
Results are NOT stored in Celery's backend — the PostgreSQL Job table is the
single source of truth. CELERY_RESULT_BACKEND is still wired for dev convenience.

Start a local worker:
  celery -A app.worker.celery_app worker --loglevel=info --concurrency=2

Start on Render (Background Worker service — set start command in render.yaml):
  celery -A app.worker.celery_app worker --loglevel=info --concurrency=2
"""
import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Broker / Backend
# ---------------------------------------------------------------------------
# Production: Upstash Redis — set CELERY_BROKER_URL=rediss://:<password>@<host>:6379
# Local dev:  redis://localhost:6379/0  (Docker or native Redis)
BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

TASK_QUEUE_NAME = os.environ.get("CELERY_TASK_QUEUE", "tenderai-jobs")

# ---------------------------------------------------------------------------
# Celery app factory
# ---------------------------------------------------------------------------
celery_app = Celery(
    "tenderai",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.tasks"],
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Reliability — ACK only after the task finishes so a crashed worker does
    # not silently drop a job; Redis will redeliver the unacked message.
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Result expiry (redis-backed dev convenience; prod relies on Job table)
    result_expires=86400,

    # Queue routing
    task_default_queue=TASK_QUEUE_NAME,

    # Upstash Redis requires TLS; the rediss:// scheme handles this automatically
    # via the redis-py client. No extra ssl_cert_reqs override needed.
    broker_use_ssl=BROKER_URL.startswith("rediss://"),
)
