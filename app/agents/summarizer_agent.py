"""
app/agents/summarizer_agent.py — Executive summary and scope-of-work agent.

Summarization is the most forgiving task for retrieval — you want breadth, not
just clause-specific depth — so the queries are intentionally broad (objectives,
scope, deliverables, dates). In full-text mode the LLM receives the entire
document; in RAG mode it works from the top-k retrieved passages.
"""
from typing import List, Optional, Tuple

from .base import BaseAgent
from ..schemas import RetrievedChunk, TenderSummary, ToolCallRecord

SUMMARY_QUERIES = [
    "project objectives scope of work overview purpose",
    "key deliverables milestones work packages",
    "contract duration period of performance timeline",
    "important dates pre-bid meeting bid submission opening",
    "background context procuring entity department",
]


def _chunks_block(chunks: List[RetrievedChunk]) -> str:
    lines = []
    for c in chunks:
        page = f" | page {c.page_number}" if c.page_number else ""
        lines.append(f"[chunk_id={c.chunk_id}{page}]\n{c.text}\n")
    return "\n".join(lines)


class SummarizerAgent(BaseAgent):
    agent_name = "SummarizerAgent"

    def __init__(self):
        super().__init__(temperature=0.2)

    def generate_summary(
        self,
        raw_text: str,
        context_chunks: Optional[List[RetrievedChunk]] = None,
        cite: bool = False,
    ) -> Tuple[TenderSummary, ToolCallRecord]:
        """
        Generate an executive summary, scope highlights and key dates.

        Parameters
        ----------
        raw_text       : Full document text (used when context_chunks is empty).
        context_chunks : Retrieved chunks from pgvector (None → full-text mode).
        cite           : When True, request chunk_id citations in the response.
        """
        use_rag = bool(context_chunks)
        source_block = _chunks_block(context_chunks) if use_rag else self._bound(raw_text)
        context_label = "retrieved tender excerpts" if use_rag else "tender document"

        cite_instruction = ""
        citation_schema = ""
        if use_rag and cite:
            cite_instruction = (
                "\nAt the end of the JSON object, include a 'citations' array listing "
                "the chunk_ids from the context block that contributed to the summary. "
                "Use -1 for any claim not supported by a retrieved chunk."
            )
            citation_schema = (
                '  "citations": [{"chunk_id": <int>, "page_number": <int or null>}, ...],\n'
            )

        prompt = f"""You are a Senior Proposal Specialist. Analyze this \
{context_label} and provide a high-quality summary.
{cite_instruction}

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "executive_summary": "<rich 2-3 paragraph overview of scope and objectives>",
  "scope_of_work": ["<key deliverable or milestone>", ...],
  "important_dates": {{
    "<event name>": "<date string>",
    ...
  }},
{citation_schema}}}

{context_label.capitalize()}:
{source_block}
"""

        return self._invoke(
            prompt=prompt,
            response_model=TenderSummary,
            tool_name="generate_summary",
            input_arguments={
                "mode": "rag-cite" if (use_rag and cite) else ("rag" if use_rag else "full"),
                "prompt_chars": len(source_block),
                "n_chunks": len(context_chunks) if context_chunks else 0,
            },
        )
