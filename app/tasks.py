"""
app/tasks.py — Background Celery tasks for tender processing.

Flow:
  1. Update job status → processing
  2. Run embed pipeline (PDF chunk + pgvector insert, with page provenance)
  3. Run the LangGraph multi-agent orchestration
  4. Update job status → completed, store result in DB

Retry / Backoff strategy:
  Attempt 1 → wait 30s  → retry
  Attempt 2 → wait 120s → retry
  Attempt 3 → wait 480s → retry
  Attempt 4+ → mark job as dead_letter, stop retrying

Dead-letter path:
  The job row in PostgreSQL is updated to status="dead_letter" with the final
  error message stored. No further re-queuing occurs.

The core pipeline lives in the plain function `run_pipeline` so that both the
direct-upload task and the S3-triggered task can share it. Previously the S3 task
called `process_tender_job(self, ...)` directly, passing its own `self` into
another task's body — which made `self.request.retries` and therefore the
retry/dead-letter bookkeeping refer to the wrong task.
"""
import datetime

from celery import Task

from .database import SessionLocal, log_tool_call
from .models import Job, Tender, TenderChunk
from .schemas import ToolCallRecord
from .worker import celery_app

MAX_RETRIES = 3
BASE_BACKOFF_SECS = 30  # countdown = BASE * (4 ** attempt)  → 30s, 120s, 480s


def _get_backoff(attempt: int) -> int:
    """Exponential backoff: 30s → 120s → 480s"""
    return BASE_BACKOFF_SECS * (4 ** attempt)


