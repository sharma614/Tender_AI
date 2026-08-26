"""
app/tasks.py — Background Celery task for tender processing.

Flow:
  1. Update job status → processing
  2. Run embed pipeline (PDF chunk + pgvector insert)
  3. Run multi-agent orchestration (Extraction, Summary, Risk, Compliance)
  4. Update job status → completed, store result in DB

Retry / Backoff strategy:
  Attempt 1 → wait 30s  → retry
  Attempt 2 → wait 120s → retry
  Attempt 3 → wait 480s → retry
  Attempt 4+ → mark job as dead_letter, stop retrying

Dead-letter path:
  The job row in PostgreSQL is updated to status="dead_letter" with the final
  error message stored. No further re-queuing occurs.
"""
import os
import io
from celery import Task
from celery.exceptions import MaxRetriesExceededError
from .worker import celery_app
from .database import SessionLocal, log_tool_call
from .models import Job, Tender, TenderChunk

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
        import datetime
        job.updated_at = datetime.datetime.utcnow()
        db.commit()
    return job


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
    job_id            : PK of the Job row to update
    tender_id         : PK of the Tender row already persisted by the API
    raw_text          : Full extracted text from the uploaded PDF
    company_profile_dict : Optional dict matching CompanyProfile schema
    """
    # Lazy imports — heavy ML/LLM libs only load in the Celery worker process,
    # not when the task module is imported by FastAPI
    from .services.pdf_processor import PDFProcessor
    from .services.embedder import TextEmbedder
    from .agents.orchestrator import AgentOrchestrator

    db = SessionLocal()
    embedder = TextEmbedder()
    orchestrator = AgentOrchestrator()

    try:
        # --- Mark job as processing ---
        _update_job_status(db, job_id, status="processing", celery_task_id=self.request.id)

        # --- Step 1: Embed chunks into pgvector ---
        chunks = PDFProcessor.chunk_text(raw_text, chunk_size=1000, chunk_overlap=200)
        chunk_texts = [c["text"] for c in chunks]
        embeddings = embedder.embed_batch(chunk_texts)

        for chunk_data, embedding in zip(chunks, embeddings):
            db_chunk = TenderChunk(
                tender_id=tender_id,
                chunk_index=chunk_data["index"],
                text_content=chunk_data["text"],
                embedding=embedding,
            )
            db.add(db_chunk)
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
        result_payload = analysis_state.model_dump(mode="json")
        _update_job_status(
            db, job_id,
            status="completed",
            result=result_payload,
            error_message=None,
        )

        return {"status": "completed", "tender_id": tender_id}

    except Exception as exc:
        # Determine which retry attempt this is (0-indexed)
        attempt = self.request.retries  # e.g. 0 on first failure, 1 on second, ...

        if attempt >= MAX_RETRIES:
            # ---- Dead-letter path ----
            _update_job_status(
                db, job_id,
                status="dead_letter",
                error_message=f"[Attempt {attempt + 1}] Max retries exceeded. Final error: {str(exc)}",
            )
            # Log the dead-letter event as a tool call for auditing
            log_tool_call(
                db=db,
                tender_id=tender_id,
                agent_name="JobQueue",
                tool_name="dead_letter",
                input_arguments={"job_id": job_id, "attempt": attempt + 1},
                output_response={"error": str(exc)},
            )
            # DO NOT call self.retry() — let the task finish with failure
            raise exc

        # ---- Retry path with exponential backoff ----
        countdown = _get_backoff(attempt)
        _update_job_status(
            db, job_id,
            status="retrying",
            retry_count=attempt + 1,
            error_message=f"[Attempt {attempt + 1}] Failed: {str(exc)}. Retrying in {countdown}s.",
        )

        raise self.retry(exc=exc, countdown=countdown)

    finally:
        db.close()


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

    The Lambda trigger publishes a message with { s3_bucket, s3_key } instead of raw text.
    This task:
      1. Downloads the PDF bytes from S3
      2. Extracts text with PyMuPDF
      3. Creates Tender + Job rows in the DB
      4. Delegates to the core processing pipeline (embed → orchestrate → persist)

    Parameters
    ----------
    s3_bucket           : S3 bucket name
    s3_key              : S3 object key (e.g. "uploads/my-tender.pdf")
    company_profile_dict: Optional dict matching CompanyProfile schema
    """
    import boto3
    from .services.pdf_processor import PDFProcessor

    db = SessionLocal()

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

        db_job = Job(tender_id=db_tender.id, status="pending", celery_task_id=self.request.id)
        db.add(db_job)
        db.commit()
        db.refresh(db_job)

        db.close()

        # --- Step 4: Delegate to the core pipeline task ---
        # Call synchronously within this worker (not re-queued — avoids double-hop latency)
        return process_tender_job(
            self,
            job_id=db_job.id,
            tender_id=db_tender.id,
            raw_text=raw_text,
            company_profile_dict=company_profile_dict,
        )

    except Exception as exc:
        attempt = self.request.retries
        if attempt >= MAX_RETRIES:
            raise exc
        countdown = _get_backoff(attempt)
        raise self.retry(exc=exc, countdown=countdown)

    finally:
        try:
            db.close()
        except Exception:
            pass
