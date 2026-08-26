import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from .database import Base

class Tender(Base):
    __tablename__ = "tenders"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    raw_text = Column(Text, nullable=True)
    s3_key = Column(String, nullable=True, index=True)   # S3 object key when uploaded via AWS path
    
    # Persistent storage for structured analysis results
    summary = Column(JSON, nullable=True)
    risks = Column(JSON, nullable=True)
    compliance_status = Column(JSON, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    chunks = relationship("TenderChunk", back_populates="tender", cascade="all, delete-orphan")
    tool_calls = relationship("ToolCall", back_populates="tender", cascade="all, delete-orphan")


class TenderChunk(Base):
    __tablename__ = "tender_chunks"

    id = Column(Integer, primary_key=True, index=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text_content = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=True)  # MiniLM-L6-v2 is 384 dimensions

    tender = relationship("Tender", back_populates="chunks")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id = Column(Integer, primary_key=True, index=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False)
    agent_name = Column(String, index=True, nullable=False)
    tool_name = Column(String, nullable=False)
    input_arguments = Column(JSON, nullable=True)
    output_response = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    tender = relationship("Tender", back_populates="tool_calls")


class Job(Base):
    """
    Tracks async processing jobs (SQS-style).
    Status state machine:
      pending -> processing -> completed
                            -> retrying  (re-queued with backoff)
                            -> dead_letter (max retries exceeded)
    """
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=True)  # Null until tender is saved
    celery_task_id = Column(String, index=True, nullable=True)            # Celery task UUID for tracking
    status = Column(String, default="pending", index=True, nullable=False) # pending|processing|retrying|completed|dead_letter
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)
    error_message = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)                                   # Final AgentState output
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

