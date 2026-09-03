"""
app/agents/extraction.py — Metadata extraction agent.

Supports three calling modes controlled by the `context_chunks` parameter:

  full-text (context_chunks=None or [])
      Prompt receives the whole raw_text. Citation fields omitted from schema.

  rag (context_chunks=[...], cite=False)
      Prompt receives retrieved chunks labelled by chunk_id. No citation fields
      requested — the chunks are context, not sources to cite.

  rag-cite (context_chunks=[...], cite=True)
      Same as rag, but the JSON schema includes citation fields so the LLM can
      attribute each field to the chunk it came from. The orchestrator verifies
      those attributions deterministically (field_value ⊆ chunk.text_content).
"""
from typing import List, Optional, Tuple

from .base import BaseAgent
from ..schemas import CitationRef, RetrievedChunk, TenderDetails, ToolCallRecord

# Targeted retrieval queries for this agent. Five concepts, ordered by expected
# information density in government tender documents.
EXTRACTION_QUERIES = [
    "submission deadline date and time",
    "minimum annual turnover financial requirement",
    "earnest money deposit EMD bid security amount",
    "eligibility criteria qualifications required bidders",
    "solvency certificate bank guarantee requirement",
]


def _chunks_block(chunks: List[RetrievedChunk]) -> str:
    """Format retrieved chunks as a numbered, labelled block for the prompt."""
    lines = []
    for c in chunks:
        page = f" | page {c.page_number}" if c.page_number else ""
        lines.append(f"[chunk_id={c.chunk_id}{page}]\n{c.text}\n")
    return "\n".join(lines)


class ExtractionAgent(BaseAgent):
    agent_name = "ExtractionAgent"

    def __init__(self):
        super().__init__(temperature=0.1)

    def extract_details(
        self,
        raw_text: str,
        context_chunks: Optional[List[RetrievedChunk]] = None,
        cite: bool = False,
    ) -> Tuple[TenderDetails, ToolCallRecord]:
        """
        Extract structured eligibility, deadline and financial requirements.

        Parameters
        ----------
        raw_text       : Full document text (used when context_chunks is empty).
        context_chunks : Retrieved chunks from pgvector (None → full-text mode).
        cite           : When True (and context_chunks is provided), request
                         chunk_id citations in the JSON output.
        """
        use_rag = bool(context_chunks)
        chunks_str = _chunks_block(context_chunks) if use_rag else ""
        context_label = "retrieved tender excerpts" if use_rag else "tender document"
        source_block = chunks_str if use_rag else self._bound(raw_text)
        prompt_chars = len(source_block)

        # Citation schema block — only emitted when rag-cite mode is active
        if use_rag and cite:
            citation_hint = (
                '\n  "field_citations": [\n'
                '    {"chunk_id": <int>, "page_number": <int or null>},\n'
                "    ... (one entry per field: tender_title, submission_deadline, overall_summary)\n"
                "  ],\n"
                '  "financial_requirements": {\n'
                "    ...,\n"
                '    "citations": [{"chunk_id": <int>, "page_number": <int or null>}, ...]\n'
                "  }"
            )
            cite_instruction = (
                "\nFor each field, set chunk_id to the chunk_id value from the context "
                "block above that contained the evidence. Use -1 if no chunk supports the value."
            )
        else:
            citation_hint = ""
            cite_instruction = ""

        prompt = f"""You are an expert Tender Analyst Agent. Analyze the following {context_label} \
and extract all relevant information.
Be precise. If a detail is not explicitly mentioned, use null for strings or an empty array for lists.
{cite_instruction}

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "tender_title": "<string>",
  "submission_deadline": "<string or null>",
  "eligibility_criteria": ["<string>", ...],
  "financial_requirements": {{
    "minimum_turnover": "<string or null>",
    "earnest_money_deposit_emd": "<string or null>",
    "solvency_certificate_required": <true|false>,
    "additional_financial_conditions": ["<string>", ...]
  }},
  "overall_summary": "<string>"{citation_hint}
}}

{context_label.capitalize()}:
{source_block}
"""

        return self._invoke(
            prompt=prompt,
            response_model=TenderDetails,
            tool_name="extract_tender_details",
            input_arguments={
                "mode": "rag-cite" if (use_rag and cite) else ("rag" if use_rag else "full"),
                "prompt_chars": prompt_chars,
                "n_chunks": len(context_chunks) if context_chunks else 0,
            },
        )
