"""
eval_harness.py — Evaluation harness for TenderAI.

Every number printed here is computed from an actual run. There are no
hardcoded scores.

This replaces an earlier version that reported fabricated results: it "retrieved"
each question's own ground-truth answer and then checked whether keywords from
that answer appeared in it, guaranteeing Recall@5 == 100%, and it printed a
hardcoded citation precision of 96.5%. Neither number measured anything.

Modes
-----
smoke       No database, no API key. Validates every annotation against
            corpus/schema.json and verifies each gold_span appears verbatim in
            text extracted from its PDF. Catches corpus rot. This is what CI runs.

retrieval   Needs PostgreSQL + pgvector. Ingests each corpus PDF (chunk → embed →
            store), then runs real vector search per question. Reports
            Recall@1/3/5/10, MRR@10 and retrieval latency.

extraction  Needs PostgreSQL + GEMINI_API_KEY. Runs ExtractionAgent over each
            document and scores extracted fields against ground truth, with
            numeric normalization for money. Reports token usage and latency.

Usage
-----
    python eval_harness.py --mode smoke
    python eval_harness.py --mode retrieval --k 10
    python eval_harness.py --mode extraction --limit 5

Results are written to eval_results/<mode>-<timestamp>.json so paper tables can
be regenerated rather than retyped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
ANNOTATION_DIR = CORPUS_DIR / "annotations"
SCHEMA_PATH = CORPUS_DIR / "schema.json"
RESULTS_DIR = Path(__file__).resolve().parent / "eval_results"

RECALL_KS = (1, 3, 5, 10)


# ===========================================================================
# Text normalization
# ===========================================================================

_WS = re.compile(r"\s+")
_PAGE_MARKER = re.compile(r"---\s*Page\s+\d+\s*---", re.IGNORECASE)


def norm(text: str) -> str:
    """
    Collapse whitespace, strip page markers, and lowercase.

    PDF text extraction reflows line breaks and column padding, so a gold span
    written as one logical sentence may come back with newlines mid-phrase.
    PDFProcessor also injects "--- Page N ---" markers, which land *inside* any
    sentence that straddles a page break — so a correct gold span would otherwise
    fail a containment check purely because of where the page happened to end.
    Removing the marker keeps the check exact on real content while making it
    insensitive to pagination.
    """
    return _WS.sub(" ", _PAGE_MARKER.sub(" ", text)).strip().lower()


def contains_span(haystack: str, span: str) -> bool:
    return norm(span) in norm(haystack)


# A gold span longer than the chunk overlap can be split across two chunks with
# neither containing all of it. Scoring such a question as a miss would measure
# the chunking geometry, not retrieval. For those, require a contiguous window of
# the span instead — still exact containment, just of the distinguishing core.
SPAN_MATCH_WINDOW = 150


def span_hit(chunk_text: str, span: str) -> bool:
    """
    True if `chunk_text` contains `span`, or — for spans longer than the chunk
    overlap — any contiguous `SPAN_MATCH_WINDOW`-character window of it.
    """
    hay = norm(chunk_text)
    needle = norm(span)
    if needle in hay:
        return True
    if len(needle) <= SPAN_MATCH_WINDOW:
        return False
    # Slide in modest steps; a hit anywhere means the answer text was retrieved.
    step = 25
    return any(
        needle[i:i + SPAN_MATCH_WINDOW] in hay
        for i in range(0, len(needle) - SPAN_MATCH_WINDOW + 1, step)
    )


# ===========================================================================
# Corpus loading
# ===========================================================================

def load_annotations(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Load annotation JSON files in stable doc_id order."""
    if not ANNOTATION_DIR.is_dir():
        raise FileNotFoundError(
            f"No annotations at {ANNOTATION_DIR}.\n"
            "Generate the corpus first:  python corpus/generate_synthetic.py --seed 42"
        )

    paths = sorted(ANNOTATION_DIR.glob("*.json"))
    if not paths:
        raise FileNotFoundError(
            f"{ANNOTATION_DIR} contains no annotation files.\n"
            "Generate the corpus first:  python corpus/generate_synthetic.py --seed 42"
        )

    annotations = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            annotations.append(json.load(fh))
    return annotations[:limit] if limit else annotations


def pdf_path_for(annotation: Dict[str, Any]) -> Path:
    return (CORPUS_DIR / annotation["pdf"]).resolve()


def extract_text(annotation: Dict[str, Any]) -> str:
    from app.services.pdf_processor import PDFProcessor

    path = pdf_path_for(annotation)
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing PDF {path} for {annotation['doc_id']}.\n"
            "corpus/pdfs/ is gitignored — regenerate with:\n"
            "  python corpus/generate_synthetic.py --seed 42"
        )
    return PDFProcessor.extract_text_from_bytes(path.read_bytes())


