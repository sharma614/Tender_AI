"""
app/metrics.py — Metrics aggregation module for TenderAI observability.
"""
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import Tender, Job, ToolCall

def get_system_metrics(db: Session) -> dict:
    """
    Computes system-wide metrics from PostgreSQL tables:
      - Total tenders processed
      - Job distribution by status (pending, processing, completed, dead_letter)
      - Agent invocation counts & error stats
    """
    total_tenders = db.query(Tender).count()
    
    # Job status distribution
    job_counts_query = db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    job_status_distribution = {status: count for status, count in job_counts_query}
    
    total_jobs = db.query(Job).count()
    completed_jobs = job_status_distribution.get("completed", 0)
    failed_jobs = job_status_distribution.get("dead_letter", 0)
    
    success_rate = (completed_jobs / total_jobs * 100) if total_jobs > 0 else 100.0
    
    # Tool call counts by agent
    agent_calls = db.query(ToolCall.agent_name, func.count(ToolCall.id)).group_by(ToolCall.agent_name).all()
    agent_usage = {agent: count for agent, count in agent_calls}
    
    return {
        "summary": {
            "total_tenders": total_tenders,
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
            "failed_jobs": failed_jobs,
            "success_rate_percent": round(success_rate, 2),
        },
        "job_status_distribution": job_status_distribution,
        "agent_activity": agent_usage,
    }
