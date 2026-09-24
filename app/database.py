import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()


def build_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return database_url

    db_config = {
        "DB_USER": os.environ.get("DB_USER"),
        "DB_PASSWORD": os.environ.get("DB_PASSWORD"),
        "DB_HOST": os.environ.get("DB_HOST"),
        "DB_PORT": os.environ.get("DB_PORT"),
        "DB_NAME": os.environ.get("DB_NAME"),
    }

    if not any(value for value in db_config.values()):
        return "sqlite:///./tenderai_dev.db"

    username = db_config["DB_USER"] or "postgres"
    password = db_config["DB_PASSWORD"] or "postgres"
    host = db_config["DB_HOST"] or "localhost"
    port = db_config["DB_PORT"] or "5432"
    database_name = db_config["DB_NAME"] or "tenderai"

    encoded_password = quote_plus(password)
    return f"postgresql://{username}:{encoded_password}@{host}:{port}/{database_name}"


DATABASE_URL = build_database_url()


def initialize_database():
    if DATABASE_URL.startswith("sqlite"):
        engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
        return engine

    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            conn.execute("SELECT 1")
        return engine
    except Exception as exc:
        raise RuntimeError(
            "PostgreSQL configured but unreachable; refusing to silently fall back to SQLite. "
            f"DB_URL={DATABASE_URL}. Original error: {exc}"
        ) from exc


engine = initialize_database()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def log_tool_call(db, tender_id: int, record):
    """
    Persist a single `ToolCallRecord` (app/schemas.py) as a ToolCall row.

    Called only by the orchestrator's persistence step, after the agent graph has
    finished — agents no longer hold a Session, because parallel graph nodes
    cannot safely share one.
    """
    from .models import ToolCall

    db_tool_call = ToolCall(
        tender_id=tender_id,
        agent_name=record.agent_name,
        tool_name=record.tool_name,
        input_arguments=record.input_arguments,
        output_response=record.output_response,
        model_name=record.model_name,
        latency_ms=record.latency_ms,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        total_tokens=record.total_tokens,
        status=record.status,
        error_message=record.error_message,
    )
    db.add(db_tool_call)
    db.commit()
    return db_tool_call

