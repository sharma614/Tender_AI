"""
app/agents/risk_agent.py — Legal/financial risk evaluation agent.

Targeted queries focus on the clause categories that actually carry contractual
risk: liquidated damages, termination, indemnity, payment. Full-text mode
passes the entire document; RAG mode narrows to the top-k retrieved passages,
shrinking the prompt ~20× on typical tenders.
"""
from typing import List, Optional, Tuple

from .base import BaseAgent
from ..schemas import RetrievedChunk, RiskAnalysis, ToolCallRecord

# Queries are ordered by retrieval importance. On tender documents the penalty
# and termination clauses are the highest-risk items; they also tend to be
# buried in schedules rather than the main body, so targeted retrieval is
# especially valuable here.
RISK_QUERIES = [
    "liquidated damages penalty clause performance guarantee",
    "termination clause exit conditions early termination",
    "indemnity clause liability limit indemnification",
    "payment terms milestone billing retention percentage",
    "force majeure dispute resolution arbitration",
    "delivery schedule timeline milestone dates",
]


def _chunks_block(chunks: List[RetrievedChunk]) -> str:
    lines = []
    for c in chunks:
        page = f" | page {c.page_number}" if c.page_number else ""
        lines.append(f"[chunk_id={c.chunk_id}{page}]\n{c.text}\n")
    return "\n".join(lines)


class RiskAgent(BaseAgent):
    agent_name = "RiskAgent"

    def __init__(self):
        super().__init__(temperature=0.2)

    def evaluate_risks(
        self,
        raw_text: str,
        context_chunks: Optional[List[RetrievedChunk]] = None,
        cite: bool = False,
    ) -> Tuple[RiskAnalysis, ToolCallRecord]:
        """
        Identify legal, operational and financial risk factors in the tender.

        Parameters
        ----------
        raw_text       : Full document text (used when context_chunks is empty).
        context_chunks : Retrieved chunks from pgvector (None → full-text mode).
        cite           : When True, request chunk_id citations per risk item.
        """
        use_rag = bool(context_chunks)
        source_block = _chunks_block(context_chunks) if use_rag else self._bound(raw_text)
        context_label = "retrieved tender excerpts" if use_rag else "tender document"

        cite_instruction = ""
        citation_schema = ""
        if use_rag and cite:
            cite_instruction = (
                "\nFor each identified risk, set citation.chunk_id to the chunk_id "
                "value from the context block where you found the evidence. Use -1 "
                "if no retrieved chunk directly supports the risk."
            )
            citation_schema = (
                '      "citation": {"chunk_id": <int>, "page_number": <int or null>},\n'
            )

        prompt = f"""You are an expert Tender Risk Compliance Officer. Analyze this \
{context_label} and identify all risk factors.
Look specifically for:
- Severe penalty clauses, liquidated damages, or performance bank guarantees
- Aggressive delivery schedules or milestone timelines
- Strict payment terms, delays in reimbursement, or retention clauses
- Liability limits, indemnity clauses, and termination conditions
{cite_instruction}

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "overall_risk_rating": "<Low|Medium|High>",
  "identified_risks": [
    {{
      "risk_item": "<short hazard title>",
      "description": "<detailed description of the risk>",
      "severity": "<Low|Medium|High>",
      "mitigation_strategy": "<concrete mitigation recommendation>",
{citation_schema}    }},
    ...
  ]
}}

{context_label.capitalize()}:
{source_block}
"""

        return self._invoke(
            prompt=prompt,
            response_model=RiskAnalysis,
            tool_name="evaluate_risks",
            input_arguments={
                "mode": "rag-cite" if (use_rag and cite) else ("rag" if use_rag else "full"),
                "prompt_chars": len(source_block),
                "n_chunks": len(context_chunks) if context_chunks else 0,
            },
        )
