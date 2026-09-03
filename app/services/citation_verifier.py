"""
app/services/citation_verifier.py — Deterministic grounding verification.

Every extracted field and risk item in 'rag-cite' mode carries a chunk_id
pointing to the TenderChunk from which it was drawn. This module checks, purely
by string containment, whether the asserted value actually appears in the
referenced chunk.

Why string containment, not an LLM judge
-----------------------------------------
String containment is:
  - Deterministic: same input, same output, no API call.
  - Arguable: the substring is either there or it is not.
  - Fast: O(n) in chunk length.
  - Independent: verifying citations does not consume any tokens.

A failed citation (value does not appear in the referenced chunk) is counted
as a hallucination in the ablation grid. A chunk_id == -1 is the LLM's own
admission that it cannot ground the value, and is scored as ungrounded (not
hallucinated — the distinction matters for the paper).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_WS = re.compile(r"\s+")
_PAGE_MARKER = re.compile(r"---\s*Page\s+\d+\s*---", re.IGNORECASE)


def _norm(text: str) -> str:
    """Collapse whitespace, strip page markers, lowercase."""
    return _WS.sub(" ", _PAGE_MARKER.sub(" ", text)).strip().lower()


def _contains(haystack: str, needle: str, min_len: int = 8) -> bool:
    """
    True if a meaningful substring of `needle` appears in `haystack`.

    For short values (< min_len chars after normalisation) we require exact
    containment. For longer values we use a sliding window of `min_len` chars
    so that minor OCR/whitespace differences don't cause false negatives.
    """
    hay = _norm(haystack)
    ndl = _norm(needle)
    if not ndl:
        return False  # empty values cannot be verified
    if len(ndl) <= min_len:
        return ndl in hay
    # Sliding window: any contiguous min_len-char window found → grounded
    step = max(1, min_len // 2)
    return any(ndl[i:i + min_len] in hay for i in range(0, len(ndl) - min_len + 1, step))


class CitationVerificationResult:
    """
    Per-document citation verification summary.

    Attributes
    ----------
    total        : number of citations checked
    grounded     : cited chunk contained the field value (substring present)
    ungrounded   : chunk_id == -1 (LLM admitted no attribution)
    hallucinated : chunk_id != -1 but value not found in chunk text
    missing_chunk: chunk_id not found in the provided chunk map
    grounding_precision : grounded / (total - ungrounded)  (excludes admitted gaps)
    hallucination_rate  : hallucinated / total
    """

    def __init__(self):
        self.total = 0
        self.grounded = 0
        self.ungrounded = 0
        self.hallucinated = 0
        self.missing_chunk = 0
        self.detail: List[Dict[str, Any]] = []

    @property
    def grounding_precision(self) -> Optional[float]:
        denom = self.total - self.ungrounded
        return self.grounded / denom if denom > 0 else None

    @property
    def hallucination_rate(self) -> Optional[float]:
        return self.hallucinated / self.total if self.total > 0 else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "grounded": self.grounded,
            "ungrounded": self.ungrounded,
            "hallucinated": self.hallucinated,
            "missing_chunk": self.missing_chunk,
            "grounding_precision": self.grounding_precision,
            "hallucination_rate": self.hallucination_rate,
            "detail": self.detail,
        }


def verify_citations(
    state_dict: Dict[str, Any],
    chunk_map: Dict[int, str],
) -> CitationVerificationResult:
    """
    Verify all citations in an AgentState serialised as a dict.

    Parameters
    ----------
    state_dict : AgentState.model_dump() output from a 'rag-cite' run.
    chunk_map  : {chunk_id: text_content} — loaded from TenderChunk rows for
                 the same tender.  Must include all chunk_ids that appear in
                 state_dict.

    Returns a CitationVerificationResult with per-field detail.
    """
    result = CitationVerificationResult()

    def _check(field_name: str, field_value: Optional[str], chunk_id: int, page: Optional[int]):
        result.total += 1
        entry: Dict[str, Any] = {
            "field": field_name,
            "value": field_value,
            "chunk_id": chunk_id,
            "page": page,
            "status": None,
        }
        if chunk_id == -1:
            result.ungrounded += 1
            entry["status"] = "ungrounded"
        elif chunk_id not in chunk_map:
            result.missing_chunk += 1
            entry["status"] = "missing_chunk"
        elif not field_value:
            # null/empty fields can't be verified — treat as ungrounded
            result.ungrounded += 1
            entry["status"] = "ungrounded_null"
        elif _contains(chunk_map[chunk_id], field_value):
            result.grounded += 1
            entry["status"] = "grounded"
        else:
            result.hallucinated += 1
            entry["status"] = "hallucinated"
        result.detail.append(entry)

    # --- TenderDetails field_citations ---
    facts = state_dict.get("extracted_facts") or {}
    field_cits = facts.get("field_citations", [])
    field_values = [
        ("tender_title", facts.get("tender_title")),
        ("submission_deadline", facts.get("submission_deadline")),
        ("overall_summary", facts.get("overall_summary")),
    ]
    for i, (fname, fval) in enumerate(field_values):
        if i < len(field_cits):
            cit = field_cits[i]
            _check(f"extraction.{fname}", fval, cit.get("chunk_id", -1), cit.get("page_number"))

    # --- FinancialRequirements citations ---
    fin = facts.get("financial_requirements") or {}
    fin_cits = fin.get("citations", [])
    fin_fields = [
        ("minimum_turnover", fin.get("minimum_turnover")),
        ("earnest_money_deposit_emd", fin.get("earnest_money_deposit_emd")),
    ]
    for i, (fname, fval) in enumerate(fin_fields):
        if i < len(fin_cits):
            cit = fin_cits[i]
            _check(f"financial.{fname}", fval, cit.get("chunk_id", -1), cit.get("page_number"))

    # --- RiskPoint citations ---
    risk_analysis = state_dict.get("risk_analysis") or {}
    for risk in risk_analysis.get("identified_risks", []):
        cit = risk.get("citation") or {}
        _check(
            f"risk.{risk.get('risk_item', '?')[:40]}",
            risk.get("description"),
            cit.get("chunk_id", -1),
            cit.get("page_number"),
        )

    # --- TenderSummary citations ---
    summary = state_dict.get("summary") or {}
    for cit in summary.get("citations", []):
        # Summary citations support the executive_summary field
        _check(
            "summary.executive_summary",
            summary.get("executive_summary"),
            cit.get("chunk_id", -1),
            cit.get("page_number"),
        )

    return result