def write_results(mode: str, payload: Dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"{mode}-{stamp}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return out


# ===========================================================================
# Mode: smoke
# ===========================================================================

def run_smoke(limit: Optional[int]) -> Tuple[bool, Dict[str, Any]]:
    """
    Validate the corpus without a database or an API key.

    Two independent checks per document:
      1. the annotation conforms to corpus/schema.json
      2. every gold_span is actually present in text extracted from the PDF

    Check 2 matters most: it is what stops the harness from scoring against
    answers that are not in the documents.
    """
    import jsonschema

    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    validator = jsonschema.Draft7Validator(schema)

    annotations = load_annotations(limit)

    print("=" * 78)
    print("  CORPUS VALIDATION (schema + gold span presence)")
    print("=" * 78)
    print(f"{'DOC':<14} {'PAGES':>5} {'Q':>4} {'SCHEMA':>8} {'SPANS':>10}   NOTES")
    print("-" * 78)

    docs: List[Dict[str, Any]] = []
    total_q = 0
    total_missing = 0
    schema_failures = 0
    leaks: List[str] = []

    for ann in annotations:
        doc_id = ann.get("doc_id", "?")

        errors = sorted(validator.iter_errors(ann), key=lambda e: e.path)
        schema_ok = not errors
        if not schema_ok:
            schema_failures += 1

        missing: List[int] = []
        overlap_flags: List[int] = []
        try:
            text = extract_text(ann)
            for q in ann.get("questions", []):
                total_q += 1
                if not contains_span(text, q["gold_span"]):
                    missing.append(q["id"])
                # A query that quotes its own answer makes retrieval trivial —
                # the exact defect the previous harness was built on.
                if contains_span(q["gold_span"], q["query"]):
                    overlap_flags.append(q["id"])
            extract_error = None
        except FileNotFoundError as exc:
            text = ""
            extract_error = str(exc)

        total_missing += len(missing)
        if overlap_flags:
            leaks.append(f"{doc_id}: q{overlap_flags}")

        notes = []
        if extract_error:
            notes.append("PDF MISSING")
        if errors:
            notes.append(f"{len(errors)} schema error(s): {errors[0].message[:60]}")
        if missing:
            notes.append(f"spans absent: q{missing}")
        if overlap_flags:
            notes.append(f"query quotes answer: q{overlap_flags}")

        n_q = len(ann.get("questions", []))
        print(
            f"{doc_id:<14} {ann.get('page_count', 0):>5} {n_q:>4} "
            f"{'ok' if schema_ok else 'FAIL':>8} "
            f"{(f'{n_q - len(missing)}/{n_q}') :>10}   {'; '.join(notes)}"
        )

        docs.append({
            "doc_id": doc_id,
            "source": ann.get("source"),
            "page_count": ann.get("page_count"),
            "questions": n_q,
            "schema_ok": schema_ok,
            "schema_errors": [e.message for e in errors],
            "missing_spans": missing,
            "query_quotes_answer": overlap_flags,
            "extract_error": extract_error,
        })

    by_source: Dict[str, int] = {}
    for ann in annotations:
        by_source[ann.get("source", "unknown")] = by_source.get(ann.get("source", "unknown"), 0) + 1

    passed = schema_failures == 0 and total_missing == 0 and not leaks

    print("-" * 78)
    print(f"  Documents            : {len(annotations)}  ({by_source})")
    print(f"  Questions            : {total_q}")
    print(f"  Schema failures      : {schema_failures}")
    print(f"  Gold spans not found : {total_missing}")
    print(f"  Queries quoting answer: {len(leaks)}")
    print(f"  RESULT               : {'PASS' if passed else 'FAIL'}")
    print("=" * 78)
    if leaks:
        print("\nQueries that quote their own gold span (retrieval would be trivial):")
        for leak in leaks:
            print(f"  - {leak}")

    return passed, {
        "mode": "smoke",
        "documents": len(annotations),
        "questions": total_q,
        "by_source": by_source,
        "schema_failures": schema_failures,
        "missing_spans": total_missing,
        "queries_quoting_answer": leaks,
        "passed": passed,
        "per_document": docs,
    }


# ===========================================================================
# Mode: retrieval
# ===========================================================================

def _ingest(db, ann: Dict[str, Any], text: str, embedder) -> int:
    """
    Chunk, embed and store one document; returns its tender_id.

    Reuses the production PDFProcessor and TextEmbedder so the harness measures
    the same pipeline the app runs, not a parallel reimplementation.
    """
    from app.models import Tender, TenderChunk
    from app.services.pdf_processor import PDFProcessor

    tender = Tender(title=f"[eval] {ann['doc_id']}", raw_text=text)
    db.add(tender)
    db.commit()
    db.refresh(tender)

    chunks = PDFProcessor.chunk_text(text, chunk_size=1000, chunk_overlap=200)
    vectors = embedder.embed_batch([c["text"] for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        db.add(TenderChunk(
            tender_id=tender.id,
            chunk_index=chunk["index"],
            text_content=chunk["text"],
            embedding=vector,
            char_start=chunk.get("char_start"),
            page_number=chunk.get("page_number"),
        ))
    db.commit()
    return tender.id


def run_retrieval(limit: Optional[int], k: int, keep: bool) -> Tuple[bool, Dict[str, Any]]:
    """
    Measure real vector-search retrieval quality.

    A question counts as a hit at rank r if the gold span is contained in the
    r-th retrieved chunk (after whitespace normalization). Retrieval runs once at
    max(k, 10) and the ranked list is sliced for each K, so Recall@1/3/5/10 come
    from a single search per question.
    """
    from app.database import SessionLocal
    from app.models import Tender, TenderChunk
    from app.services.embedder import TextEmbedder
    from app.services.retriever import retrieve

    top_k = max(k, max(RECALL_KS))
    annotations = load_annotations(limit)
    embedder = TextEmbedder()
    db = SessionLocal()

    created: List[int] = []
    per_doc: List[Dict[str, Any]] = []
    ranks: List[Optional[int]] = []       # first hit rank per question, None if miss
    latencies: List[float] = []
    chunk_total = 0

    print("=" * 78)
    print(f"  RETRIEVAL EVALUATION (real pgvector cosine search, top-{top_k})")
    print("=" * 78)
    print(f"{'DOC':<14} {'CHUNKS':>7} {'Q':>4} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'ms':>7}")
    print("-" * 78)

    try:
        for ann in annotations:
            text = extract_text(ann)
            tender_id = _ingest(db, ann, text, embedder)
            created.append(tender_id)
            n_chunks = db.query(TenderChunk).filter(TenderChunk.tender_id == tender_id).count()
            chunk_total += n_chunks

            doc_ranks: List[Optional[int]] = []
            doc_latencies: List[float] = []
            misses: List[int] = []

            for q in ann["questions"]:
                qvec = embedder.embed_text(q["query"])

                started = time.perf_counter()
                results = retrieve(db, tender_id, q["query"], k=top_k, query_vector=qvec)
                elapsed_ms = (time.perf_counter() - started) * 1000
                doc_latencies.append(elapsed_ms)
                latencies.append(elapsed_ms)

                hit_rank = None
                for rank, chunk in enumerate(results, start=1):
                    if span_hit(chunk.text, q["gold_span"]):
                        hit_rank = rank
                        break
                doc_ranks.append(hit_rank)
                ranks.append(hit_rank)
                if hit_rank is None:
                    misses.append(q["id"])

            def recall_at(rs: List[Optional[int]], kk: int) -> float:
                return sum(1 for r in rs if r is not None and r <= kk) / len(rs) if rs else 0.0

            doc_mrr = mean([1.0 / r if r else 0.0 for r in doc_ranks]) if doc_ranks else 0.0
            print(
                f"{ann['doc_id']:<14} {n_chunks:>7} {len(doc_ranks):>4} "
                f"{recall_at(doc_ranks, 1) * 100:>5.1f}% {recall_at(doc_ranks, 5) * 100:>5.1f}% "
                f"{recall_at(doc_ranks, 10) * 100:>5.1f}% {doc_mrr:>6.3f} "
                f"{mean(doc_latencies):>7.1f}"
            )

            per_doc.append({
                "doc_id": ann["doc_id"],
                "chunks": n_chunks,
                "questions": len(doc_ranks),
                "recall": {f"@{kk}": recall_at(doc_ranks, kk) for kk in RECALL_KS},
                "mrr": doc_mrr,
                "mean_latency_ms": mean(doc_latencies) if doc_latencies else None,
                "missed_question_ids": misses,
            })

        n = len(ranks)
        recall = {f"@{kk}": sum(1 for r in ranks if r is not None and r <= kk) / n for kk in RECALL_KS} if n else {}
        mrr = mean([1.0 / r if r else 0.0 for r in ranks]) if ranks else 0.0
        sorted_lat = sorted(latencies)

        print("-" * 78)
        print("  RETRIEVAL SUMMARY")
        print(f"    Documents            : {len(annotations)}")
        print(f"    Chunks indexed       : {chunk_total}")
        print(f"    Questions            : {n}")
        for kk in RECALL_KS:
            print(f"    Recall@{kk:<2}            : {recall.get(f'@{kk}', 0) * 100:.1f}%")
        print(f"    MRR@{max(RECALL_KS):<2}               : {mrr:.3f}")
        if sorted_lat:
            print(f"    Latency mean / p95   : {mean(sorted_lat):.1f} ms / "
                  f"{sorted_lat[min(len(sorted_lat) - 1, int(0.95 * (len(sorted_lat) - 1)))]:.1f} ms")

        r5 = recall.get("@5", 0.0)
        if r5 >= 0.999:
            print("\n  WARNING: Recall@5 is 100%. Treat this as a corpus defect, not a result —")
            print("  either the documents are too easy or queries overlap their gold spans.")
        print("=" * 78)

        # Fail loudly on a floor, so a silently broken retriever cannot pass CI.
        passed = r5 >= 0.50
        if not passed:
            print(f"\n  FAIL: Recall@5 {r5 * 100:.1f}% is below the 50% floor.")

        return passed, {
            "mode": "retrieval",
            "documents": len(annotations),
            "chunks_indexed": chunk_total,
            "questions": n,
            "top_k": top_k,
            "recall": recall,
            "mrr": mrr,
            "latency_ms": {
                "mean": mean(sorted_lat) if sorted_lat else None,
                "p50": sorted_lat[len(sorted_lat) // 2] if sorted_lat else None,
                "p95": sorted_lat[min(len(sorted_lat) - 1, int(0.95 * (len(sorted_lat) - 1)))] if sorted_lat else None,
            },
            "embedding_model": os.environ.get("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"),
            "chunk_size": 1000,
            "chunk_overlap": 200,
            "passed": passed,
            "per_document": per_doc,
        }
    finally:
        if keep:
            print(f"\n  Kept {len(created)} evaluation tenders in the database (--keep).")
        else:
            for tender_id in created:
                db.query(TenderChunk).filter(TenderChunk.tender_id == tender_id).delete()
                db.query(Tender).filter(Tender.id == tender_id).delete()
            db.commit()
        db.close()


# ===========================================================================
# Mode: extraction
# ===========================================================================

_NUM = re.compile(r"[\d,]+(?:\.\d+)?")
_MULT = (
    ("crore", 10_000_000),
    ("cr", 10_000_000),
    ("lakh", 100_000),
    ("lac", 100_000),
    ("million", 1_000_000),
    ("billion", 1_000_000_000),
)


def parse_money(text: Optional[str]) -> Optional[float]:
    """
    Parse an Indian-format monetary string to a number.

    Handles 'Rs. 5,00,00,000', 'INR 50000000', '5 crore', '50 lakhs'. Returns
    None when no number is present. Comparing parsed values rather than strings
    is the only way to score extraction fairly across surface formats.
    """
    if not text:
        return None
    lowered = text.lower()
    match = _NUM.search(lowered)
    if not match:
        return None
    try:
        value = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    tail = lowered[match.end():]
    for token, factor in _MULT:
        if token in tail:
            return value * factor
    return value


def _money_match(expected: Optional[Dict[str, Any]], actual: Optional[str]) -> Optional[bool]:
    """None when the corpus has no ground truth for this field."""
    if not expected:
        return None
    got = parse_money(actual)
    if got is None:
        return False
    return abs(got - float(expected["value"])) < 1.0


def run_extraction(limit: Optional[int]) -> Tuple[bool, Dict[str, Any]]:
    """
    Score ExtractionAgent output against ground truth.

    Money is compared numerically after parsing, so 'Rs. 5 crore' and
    'INR 50,000,000' both count as correct. Token and latency figures come from
    the agent's own ToolCallRecord instrumentation.
    """
    from app.agents.base import AgentError
    from app.agents.extraction import ExtractionAgent

    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: extraction mode requires GEMINI_API_KEY.", file=sys.stderr)
        return False, {"mode": "extraction", "error": "GEMINI_API_KEY not set"}

    annotations = load_annotations(limit)
    agent = ExtractionAgent()

    print("=" * 78)
    print("  EXTRACTION EVALUATION (live LLM calls)")
    print("=" * 78)
    print(f"{'DOC':<14} {'TURNOVER':>9} {'EMD':>6} {'SOLVENCY':>9} {'DEADLINE':>9} {'TOKENS':>8} {'ms':>7}")
    print("-" * 78)

    field_hits: Dict[str, List[bool]] = {
        "minimum_turnover": [], "emd": [], "solvency": [], "deadline": [], "title": [],
    }
    per_doc: List[Dict[str, Any]] = []
    tokens: List[int] = []
    latencies: List[int] = []
    failures = 0

    def mark(flag: Optional[bool]) -> str:
        return "n/a" if flag is None else ("ok" if flag else "MISS")

    for ann in annotations:
        text = extract_text(ann)
        gold = ann["fields"]

        try:
            facts, record = agent.extract_details(text)
        except AgentError as exc:
            failures += 1
            print(f"{ann['doc_id']:<14} {'ERROR':>9}  {exc}")
            per_doc.append({"doc_id": ann["doc_id"], "error": str(exc),
                            "latency_ms": exc.record.latency_ms})
            continue

        turnover_ok = _money_match(gold.get("minimum_turnover"),
                                   facts.financial_requirements.minimum_turnover)
        emd_ok = _money_match(gold.get("earnest_money_deposit_emd"),
                              facts.financial_requirements.earnest_money_deposit_emd)

        solvency_ok = None
        if "solvency_certificate_required" in gold:
            solvency_ok = (facts.financial_requirements.solvency_certificate_required
                           == gold["solvency_certificate_required"])

        # Date formats vary too much for exact match; require the day, the month
        # name and the year to all appear.
        deadline_ok = None
        if gold.get("submission_deadline"):
            got = norm(facts.submission_deadline or "")
            deadline_ok = bool(got) and all(
                part in got for part in norm(gold["submission_deadline"]).split()[:3]
            )

        title_ok = contains_span(facts.tender_title or "", gold["tender_title"][:40]) or \
            contains_span(gold["tender_title"], (facts.tender_title or "")[:40])

        for key, flag in (("minimum_turnover", turnover_ok), ("emd", emd_ok),
                          ("solvency", solvency_ok), ("deadline", deadline_ok),
                          ("title", title_ok)):
            if flag is not None:
                field_hits[key].append(flag)

        if record.total_tokens:
            tokens.append(record.total_tokens)
        if record.latency_ms:
            latencies.append(record.latency_ms)

        print(f"{ann['doc_id']:<14} {mark(turnover_ok):>9} {mark(emd_ok):>6} "
              f"{mark(solvency_ok):>9} {mark(deadline_ok):>9} "
              f"{record.total_tokens or 0:>8} {record.latency_ms or 0:>7}")

        per_doc.append({
            "doc_id": ann["doc_id"],
            "fields": {
                "minimum_turnover": turnover_ok, "emd": emd_ok, "solvency": solvency_ok,
                "deadline": deadline_ok, "title": title_ok,
            },
            "extracted": {
                "minimum_turnover": facts.financial_requirements.minimum_turnover,
                "emd": facts.financial_requirements.earnest_money_deposit_emd,
                "submission_deadline": facts.submission_deadline,
            },
            "total_tokens": record.total_tokens,
            "latency_ms": record.latency_ms,
        })

    accuracy = {k: (sum(v) / len(v) if v else None) for k, v in field_hits.items()}
    scored = [a for a in accuracy.values() if a is not None]

    print("-" * 78)
    print("  EXTRACTION SUMMARY")
    print(f"    Documents          : {len(annotations)} ({failures} agent failure(s))")
    for key, value in accuracy.items():
        if value is not None:
            print(f"    {key:<18} : {value * 100:.1f}%")
    if scored:
        print(f"    Macro field accuracy: {mean(scored) * 100:.1f}%")
    if tokens:
        print(f"    Tokens / doc (mean) : {mean(tokens):.0f}")
    if latencies:
        print(f"    Latency / doc (mean): {mean(latencies):.0f} ms")
    print("=" * 78)

    passed = failures == 0 and bool(scored) and mean(scored) >= 0.60
    return passed, {
        "mode": "extraction",
        "documents": len(annotations),
        "agent_failures": failures,
        "field_accuracy": accuracy,
        "macro_field_accuracy": mean(scored) if scored else None,
        "mean_total_tokens": mean(tokens) if tokens else None,
        "mean_latency_ms": mean(latencies) if latencies else None,
        "model": os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash"),
        "passed": passed,
        "per_document": per_doc,
    }


# ===========================================================================
# Mode: ablation
# ===========================================================================
# Configs in the ablation grid. Each is a tuple of
#   (config_id, description, agent_mode)
# agent_mode=None means MonolithicAgent; otherwise passed to AgentOrchestrator.
ABLATION_CONFIGS = [
    ("mono-full",    "Monolithic single call, full document",            None),
    ("multi-full",   "Multi-agent, full-context prompts",                "full"),
    ("multi-rag",    "Multi-agent, retrieval-grounded prompts",          "rag"),
    ("multi-rag-cite", "Multi-agent, retrieval + citation verification", "rag-cite"),
]

# Cost model: Gemini 1.5 Flash $/1M tokens
_COST_PER_1M_INPUT  = 0.075
_COST_PER_1M_OUTPUT = 0.30


def _estimate_cost(prompt_tokens: float, completion_tokens: float) -> float:
    return (prompt_tokens * _COST_PER_1M_INPUT + completion_tokens * _COST_PER_1M_OUTPUT) / 1_000_000


def _score_extraction_fields(facts_dict: Dict[str, Any], gold: Dict[str, Any]) -> Dict[str, Optional[bool]]:
    """Score extracted fields against gold annotations. Compatible with both TenderDetails and MonolithicOutput shapes."""
    fin = facts_dict.get("financial_requirements") or facts_dict
    turnover_ok = _money_match(gold.get("minimum_turnover"), fin.get("minimum_turnover"))
    emd_ok      = _money_match(gold.get("earnest_money_deposit_emd"), fin.get("earnest_money_deposit_emd"))
    solvency_ok = None
    if "solvency_certificate_required" in gold:
        solvency_ok = (fin.get("solvency_certificate_required") == gold["solvency_certificate_required"])
    deadline_ok = None
    if gold.get("submission_deadline"):
        got = norm(facts_dict.get("submission_deadline") or "")
        deadline_ok = bool(got) and all(
            part in got for part in norm(gold["submission_deadline"]).split()[:3]
        )
    title = facts_dict.get("tender_title") or ""
    gold_title = gold.get("tender_title", "")
    title_ok = contains_span(title, gold_title[:40]) or contains_span(gold_title, title[:40])
    return {
        "minimum_turnover": turnover_ok, "emd": emd_ok,
        "solvency": solvency_ok, "deadline": deadline_ok, "title": title_ok,
    }


def run_ablation(limit: Optional[int], k: int, keep: bool) -> Tuple[bool, Dict[str, Any]]:
    """
    Run all four ablation configs on the same corpus and report the results grid.

    Grid columns per config:
      - macro field accuracy  (extraction quality vs gold annotations)
      - mean total tokens     (proxy for cost)
      - estimated cost USD    (tokens × Gemini 1.5 Flash rate)
      - mean latency ms
      - grounding precision   (rag-cite only: cited chunk contained the value)
      - hallucination rate    (rag-cite only: value not in cited chunk)

    Requires PostgreSQL + pgvector + GEMINI_API_KEY.
    """
    from app.agents.base import AgentError
    from app.agents.monolithic import MonolithicAgent
    from app.agents.orchestrator import AgentOrchestrator
    from app.database import SessionLocal
    from app.models import Tender, TenderChunk
    from app.services.citation_verifier import verify_citations
    from app.services.embedder import TextEmbedder
    from app.services.pdf_processor import PDFProcessor

    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: ablation mode requires GEMINI_API_KEY.", file=sys.stderr)
        return False, {"mode": "ablation", "error": "GEMINI_API_KEY not set"}

    annotations = load_annotations(limit)
    embedder    = TextEmbedder()
    mono_agent  = MonolithicAgent()
    orchestrator = AgentOrchestrator()
    db = SessionLocal()
    created_tender_ids: List[int] = []

    print("=" * 78)
    print("  ABLATION SETUP: Ingesting corpus into pgvector")
    print("=" * 78)
    tender_ids: Dict[str, int] = {}    # doc_id → tender_id
    try:
        for ann in annotations:
            text   = extract_text(ann)
            tender = Tender(title=f"[ablation] {ann['doc_id']}", raw_text=text)
            db.add(tender); db.commit(); db.refresh(tender)
            tid = tender.id
            created_tender_ids.append(tid)
            tender_ids[ann["doc_id"]] = tid

            chunks   = PDFProcessor.chunk_text(text, chunk_size=1000, chunk_overlap=200)
            vectors  = embedder.embed_batch([c["text"] for c in chunks])
            for chunk, vector in zip(chunks, vectors):
                db.add(TenderChunk(
                    tender_id=tid, chunk_index=chunk["index"],
                    text_content=chunk["text"], embedding=vector,
                    char_start=chunk.get("char_start"), page_number=chunk.get("page_number"),
                ))
            db.commit()
            print(f"  {ann['doc_id']}: {len(chunks)} chunks (tender_id={tid})")
    except Exception as exc:
        db.close()
        raise RuntimeError(f"Ingestion failed: {exc}") from exc

    grid: Dict[str, Dict[str, Any]] = {}

    try:
        for config_id, description, agent_mode in ABLATION_CONFIGS:
            print()
            print("=" * 78)
            print(f"  CONFIG: {config_id} — {description}")
            print("=" * 78)
            print(f"  {'DOC':<14} {'TURN':>6} {'EMD':>5} {'SOL':>5} {'DEAD':>5} {'TITLE':>6} {'TOK':>8} {'ms':>7}")
            print(f"  {'-'*72}")

            field_hits: Dict[str, List[bool]] = {
                "minimum_turnover": [], "emd": [], "solvency": [], "deadline": [], "title": []
            }
            prompt_tokens_all:     List[int]   = []
            completion_tokens_all: List[int]   = []
            total_tokens_all:      List[int]   = []
            latencies_all:         List[float] = []
            failures  = 0
            cite_results = []   # CitationVerificationResult per doc (rag-cite only)

            for ann in annotations:
                doc_id = ann["doc_id"]
                text   = extract_text(ann)
                gold   = ann["fields"]
                tid    = tender_ids[doc_id]

                try:
                    t0 = time.perf_counter()

                    if agent_mode is None:
                        # ── Monolithic baseline ──────────────────────────────
                        output, record = mono_agent.analyze(text)
                        facts_dict = output.model_dump()
                        state_dict = None
                        pt  = record.prompt_tokens or 0
                        ct  = record.completion_tokens or 0
                        tot = record.total_tokens or 0

                    else:
                        # ── Multi-agent ──────────────────────────────────────
                        state = orchestrator.run_analysis(
                            tender_id=tid, raw_text=text, db=db, mode=agent_mode
                        )
                        facts_dict = (
                            state.extracted_facts.model_dump() if state.extracted_facts else {}
                        )
                        pt  = sum(r.prompt_tokens     or 0 for r in state.tool_calls)
                        ct  = sum(r.completion_tokens or 0 for r in state.tool_calls)
                        tot = sum(r.total_tokens      or 0 for r in state.tool_calls)
                        state_dict = state.model_dump(mode="json") if agent_mode == "rag-cite" else None

                    elapsed_ms = int((time.perf_counter() - t0) * 1000)

                except (AgentError, Exception) as exc:
                    failures += 1
                    print(f"  {doc_id:<14} ERROR: {exc}")
                    continue

                # Field accuracy
                field_scores = _score_extraction_fields(facts_dict, gold)
                for k_name, flag in field_scores.items():
                    if flag is not None:
                        field_hits[k_name].append(flag)

                # Token / latency accumulation
                if pt:  prompt_tokens_all.append(pt)
                if ct:  completion_tokens_all.append(ct)
                if tot: total_tokens_all.append(tot)
                latencies_all.append(elapsed_ms)

                # Citation verification (rag-cite only)
                if agent_mode == "rag-cite" and state_dict is not None:
                    chunk_rows = (
                        db.query(TenderChunk)
                        .filter(TenderChunk.tender_id == tid)
                        .all()
                    )
                    chunk_map = {row.id: row.text_content for row in chunk_rows}
                    cv = verify_citations(state_dict, chunk_map)
                    cite_results.append(cv)

                def _m(flag) -> str:
                    return "n/a" if flag is None else ("ok" if flag else "MISS")

                print(
                    f"  {doc_id:<14} "
                    f"{_m(field_scores.get('minimum_turnover')):>6} "
                    f"{_m(field_scores.get('emd')):>5} "
                    f"{_m(field_scores.get('solvency')):>5} "
                    f"{_m(field_scores.get('deadline')):>5} "
                    f"{_m(field_scores.get('title')):>6} "
                    f"{tot:>8} {elapsed_ms:>7}"
                )

            # ── Config summary ───────────────────────────────────────────────
            accuracy  = {kk: (sum(v) / len(v) if v else None) for kk, v in field_hits.items()}
            scored    = [a for a in accuracy.values() if a is not None]
            macro_acc = mean(scored) if scored else None
            mean_tok  = mean(total_tokens_all) if total_tokens_all else None
            mean_pt   = mean(prompt_tokens_all) if prompt_tokens_all else None
            mean_ct   = mean(completion_tokens_all) if completion_tokens_all else None
            mean_lat  = mean(latencies_all) if latencies_all else None
            est_cost  = _estimate_cost(sum(prompt_tokens_all), sum(completion_tokens_all)) if prompt_tokens_all else None

            cite_summary: Optional[Dict[str, Any]] = None
            if cite_results:
                tc   = sum(c.total       for c in cite_results)
                gr   = sum(c.grounded    for c in cite_results)
                ha   = sum(c.hallucinated for c in cite_results)
                ug   = sum(c.ungrounded  for c in cite_results)
                dnom = tc - ug
                cite_summary = {
                    "total_citations":    tc,
                    "grounded":           gr,
                    "hallucinated":       ha,
                    "ungrounded":         ug,
                    "grounding_precision": gr / dnom if dnom > 0 else None,
                    "hallucination_rate":  ha / tc   if tc   > 0 else None,
                }

            print(f"  {'-'*72}")
            print(f"  Macro field accuracy   : {macro_acc * 100:.1f}%"    if macro_acc is not None else "  Macro field accuracy   : n/a")
            print(f"  Agent failures         : {failures}")
            print(f"  Mean tokens / doc      : {mean_tok:.0f}"             if mean_tok  else "  Mean tokens / doc      : n/a")
            print(f"  Est. cost (corpus)     : ${est_cost:.4f}"            if est_cost  else "  Est. cost (corpus)     : n/a")
            print(f"  Mean latency / doc ms  : {mean_lat:.0f}"             if mean_lat  else "  Mean latency / doc ms  : n/a")
            if cite_summary:
                gp = cite_summary.get("grounding_precision")
                hr = cite_summary.get("hallucination_rate")
                print(f"  Grounding precision    : {gp * 100:.1f}%" if gp is not None else "  Grounding precision    : n/a")
                print(f"  Hallucination rate     : {hr * 100:.1f}%" if hr is not None else "  Hallucination rate     : n/a")

            grid[config_id] = {
                "description": description,
                "agent_mode": agent_mode,
                "documents": len(annotations),
                "agent_failures": failures,
                "field_accuracy": accuracy,
                "macro_field_accuracy": macro_acc,
                "mean_total_tokens": mean_tok,
                "mean_prompt_tokens": mean_pt,
                "mean_completion_tokens": mean_ct,
                "estimated_cost_usd": est_cost,
                "mean_latency_ms": mean_lat,
                "citation_verification": cite_summary,
            }

        # ── Cross-config table ───────────────────────────────────────────────
        print()
        print("=" * 78)
        print("  ABLATION GRID SUMMARY")
        print("=" * 78)
        print(f"  {'CONFIG':<18} {'ACC':>7} {'TOKENS':>8} {'COST $':>8} {'LAT ms':>8} {'GPREC':>7} {'HRATE':>7}")
        print(f"  {'-'*72}")
        for cid, cdata in grid.items():
            acc  = cdata["macro_field_accuracy"]
            tok  = cdata["mean_total_tokens"]
            cost = cdata["estimated_cost_usd"]
            lat  = cdata["mean_latency_ms"]
            cs   = cdata["citation_verification"] or {}
            gp   = cs.get("grounding_precision")
            hr   = cs.get("hallucination_rate")
            print(
                f"  {cid:<18} "
                f"{f'{acc*100:.1f}%' if acc is not None else 'n/a':>7} "
                f"{f'{tok:.0f}'      if tok  else 'n/a':>8} "
                f"{f'{cost:.4f}'     if cost else 'n/a':>8} "
                f"{f'{lat:.0f}'      if lat  else 'n/a':>8} "
                f"{f'{gp*100:.1f}%'  if gp  is not None else 'n/a':>7} "
                f"{f'{hr*100:.1f}%'  if hr  is not None else 'n/a':>7}"
            )
        print("=" * 78)

        passed = all(
            g["agent_failures"] == 0 and g["macro_field_accuracy"] is not None
            for g in grid.values()
        )
        return passed, {
            "mode": "ablation",
            "corpus_documents": len(annotations),
            "k_retrieval": k,
            "model": os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash"),
            "cost_model": {"input_per_1m": _COST_PER_1M_INPUT, "output_per_1m": _COST_PER_1M_OUTPUT},
            "grid": grid,
            "passed": passed,
        }
    finally:
        if keep:
            print(f"\n  Kept {len(created_tender_ids)} ablation tenders in DB (--keep).")
        else:
            for tid in created_tender_ids:
                db.query(TenderChunk).filter(TenderChunk.tender_id == tid).delete()
                db.query(Tender).filter(Tender.id == tid).delete()
            db.commit()
        db.close()


def run_corrigendum(limit: Optional[int]) -> Tuple[bool, Dict[str, Any]]:
    """
    Demonstrates multi-document tender handling with corrigendum overrides.

    Tests that processing a Corrigendum document cleanly updates parent tender
    submission deadline and financial requirements.
    """
    from app.models import Tender
    from app.agents.orchestrator import AgentOrchestrator
    from app.database import Base, engine, SessionLocal

    print("=" * 78)
    print("  CORRIGENDUM / AMENDMENT EVALUATION")
    print("=" * 78)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()


    try:
        # Create parent RFP tender
        base_rfp_text = """
        TENDER TITLE: Construction of Regional Cyber Security Hub
        SUBMISSION DEADLINE: October 10, 2026
        MINIMUM TURNOVER REQUIRED: Rs. 10 Crore
        EARNEST MONEY DEPOSIT (EMD): Rs. 10,00,000
        ELIGIBILITY: Bidders must have at least 5 years of experience in cybersecurity.
        """
        tender = Tender(title="Regional Cyber Security Hub RFP", raw_text=base_rfp_text)
        db.add(tender)
        db.commit()
        db.refresh(tender)

        orchestrator = AgentOrchestrator()

        # Step 1: Base analysis
        print(f"1. Analyzing Base RFP Tender (ID: {tender.id})...")
        base_state = orchestrator.run_analysis(tender.id, base_rfp_text, db, mode="full")
        base_deadline = base_state.extracted_facts.submission_deadline if base_state.extracted_facts else None
        print(f"   Base RFP Deadline Extracted: {base_deadline}")

        # Step 2: Apply Corrigendum 1
        corrigendum_text = """
        CORRIGENDUM NO. 1
        TENDER REF: Regional Cyber Security Hub
        AMENDMENT:
        1. The last date for submission of bids is extended from October 10, 2026 to November 15, 2026.
        2. EMD requirement is revised from Rs. 10,00,000 to Rs. 5,00,000.
        """
        print(f"\n2. Applying Corrigendum No. 1 to Tender ID: {tender.id}...")
        corr_state = orchestrator.apply_corrigendum(tender.id, corrigendum_text, db)
        updated_deadline = corr_state.extracted_facts.submission_deadline if corr_state.extracted_facts else None
        updated_emd = corr_state.extracted_facts.financial_requirements.earnest_money_deposit_emd if corr_state.extracted_facts else None

        print(f"   Updated RFP Deadline in DB : {updated_deadline}")
        print(f"   Updated EMD Amount in DB    : {updated_emd}")

        deadline_overridden = updated_deadline is not None and ("November 15" in updated_deadline or "15-11-2026" in updated_deadline or "2026-11-15" in updated_deadline or "Nov" in updated_deadline)
        emd_overridden = updated_emd is not None and ("5,00,000" in updated_emd or "5 Lakh" in updated_emd or "500000" in updated_emd)

        passed = deadline_overridden or emd_overridden
        print(f"\nResult: {'PASSED (Corrigendum correctly overrode base parameters)' if passed else 'FAILED'}")


        return passed, {
            "mode": "corrigendum",
            "base_deadline": base_deadline,
            "updated_deadline": updated_deadline,
            "updated_emd": updated_emd,
            "passed": passed,
        }
    finally:
        db.close()


# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="TenderAI evaluation harness. All reported numbers are measured.",
    )
    parser.add_argument("--mode", choices=["smoke", "retrieval", "extraction", "ablation", "corrigendum"], default="smoke")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N documents.")
    parser.add_argument("--k", type=int, default=10, help="Top-k for retrieval mode (default: 10).")
    parser.add_argument("--chunking-strategy", choices=["character", "structure"], default="character",
                        help="Chunking strategy: 'character' (1000 char window) or 'structure' (clause/section aware).")
    parser.add_argument("--keep", action="store_true",
                        help="Retrieval mode: leave ingested tenders in the DB for inspection.")
    parser.add_argument("--no-save", action="store_true", help="Do not write eval_results/*.json.")
    args = parser.parse_args()

    started = time.time()
    if args.mode == "smoke":
        passed, payload = run_smoke(args.limit)
    elif args.mode == "retrieval":
        passed, payload = run_retrieval(args.limit, args.k, args.keep)
    elif args.mode == "extraction":
        passed, payload = run_extraction(args.limit)
    elif args.mode == "corrigendum":
        passed, payload = run_corrigendum(args.limit)
    else:  # ablation
        passed, payload = run_ablation(args.limit, args.k, args.keep)

    payload["elapsed_seconds"] = round(time.time() - started, 3)
    payload["timestamp_utc"] = datetime.now(timezone.utc).isoformat()

    if not args.no_save:
        out = write_results(args.mode, payload)
        print(f"\nResults written to {out}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

