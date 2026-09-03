import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/tenderai")

try:
    engine = create_engine(DATABASE_URL)
    # Test connection
    with engine.connect() as conn:
        pass
except Exception:
    # Fallback to local SQLite file database if PostgreSQL is not running
    print("Warning: PostgreSQL server unreachable. Falling back to SQLite database (sqlite:///./tenderai_dev.db).")
    DATABASE_URL = "sqlite:///./tenderai_dev.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

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

