"""
app/worker.py — Celery application factory.

Local development:  CELERY_BROKER_URL=redis://localhost:6379/0  (default)
AWS production:     CELERY_BROKER_URL=sqs://                    (uses IAM role credentials)

The SQS queue name is read from CELERY_TASK_QUEUE (set by Terraform in the ECS task definition).
When running locally against Redis, the default queue name 'tenderai-jobs' is used.

Start a local worker:
  celery -A app.worker.celery_app worker --loglevel=info --concurrency=2

Start an AWS/SQS worker (in the Docker container):
  celery -A app.worker.celery_app worker --loglevel=info --concurrency=1
"""
import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Broker / Backend configuration
# ---------------------------------------------------------------------------
BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")

# On AWS with SQS broker, Celery uses SQS natively and doesn't need a result backend.
# Results are stored in the PostgreSQL Job table, not in Celery's backend.
# For local Redis, we still configure a result backend for dev convenience.
if BROKER_URL.startswith("sqs://"):
    RESULT_BACKEND = None   # No result backend on AWS — Job table is the source of truth
else:
    RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# SQS queue name — Terraform sets CELERY_TASK_QUEUE to the actual SQS queue name
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

    # Reliability — only ACK a task after it finishes.
    # On SQS this means the message stays in-flight (invisible) until the task
    # completes or the visibility timeout expires, then it becomes visible again
    # for another worker. This prevents silent drops if the worker crashes.
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Result expiry (only applies when RESULT_BACKEND is set i.e. local Redis)
    result_expires=86400,

    # Queue routing
    task_default_queue=TASK_QUEUE_NAME,
)

# ---------------------------------------------------------------------------
# SQS-specific Kombu transport settings
# ---------------------------------------------------------------------------
if BROKER_URL.startswith("sqs://"):
    # kombu (Celery's transport layer) reads these from broker_transport_options
    # AWS credentials come from the ECS task IAM role — no explicit keys needed.
    celery_app.conf.broker_transport_options = {
        "region": os.environ.get("AWS_REGION", "us-east-1"),
        "visibility_timeout": int(os.environ.get("SQS_VISIBILITY_TIMEOUT", "900")),
        "polling_interval": 5,          # seconds between SQS ReceiveMessage calls
        "wait_time_seconds": 20,        # SQS long-polling (reduces API calls by ~95%)
        "is_secure": True,
    }

    # On SQS, we want to predeclare the queue so Celery knows it exists
    # (avoids a CreateQueue call on every worker startup)
    celery_app.conf.task_create_missing_queues = False
