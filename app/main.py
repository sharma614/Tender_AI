"""
app/main.py — FastAPI application (v1.2.0 — Async job queue)

POST /upload        → reads PDF, saves Tender + Job rows, enqueues Celery task
                      returns 202 Accepted with { job_id } immediately
GET  /jobs/{job_id} → polls DB-backed job status
POST /tenders/{id}/analyze → re-run analysis with a custom company profile (also async)
GET  /tenders       → list all tenders
GET  /health        → service health check
"""
import os
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional

from .database import engine, Base, get_db
from .models import Tender, Job
from .schemas import (
    TenderResponse,
    JobEnqueuedResponse,
    JobStatusResponse,
    CompanyProfile,
)
from .services.pdf_processor import PDFProcessor

# ---------------------------------------------------------------------------
# Schema is managed by Alembic — run `alembic upgrade head` before starting.
#
# The previous `Base.metadata.create_all()` bootstrap was removed because it
# cannot ALTER existing tables, so it silently skipped new columns on an
# already-created database and diverged from the migration history.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="TenderAI API",
    description=(
        "Async multi-agent tender analysis. "
        "Upload a PDF → get a job_id → poll /jobs/{job_id} for results."
    ),
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_html_caching(request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/dashboard", "/site", "/preview"} or request.url.path.lower().endswith(".html"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if os.path.exists("public"):
    app.mount("/public", StaticFiles(directory="public"), name="public")


@app.get("/", response_class=FileResponse, summary="Interactive Dashboard")
@app.get("/dashboard", response_class=FileResponse, summary="Interactive Dashboard")
def serve_dashboard():
    dashboard_path = os.path.join("static", "dashboard.html")
    if os.path.exists(dashboard_path):
        return FileResponse(dashboard_path)
    return FileResponse("static/dashboard.html")


@app.get("/site", response_class=FileResponse, summary="Firebase Marketing Site")
@app.get("/preview", response_class=FileResponse, summary="Firebase Marketing Site")
def serve_site():
    return FileResponse("public/index.html")




# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    return {"status": "healthy", "version": "1.2.0"}


@app.post(
    "/upload",
    response_model=JobEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF tender (async)",
    description=(
        "Saves the PDF, creates a Tender record, enqueues the processing job, "
        "and returns immediately with a job_id. "
        "Poll GET /jobs/{job_id} for progress."
    ),
)
async def upload_tender(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported.",
        )

    # 1. Read bytes
    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read uploaded file: {e}",
        )

    # 2. Extract text eagerly (fast, CPU-only — no need to defer)
    try:
        raw_text = PDFProcessor.extract_text_from_bytes(file_bytes)
        if not raw_text.strip():
            raise ValueError("No extractable text found in this PDF.")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Text extraction failed: {e}",
        )

    # 3. Persist Tender record
    tender_title = file.filename.replace(".pdf", "").replace("_", " ").replace("-", " ").strip()
    db_tender = Tender(title=tender_title, raw_text=raw_text)
    db.add(db_tender)
    db.commit()
    db.refresh(db_tender)

    # 4. Create a Job row (status=pending)
    db_job = Job(tender_id=db_tender.id, status="pending")
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    # 5. Enqueue the Celery task — fallback to inline run_pipeline if Redis is offline
    try:
        from .tasks import process_tender_job
        celery_result = process_tender_job.delay(
            job_id=db_job.id,
            tender_id=db_tender.id,
            raw_text=raw_text,
            company_profile_dict=None,  # Use default AeroCorp profile
        )
        db_job.celery_task_id = celery_result.id
        db.commit()
    except Exception as exc:
        # Fallback to direct synchronous execution for local dev without Redis broker
        print(f"[Fallback] Celery/Redis unavailable ({exc}). Running pipeline inline...")
        from .tasks import run_pipeline
        run_pipeline(
            job_id=db_job.id,
            tender_id=db_tender.id,
            raw_text=raw_text,
            company_profile_dict=None,
        )

    return JobEnqueuedResponse(
        job_id=db_job.id,
        status="pending",
        message=f"Job enqueued. Poll GET /jobs/{db_job.id} for status updates.",
    )


@app.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll job status",
    description=(
        "Returns the current status of a processing job. "
        "Status values: pending | processing | retrying | completed | dead_letter. "
        "The 'result' field is populated when status == 'completed'."
    ),
)
def get_job_status(job_id: int, db: Session = Depends(get_db)):
    db_job = db.query(Job).filter(Job.id == job_id).first()
    if not db_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )
    return JobStatusResponse(
        job_id=db_job.id,
        tender_id=db_job.tender_id,
        celery_task_id=db_job.celery_task_id,
        status=db_job.status,
        retry_count=db_job.retry_count,
        max_retries=db_job.max_retries,
        error_message=db_job.error_message,
        result=db_job.result,
        created_at=db_job.created_at,
        updated_at=db_job.updated_at,
    )


@app.post(
    "/tenders/{tender_id}/analyze",
    response_model=JobEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-analyze a tender with a custom company profile (async)",
)
def analyze_tender_with_profile(
    tender_id: int,
    company_profile: CompanyProfile,
    db: Session = Depends(get_db),
):
    db_tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not db_tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tender not found.",
        )

    # Create a new Job for this re-analysis run
    db_job = Job(tender_id=db_tender.id, status="pending")
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    try:
        from .tasks import process_tender_job
        celery_result = process_tender_job.delay(
            job_id=db_job.id,
            tender_id=db_tender.id,
            raw_text=db_tender.raw_text,
            company_profile_dict=company_profile.model_dump(),
        )
        db_job.celery_task_id = celery_result.id
        db.commit()
    except Exception as exc:
        print(f"[Fallback] Celery/Redis unavailable ({exc}). Running pipeline inline...")
        from .tasks import run_pipeline
        run_pipeline(
            job_id=db_job.id,
            tender_id=db_tender.id,
            raw_text=db_tender.raw_text,
            company_profile_dict=company_profile.model_dump(),
        )

    return JobEnqueuedResponse(
        job_id=db_job.id,
        status="pending",
        message=f"Re-analysis job enqueued. Poll GET /jobs/{db_job.id} for status updates.",
    )


@app.get("/tenders", response_model=List[TenderResponse], summary="List all tenders")
def list_tenders(db: Session = Depends(get_db)):
    return db.query(Tender).all()


@app.get("/admin/metrics", summary="System Observability & Agent Metrics")
def get_metrics(db: Session = Depends(get_db)):
    from .metrics import get_system_metrics
    return get_system_metrics(db)

