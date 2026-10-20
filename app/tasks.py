"""
app/tasks.py — Background Celery tasks for tender processing.

Flow:
  1. Update job status → processing
  2. Upload raw PDF to Supabase Storage (if bytes provided)
  3. Run embed pipeline (PDF chunk + pgvector insert, with page provenance)
  4. Run the LangGraph multi-agent orchestration
  5. Update job status → completed, store result in DB

Retry / Backoff strategy:
  Attempt 1 → wait 30s  → retry
  Attempt 2 → wait 120s → retry
  Attempt 3 → wait 480s → retry
  Attempt 4+ → mark job as dead_letter, stop retrying

Dead-letter path:
  The job row in PostgreSQL is updated to status="dead_letter" with the final
  error message stored. No further re-queuing occurs.

The core pipeline lives in the plain function `run_pipeline` so that the
calling task owns retry policy and `run_pipeline` can be called directly
(inline fallback when Redis is unavailable on first boot).
"""
import datetime
import os

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


def _upload_to_supabase(file_bytes: bytes, object_name: str) -> str | None:
    """
    Upload a PDF to Supabase Storage and return the storage path.
    Returns None (silently) when SUPABASE_URL / SUPABASE_SERVICE_KEY are not set
    (e.g. local dev without Supabase configured).
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
    bucket = os.environ.get("SUPABASE_BUCKET", "tenders")

    if not supabase_url or not supabase_key:
        return None

    try:
        from supabase import create_client
        client = create_client(supabase_url, supabase_key)
        client.storage.from_(bucket).upload(
            object_name,
            file_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        return f"{bucket}/{object_name}"
    except Exception as exc:
        # Storage upload failure is non-fatal: analysis can proceed without it.
        print(f"[Storage] Supabase upload failed for {object_name}: {exc}")
        return None


def run_pipeline(
    job_id: int,
    tender_id: int,
    raw_text: str,
    company_profile_dict: dict = None,
    celery_task_id: str = None,
    file_bytes: bytes = None,
    original_filename: str = None,
) -> dict:
    """
    Embed chunks and run the agent graph for one tender.

    Plain function, no Celery binding — raises on failure and lets the calling
    task own retry policy. Shared by all task entry points.

    Parameters
    ----------
    file_bytes       : Original PDF bytes — uploaded to Supabase Storage when provided.
    original_filename: Used as the Supabase Storage object name.
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

        # --- Step 1: Upload PDF to Supabase Storage (optional, non-fatal) ---
        if file_bytes and original_filename:
            storage_path = _upload_to_supabase(
                file_bytes,
                f"{tender_id}/{original_filename}",
            )
            if storage_path:
                tender = db.query(Tender).filter(Tender.id == tender_id).first()
                if tender:
                    tender.storage_path = storage_path
                    db.commit()

        # --- Step 2: Embed chunks into pgvector (with page provenance) ---
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

        # --- Step 3: Run multi-agent orchestration ---
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

        # --- Step 4: Mark job completed, store result ---
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
def process_tender_job(
    self: Task,
    job_id: int,
    tender_id: int,
    raw_text: str,
    company_profile_dict: dict = None,
    file_bytes: bytes = None,
    original_filename: str = None,
):
    """
    Celery task: embed chunks and run multi-agent orchestration for a tender.

    Parameters
    ----------
    job_id               : PK of the Job row to update
    tender_id            : PK of the Tender row already persisted by the API
    raw_text             : Full extracted text from the uploaded PDF
    company_profile_dict : Optional dict matching CompanyProfile schema
    file_bytes           : Original PDF bytes for Supabase Storage upload
    original_filename    : Filename used as the Storage object name
    """
    try:
        return run_pipeline(
            job_id=job_id,
            tender_id=tender_id,
            raw_text=raw_text,
            company_profile_dict=company_profile_dict,
            celery_task_id=self.request.id,
            file_bytes=file_bytes,
            original_filename=original_filename,
        )
    except Exception as exc:
        raise _handle_failure(self, job_id, tender_id, exc)