def _update_job_status(db, job_id: int, **kwargs):
    """Helper to patch a Job row and commit."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job:
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.datetime.utcnow()
        db.commit()
    return job


def run_pipeline(
    job_id: int,
    tender_id: int,
    raw_text: str,
    company_profile_dict: dict = None,
    celery_task_id: str = None,
) -> dict:
    """
    Embed chunks and run the agent graph for one tender.

    Plain function, no Celery binding — raises on failure and lets the calling
    task own retry policy. Shared by both task entry points.
    """
    # Lazy imports — heavy ML/LLM libs only load in the Celery worker process,
    # not when the task module is imported by FastAPI
    from .agents.orchestrator import AgentOrchestrator
    from .services.embedder import TextEmbedder
    from .services.pdf_processor import PDFProcessor

    db = SessionLocal()
    embedder = TextEmbedder()
    orchestrator = AgentOrchestrator()

    try:
        _update_job_status(db, job_id, status="processing", celery_task_id=celery_task_id)

        # --- Step 1: Embed chunks into pgvector (with page provenance) ---
        chunks = PDFProcessor.chunk_text(raw_text, chunk_size=1000, chunk_overlap=200)
        chunk_texts = [c["text"] for c in chunks]
        embeddings = embedder.embed_batch(chunk_texts)

        for chunk_data, embedding in zip(chunks, embeddings):
            db.add(TenderChunk(
                tender_id=tender_id,
                chunk_index=chunk_data["index"],
                text_content=chunk_data["text"],
                embedding=embedding,
                char_start=chunk_data.get("char_start"),
                page_number=chunk_data.get("page_number"),
            ))
        db.commit()

        # --- Step 2: Run multi-agent orchestration ---
        company_profile = None
        if company_profile_dict:
            from .schemas import CompanyProfile
            company_profile = CompanyProfile(**company_profile_dict)

        analysis_state = orchestrator.run_analysis(
            tender_id=tender_id,
            raw_text=raw_text,
            db=db,
            company_profile=company_profile,
        )

        # --- Step 3: Mark job completed, store result ---
        _update_job_status(
            db, job_id,
            status="completed",
            result=analysis_state.model_dump(mode="json"),
            error_message=None,
        )

        return {"status": "completed", "tender_id": tender_id}
    finally:
        db.close()


def _handle_failure(self: Task, job_id: int, tender_id: int, exc: Exception):
    """
    Shared retry / dead-letter handling. Returns a retry exception to raise, or
    re-raises when retries are exhausted.
    """
    db = SessionLocal()
    try:
        attempt = self.request.retries  # 0 on first failure, 1 on second, ...

        if attempt >= MAX_RETRIES:
            _update_job_status(
                db, job_id,
                status="dead_letter",
                error_message=f"[Attempt {attempt + 1}] Max retries exceeded. Final error: {exc}",
            )
            if tender_id is not None:
                log_tool_call(
                    db=db,
                    tender_id=tender_id,
                    record=ToolCallRecord(
                        agent_name="JobQueue",
                        tool_name="dead_letter",
                        input_arguments={"job_id": job_id, "attempt": attempt + 1},
                        status="error",
                        error_message=str(exc),
                    ),
                )
            # DO NOT retry — let the task finish as failed.
            raise exc

        countdown = _get_backoff(attempt)
        _update_job_status(
            db, job_id,
            status="retrying",
            retry_count=attempt + 1,
            error_message=f"[Attempt {attempt + 1}] Failed: {exc}. Retrying in {countdown}s.",
        )
        return self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="app.tasks.process_tender_job",
    max_retries=MAX_RETRIES,
    # Do NOT use autoretry_for here — we handle retries manually so we can
    # update DB status on each attempt and implement dead-letter logic.
)
def process_tender_job(self: Task, job_id: int, tender_id: int, raw_text: str, company_profile_dict: dict = None):
    """
    Celery task: embed chunks and run multi-agent orchestration for a tender.

    Parameters
    ----------
    job_id               : PK of the Job row to update
    tender_id            : PK of the Tender row already persisted by the API
    raw_text             : Full extracted text from the uploaded PDF
    company_profile_dict : Optional dict matching CompanyProfile schema
    """
    try:
        return run_pipeline(
            job_id=job_id,
            tender_id=tender_id,
            raw_text=raw_text,
            company_profile_dict=company_profile_dict,
            celery_task_id=self.request.id,
        )
    except Exception as exc:
        raise _handle_failure(self, job_id, tender_id, exc)


# ===========================================================================
# S3-Triggered Entry Point (called by the Lambda → SQS path on AWS)
# ===========================================================================

@celery_app.task(
    bind=True,
    name="app.tasks.process_tender_job_from_s3",
    max_retries=MAX_RETRIES,
)
def process_tender_job_from_s3(self: Task, s3_bucket: str, s3_key: str, company_profile_dict: dict = None):
    """
    S3-aware task entry point — used exclusively on AWS.

    The Lambda trigger publishes a message with { s3_bucket, s3_key } instead of
    raw text. This task downloads the PDF, extracts text, creates Tender + Job
    rows, then delegates to the shared `run_pipeline`.
    """
    import boto3

    from .services.pdf_processor import PDFProcessor

    db = SessionLocal()
    job_id = None
    tender_id = None

    try:
        # --- Step 1: Download PDF from S3 ---
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        file_bytes = response["Body"].read()

        # --- Step 2: Extract text ---
        raw_text = PDFProcessor.extract_text_from_bytes(file_bytes)
        if not raw_text.strip():
            raise ValueError(f"No extractable text found in s3://{s3_bucket}/{s3_key}")

        # --- Step 3: Create DB rows ---
        tender_title = s3_key.split("/")[-1].replace(".pdf", "").replace("_", " ").replace("-", " ")
        db_tender = Tender(title=tender_title, raw_text=raw_text, s3_key=s3_key)
        db.add(db_tender)
        db.commit()
        db.refresh(db_tender)
        tender_id = db_tender.id

        db_job = Job(tender_id=tender_id, status="pending", celery_task_id=self.request.id)
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        job_id = db_job.id
    except Exception as exc:
        db.close()
        attempt = self.request.retries
        if attempt >= MAX_RETRIES:
            raise exc
        raise self.retry(exc=exc, countdown=_get_backoff(attempt))
    finally:
        try:
            db.close()
        except Exception:
            pass

    # --- Step 4: Delegate to the shared pipeline, with this task's own retry state ---
    try:
        return run_pipeline(
            job_id=job_id,
            tender_id=tender_id,
            raw_text=raw_text,
            company_profile_dict=company_profile_dict,
            celery_task_id=self.request.id,
        )
    except Exception as exc:
        raise _handle_failure(self, job_id, tender_id, exc)
