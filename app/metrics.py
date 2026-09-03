"""
app/metrics.py — Metrics aggregation module for TenderAI observability.

Reports per-agent latency, token usage and error rates from the instrumentation
columns on `tool_calls`. Percentiles are computed in Python rather than with
PostgreSQL's `percentile_cont`, so the same code path works against the SQLite
engine used by the test suite.
"""
from collections import defaultdict
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Job, Tender, ToolCall


def _percentile(sorted_values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile of an already-sorted, non-empty list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = max(0, min(len(sorted_values) - 1, int(round(pct / 100.0 * (len(sorted_values) - 1)))))
    return sorted_values[rank]


def get_system_metrics(db: Session) -> dict:
    """
    Computes system-wide metrics from PostgreSQL tables:
      - Total tenders processed
      - Job distribution by status (pending, processing, completed, dead_letter)
      - Per-agent invocation counts, latency (mean/p50/p95), token usage, errors
    """
    total_tenders = db.query(Tender).count()

    # Job status distribution
    job_counts_query = db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    job_status_distribution = {status: count for status, count in job_counts_query}

    total_jobs = db.query(Job).count()
    completed_jobs = job_status_distribution.get("completed", 0)
    failed_jobs = job_status_distribution.get("dead_letter", 0)

    success_rate = (completed_jobs / total_jobs * 100) if total_jobs > 0 else 100.0

    # --- Per-agent observability -------------------------------------------
    # Pulls the instrumentation columns and aggregates in Python; volumes here
    # are per-tender, not analytics-scale.
    rows = db.query(
        ToolCall.agent_name,
        ToolCall.status,
        ToolCall.latency_ms,
        ToolCall.prompt_tokens,
        ToolCall.completion_tokens,
        ToolCall.total_tokens,
    ).all()

    grouped: Dict[str, Dict[str, list]] = defaultdict(
        lambda: {"latencies": [], "prompt": [], "completion": [], "total": [], "calls": 0, "errors": 0}
    )
    for agent_name, status, latency, prompt_t, completion_t, total_t in rows:
        bucket = grouped[agent_name]
        bucket["calls"] += 1
        if status == "error":
            bucket["errors"] += 1
        if latency is not None:
            bucket["latencies"].append(latency)
        if prompt_t is not None:
            bucket["prompt"].append(prompt_t)
        if completion_t is not None:
            bucket["completion"].append(completion_t)
        if total_t is not None:
            bucket["total"].append(total_t)

    agent_activity = {}
    for agent_name, bucket in grouped.items():
        latencies = sorted(bucket["latencies"])
        calls = bucket["calls"]
        errors = bucket["errors"]
        agent_activity[agent_name] = {
            "calls": calls,
            "errors": errors,
            "success_rate_percent": round((calls - errors) / calls * 100, 2) if calls else None,
            "latency_ms": {
                "mean": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "max": latencies[-1] if latencies else None,
            },
            "tokens": {
                "prompt_total": sum(bucket["prompt"]) if bucket["prompt"] else None,
                "completion_total": sum(bucket["completion"]) if bucket["completion"] else None,
                "total": sum(bucket["total"]) if bucket["total"] else None,
                "mean_total_per_call": (
                    round(sum(bucket["total"]) / len(bucket["total"]), 1) if bucket["total"] else None
                ),
            },
        }

    all_total_tokens = [t for bucket in grouped.values() for t in bucket["total"]]

    return {
        "summary": {
            "total_tenders": total_tenders,
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
            "failed_jobs": failed_jobs,
            "success_rate_percent": round(success_rate, 2),
            "total_agent_calls": len(rows),
            "total_tokens_consumed": sum(all_total_tokens) if all_total_tokens else None,
        },
        "job_status_distribution": job_status_distribution,
        "agent_activity": agent_activity,
    }
