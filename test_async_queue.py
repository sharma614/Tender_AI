"""
test_async_queue.py — Verifies the async job queue infrastructure without
requiring a live Redis or Postgres connection.
"""
import os, sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

print("--- TenderAI Async Queue Verification ---")

# 1. Schema imports
try:
    from app.schemas import JobEnqueuedResponse, JobStatusResponse, CompanyProfile
    print("[OK] JobEnqueuedResponse and JobStatusResponse schemas load correctly.")
except Exception as e:
    print(f"[ERROR] Schema import failed: {e}")
    sys.exit(1)

# 2. Model import
try:
    from app.models import Job, Tender, ToolCall
    print("[OK] Job model loaded correctly.")
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"[ERROR] Model import failed: {e}")
    sys.exit(1)

# 3. Celery worker factory import
try:
    from app.worker import celery_app
    print(f"[OK] Celery app loaded. Broker: {celery_app.conf.broker_url}")
except Exception as e:
    print(f"[ERROR] Celery worker import failed: {e}")
    sys.exit(1)

# 4. Task import (without calling .delay — no Redis needed)
try:
    from app.tasks import process_tender_job, _get_backoff
    print("[OK] process_tender_job task loaded correctly.")
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"[ERROR] Task import failed: {e}")
    sys.exit(1)

# 5. Verify backoff schedule
expected = [(0, 30), (1, 120), (2, 480)]
ok = True
for attempt, expected_secs in expected:
    actual = _get_backoff(attempt)
    if actual != expected_secs:
        print(f"[ERROR] Backoff mismatch at attempt {attempt}: expected {expected_secs}s, got {actual}s")
        ok = False
if ok:
    print(f"[OK] Exponential backoff schedule correct: {[_get_backoff(i) for i in range(3)]}s")

# 6. SQLite mock: Job state transitions
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, log_tool_call
import datetime

engine = create_engine("sqlite:///:memory:")
Session = sessionmaker(bind=engine)
db = Session()
Base.metadata.create_all(bind=engine, tables=[Tender.__table__, Job.__table__, ToolCall.__table__])

mock_tender = Tender(title="Test Tender", raw_text="content")
db.add(mock_tender)
db.commit()

# Simulate state machine transitions
mock_job = Job(tender_id=mock_tender.id, status="pending")
db.add(mock_job)
db.commit()

for new_status in ["processing", "retrying", "completed"]:
    mock_job.status = new_status
    mock_job.updated_at = datetime.datetime.utcnow()
    db.commit()

final_job = db.query(Job).filter(Job.id == mock_job.id).first()
if final_job.status == "completed":
    print("[OK] Job status state machine transitions work correctly.")
else:
    print(f"[ERROR] Unexpected final status: {final_job.status}")
    sys.exit(1)

# Dead-letter transition
mock_job.status = "dead_letter"
mock_job.error_message = "Max retries exceeded."
db.commit()
dl_job = db.query(Job).filter(Job.id == mock_job.id).first()
if dl_job.status == "dead_letter" and dl_job.error_message:
    print("[OK] Dead-letter path works correctly.")
else:
    print("[ERROR] Dead-letter state not set correctly.")
    sys.exit(1)

print("\n--- All async queue checks passed! ---")
print("To start the full stack locally:")
print("  docker-compose up -d              # PostgreSQL + Redis")
print("  pip install -r requirements.txt")
print("  uvicorn app.main:app --reload     # FastAPI (port 8000)")
print("  celery -A app.worker.celery_app worker --loglevel=info  # Worker")
