"""
tests/test_api.py — Integration tests for FastAPI endpoints with mock DB.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models import Tender, Job, ToolCall, TenderChunk
from app.database import Base, get_db
from app.main import app as fastapi_app

# Create in-memory SQLite engine with StaticPool so all sessions share the same memory DB
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create all model tables
Base.metadata.create_all(bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

fastapi_app.dependency_overrides[get_db] = override_get_db

client = TestClient(fastapi_app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert "version" in response.json()

def test_html_entrypoints_are_not_cached():
    for path in ("/", "/dashboard", "/site", "/preview", "/public/index.html", "/static/dashboard.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"

def test_metrics_endpoint():
    response = client.get("/admin/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "job_status_distribution" in data

def test_upload_invalid_filetype():
    response = client.post(
        "/upload",
        files={"file": ("test.txt", b"Hello world", "text/plain")}
    )
    assert response.status_code == 400
    assert "Only PDF files are supported" in response.json()["detail"]
