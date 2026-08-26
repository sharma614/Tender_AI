import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/tenderai")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def log_tool_call(db, tender_id: int, agent_name: str, tool_name: str, input_arguments: dict, output_response: dict):
    from .models import ToolCall
    db_tool_call = ToolCall(
        tender_id=tender_id,
        agent_name=agent_name,
        tool_name=tool_name,
        input_arguments=input_arguments,
        output_response=output_response
    )
    db.add(db_tool_call)
    db.commit()
    return db_tool_call

