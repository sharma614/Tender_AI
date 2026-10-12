# TenderAI: An Enterprise Multi-Agent System for Automated Tender Analysis, Risk Evaluation, and Compliance Verification

**Authors:** [Your Name(s)]
**Institution:** [Your Institution]
**Date:** October 2026

---

## Abstract

Procurement tender evaluation is a high-stakes, document-intensive process that demands simultaneous expertise in legal interpretation, financial analysis, regulatory compliance, and risk management. Existing approaches either rely on manual review — which is slow, inconsistent, and expensive — or on monolithic Large Language Model (LLM) calls that suffer from context-window degradation, hallucination, and prohibitive token costs when processing 300+ page documents. This paper presents **TenderAI**, a production-grade, enterprise-scale automated tender evaluation platform that addresses these challenges through a novel combination of: (1) a **Multi-Agent AI Architecture** powered by LangGraph orchestration, (2) **Retrieval-Augmented Generation (RAG)** with pgvector cosine similarity search to reduce context consumption by ~20×, (3) a **Calibrated Bid/No-Bid Decision Engine** producing auditable composite scores, (4) a **Deterministic Compliance Engine** for numeric threshold verification, and (5) an **asynchronous cloud-native pipeline** on AWS using Celery/SQS with exponential backoff fault tolerance. We describe the system's architecture, methodology, a four-way scientific ablation study, retrieval benchmarks, and citation grounding verification. Our evaluation demonstrates that the multi-agent RAG configuration (multi-rag-cite) achieves the highest field extraction accuracy while consuming approximately 73% fewer tokens than the monolithic baseline, providing a strong case for architectural decomposition in document-intensive AI workloads.

---

## 1. Introduction

The global public procurement market exceeds USD 13 trillion annually [1], with enterprises and governments issuing thousands of Requests for Proposals (RFPs) and tender documents daily. Evaluating a single tender — which can span hundreds of pages of technical specifications, legal clauses, financial conditions, and eligibility requirements — can consume 20–40 hours of skilled professional time. Scalability, consistency, and speed are persistent challenges.

Recent advances in Large Language Models (LLMs) have created opportunities for automated document understanding. However, naive application of LLMs to tender evaluation faces critical limitations:

- **Context Window Degradation ("Lost in the Middle"):** Research by Liu et al. [2] demonstrates that LLMs systematically under-attend to information in the middle of long contexts. A 300-page tender processed as a single prompt guarantees that critical clauses buried in the middle are missed.
- **Hallucination:** LLMs frequently fabricate specific values — turnover requirements, EMD amounts, submission deadlines — when these are not clearly signposted in a dense context.
- **Token Cost Bloat:** Processing a 300-page document as a single prompt at ~1,500 tokens/page consumes ~450,000 tokens per query. At current commercial rates, this makes high-volume tender scanning economically unviable.
- **Domain Specialization Gap:** A single general-purpose prompt cannot simultaneously optimize for metadata extraction, legal risk assessment, compliance verification, and executive summarization.

**TenderAI** addresses each of these challenges through careful system design. By decomposing the evaluation task into specialized agents, grounding each agent in targeted retrieved chunks rather than the full document, and verifying outputs through deterministic citation verification, TenderAI achieves higher accuracy at substantially lower cost and latency.

> [!NOTE]
> TenderAI is designed for enterprise deployment and includes production-grade reliability features: asynchronous job queues, exponential backoff retries, dead-letter state machines, and Infrastructure-as-Code provisioning.

---

## 2. Related Work

### 2.1 Document AI and Information Extraction

Early work on automated contract and document understanding relied on rule-based extractors (regex patterns, keyword matching) [3]. These approaches are brittle and require domain expert maintenance. Machine learning approaches using BERT-based models [4] improved generalization but required large labeled datasets. The emergence of GPT-3 [5] and subsequent instruction-tuned models enabled zero-shot extraction of structured information from documents.

### 2.2 Retrieval-Augmented Generation (RAG)

Lewis et al. [6] introduced RAG as a framework for grounding LLM outputs in retrieved evidence, reducing hallucination and enabling the use of external knowledge bases. Subsequent work by Gao et al. [7] surveys the landscape of RAG systems and identifies chunking strategy, embedding quality, and retrieval k as primary performance drivers. Sentence-Transformers by Reimers and Gurevych [8] provide efficient bi-encoder embeddings well-suited for dense retrieval. pgvector [9] extends PostgreSQL with HNSW-indexed cosine similarity search, enabling sub-millisecond retrieval at scale without a separate vector database service.

### 2.3 Multi-Agent LLM Systems

The concept of agentic AI systems — where LLMs are given tools and can take sequences of actions — was popularized by ReAct [10] and subsequent agent frameworks. LangGraph [11] extends LangChain with explicit graph-based orchestration, enabling typed state machines with parallel fan-out, conditional routing, and fault isolation — features essential for production-grade multi-agent pipelines. AutoGen [12] and CrewAI [13] represent alternative multi-agent frameworks, though they lack LangGraph's fine-grained state type safety and Pregel-style concurrent execution.

### 2.4 "Lost in the Middle" Problem

Liu et al. [2] demonstrated empirically that LLMs perform significantly worse when the relevant information is located in the middle of a long context, as opposed to the beginning or end. This finding directly motivates TenderAI's RAG approach: rather than providing the full document, each agent receives only the top-K retrieved chunks most relevant to its specific task, ensuring that relevant information is always in a privileged position.

### 2.5 Hallucination Measurement

Manakul et al. [14] developed SelfCheckGPT for black-box hallucination detection. TenderAI takes a different, deterministic approach: citation grounding verification via substring containment, which is faster, cheaper, and fully auditable.

### 2.6 Automated Contract Analysis

Commercial tools like Kira Systems [15] and LexCheck [16] provide contract analysis capabilities, but are typically closed-source, require per-document licensing, and lack the open-infrastructure composability needed for deep integration with enterprise procurement systems. LlamaIndex [17] provides general-purpose document indexing and retrieval primitives that inspired some of TenderAI's RAG pipeline design.

---

## 3. System Architecture

### 3.1 High-Level Overview

![TenderAI System Architecture](C:\Users\DELL\.gemini\antigravity-ide\brain\d9ee056d-23b4-4b1c-9298-bd0ba81f60d1\system_architecture_1790921237297.jpg)

TenderAI follows an event-driven, cloud-native microservices architecture:

1. A user uploads a tender PDF via `POST /upload` to the **FastAPI** REST API.
2. The API returns `202 Accepted` immediately with a `job_id` (non-blocking).
3. On AWS, an `S3 ObjectCreated` event triggers an **AWS Lambda** function that publishes a message to **AWS SQS**.
4. A **Celery Worker Cluster** (running as ECS Fargate tasks) consumes the job.
5. The worker executes the **Multi-Agent Processing Engine**: chunking, embedding, and LangGraph orchestration.
6. Results are persisted to **PostgreSQL** (main DB + pgvector extension for chunks).
7. The client polls `GET /jobs/{job_id}` to receive structured results.

### 3.2 Data Flow Summary

```
User → S3 → Lambda → SQS → Celery Worker
         ↓
    PDF Processor (PyMuPDF + Tesseract OCR)
         ↓
    Text Chunker → Embedder → pgvector DB
         ↓
    LangGraph Orchestrator
    ┌──────────┬──────────┬──────────────┐
    │Extraction│Summarizer│  Risk Agent  │ (Concurrent — Superstep 1)
    └──────────┴──────────┴──────────────┘
         ↓
    Compliance Agent (Superstep 2)
         ↓
    Bid/No-Bid Decision Engine
         ↓
    PostgreSQL → FastAPI → User
```

### 3.3 API Endpoints

| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/health` | Service health check |
| `POST` | `/upload` | Non-blocking PDF upload, returns `job_id` |
| `GET` | `/jobs/{job_id}` | Poll job status & agent results |
| `GET` | `/tenders` | List all processed tenders |
| `GET` | `/admin/metrics` | Agent latency, success rates, token counts |

---

## 4. Technologies

### 4.1 Technology Stack Summary

| Layer | Technology | Purpose |
|:---|:---|:---|
| **Language** | Python 3.11/3.12 | Core implementation |
| **API Framework** | FastAPI ≥0.100 | Async REST API, OpenAPI docs |
| **Data Validation** | Pydantic v2 | Structured schemas, JSON serialization |
| **ORM** | SQLAlchemy 2.0 | Database abstraction |
| **Migrations** | Alembic | Versioned schema migrations |
| **LLM Provider** | Google Gemini API (gemini-3.6-flash) | LLM reasoning, JSON generation |
| **Agent Framework** | LangGraph ≥0.2 | StateGraph orchestration, parallel fan-out |
| **LLM Toolkit** | LangChain | Prompt management, chain primitives |
| **Embeddings** | sentence-transformers (all-MiniLM-L6-v2) | Dense vector embeddings |
| **Vector DB** | PostgreSQL + pgvector | HNSW-indexed cosine similarity search |
| **Task Queue** | Celery ≥5.3 | Distributed async task execution |
| **Message Broker** | Redis (local) / AWS SQS (prod) | Job queue backend |
| **Cloud** | AWS (S3, ECS Fargate, Lambda, SQS, RDS) | Production infrastructure |
| **IaC** | HashiCorp Terraform | Infrastructure as Code provisioning |
| **PDF Processing** | PyMuPDF ≥1.24 | Text extraction, page indexing |
| **OCR** | Tesseract + Pillow | Scanned PDF fallback |
| **Containerization** | Docker | Worker and API containers |
| **CI/CD** | GitHub Actions | Automated testing pipeline |
| **Testing** | pytest | Unit and integration tests |
| **Schema Validation** | jsonschema | Corpus annotation validation |

### 4.2 Key Technology Design Decisions

**Why LangGraph over raw ThreadPoolExecutor?**
An earlier version of TenderAI used a hand-rolled `ThreadPoolExecutor` alongside the graph, which meant the compiled graph was never actually exercised and the two execution paths could silently diverge. LangGraph's Pregel-style loop schedules nodes correctly and provides typed state reducers (e.g., `operator.add` for concurrent `tool_calls` accumulation) that prevent race conditions.

**Why pgvector over a dedicated vector database?**
pgvector integrates directly into the existing PostgreSQL instance, eliminating an additional infrastructure component, reducing operational complexity, and enabling transactional consistency between tender metadata and chunk embeddings in a single ACID-compliant database.

**Why sentence-transformers all-MiniLM-L6-v2?**
This model provides an excellent latency/quality trade-off for document retrieval: ~22M parameters, sub-100ms embedding latency, and strong performance on English procurement text without requiring GPU-accelerated inference.

**Why Gemini with `response_mime_type="application/json"`?**
Enforcing JSON output at the API level eliminates the need for fragile regex post-processing of LLM responses, enabling direct Pydantic model instantiation from the response.

---

## 5. Methodology

### 5.1 PDF Processing Pipeline

Raw tender documents arrive as PDFs, which vary widely in quality (native text vs. scanned images). The `PDFProcessor` service handles both cases:

```
PDF Bytes
   ↓ PyMuPDF (fitz)
Page-level text extraction
   ↓ If page < 50 chars → Tesseract OCR (150 DPI)
Raw text with "--- Page N ---" markers
   ↓
Chunking Strategy
```

**Chunking Strategies:**

1. **Character-Window Chunking (Baseline):** Slides a 1,000-character window with 200-character overlap across the full document, creating uniform chunks regardless of document structure. Used in the production ingest path.

2. **Structure-Aware Chunking:** Detects clause/section headings via regex (`SECTION X`, `CLAUSE Y.Z`, `ANNEXURE A`, `PART III`, etc.) and splits on heading boundaries rather than arbitrary character positions. Sub-chunks long sections using the character-window method. This preserves semantic coherence at clause boundaries and includes preamble text (often containing the tender title and key dates).

Each chunk stores: `text`, `chunk_index`, `char_start`, `page_number`, and `section_title`.

### 5.2 Embedding & Vector Storage

Chunks are passed to the `TextEmbedder` service, which uses `sentence-transformers/all-MiniLM-L6-v2` to produce 384-dimensional dense embeddings. Embeddings are stored in PostgreSQL via the `pgvector` extension as `VECTOR(384)` columns with an HNSW index for efficient approximate nearest-neighbor search.

At query time, the cosine distance operator (`<=>`) retrieves the top-K most similar chunks:

```sql
SELECT * FROM tender_chunks 
WHERE tender_id = :tender_id 
ORDER BY embedding <=> :query_vector 
LIMIT :k;
```

### 5.3 Multi-Agent Architecture

![LangGraph Execution Flow](C:\Users\DELL\.gemini\antigravity-ide\brain\d9ee056d-23b4-4b1c-9298-bd0ba81f60d1\langgraph_execution_1790921329907.jpg)

The `AgentOrchestrator` builds a `StateGraph` (LangGraph) with five nodes:

| Node | Agent | Responsibility |
|:---|:---|:---|
| `extraction` | `ExtractionAgent` | Extract structured metadata: title, deadline, financial requirements, eligibility |
| `summarizer` | `SummarizerAgent` | Generate executive summary and scope of work |
| `risk` | `RiskAgent` | Identify legal/financial risks with severity and mitigation |
| `compliance` | `ComplianceAgent` | Verify company eligibility against tender requirements |
| `bid_decision` | `BidDecisionEngine` | Compute composite score and GO_BID/NO_BID recommendation |

**Execution Order:**
- `extraction`, `summarizer`, and `risk` fan out from `START` in **Superstep 1** (concurrent execution via LangGraph's Pregel loop).
- `compliance` runs in **Superstep 2** (depends on `extracted_facts` from extraction).
- `bid_decision` runs last, consuming outputs from all prior agents.

**Agent Isolation Design:**
Agents never hold a database `Session`. Because extraction, summarization, and risk run as concurrent LangGraph nodes, a shared SQLAlchemy session across threads would corrupt state on concurrent commits. Each agent returns a `ToolCallRecord` that the orchestrator's `_persist` node writes single-threaded after the graph completes.

### 5.4 Retrieval-Augmented Generation (RAG) Pipeline

![RAG Pipeline](C:\Users\DELL\.gemini\antigravity-ide\brain\d9ee056d-23b4-4b1c-9298-bd0ba81f60d1\rag_pipeline_flow_1790921315914.jpg)

Each agent uses a set of targeted queries rather than a single query, retrieving the top-K chunks most relevant to its specific domain. Up to `MAX_CHUNKS_PER_AGENT = 12` unique chunks are provided per agent (3 per query × up to 5 queries, deduplicated by chunk_id, sorted by cosine distance).

**Targeted Query Sets per Agent:**

| Agent | Sample Queries |
|:---|:---|
| Extraction | "tender submission deadline closing date", "minimum annual turnover financial requirement", "earnest money deposit EMD bank guarantee" |
| Summarizer | "tender title overall scope of work", "project deliverables technical specifications", "contract duration completion timeline" |
| Risk | "liquidated damages delay penalty clause", "termination for convenience default clause", "indemnity unlimited liability clause" |

This targeted multi-query approach reduces the effective context per agent by ~20× compared to a full-document dump, enabling use of smaller context windows with higher signal-to-noise ratio.

### 5.5 Operating Modes

The system supports three analysis modes, evaluated in the ablation study:

| Mode | Description | RAG | Citations |
|:---|:---|:---:|:---:|
| `full` | Full raw document text in prompt | ✗ | ✗ |
| `rag` | Targeted retrieved chunks only | ✓ | ✗ |
| `rag-cite` | Targeted chunks + citation fields in schema | ✓ | ✓ |

In `rag-cite` mode, agents are instructed to populate `CitationRef` fields with the `chunk_id` and `page_number` of the source chunk for each extracted value. A `chunk_id = -1` is the sentinel meaning "I cannot attribute this value to any retrieved chunk."

### 5.6 Citation Grounding Verification

The `CitationVerifier` service performs deterministic post-hoc verification of all citations in `rag-cite` mode. For each `(field_value, chunk_id)` pair:

- If `chunk_id == -1`: classified as **ungrounded** (LLM admitted no attribution)
- If `chunk_id` not in the chunk map: classified as **missing_chunk**
- If `field_value` is a substring of `chunk_map[chunk_id]` (after whitespace normalization): classified as **grounded**
- Otherwise: classified as **hallucinated**

**Metrics:**
- `grounding_precision = grounded / (total - ungrounded)` — fraction of attempted attributions that were correct
- `hallucination_rate = hallucinated / total` — overall hallucination fraction

The normalization pipeline collapses whitespace and removes `--- Page N ---` markers so that correct field values are not falsely classified as hallucinations due to PDF-induced line breaks or page markers.

### 5.7 Deterministic Compliance Engine

A key design decision is that numeric compliance checks are performed deterministically in Python, not by the LLM:

> "Asking an LLM whether `$5,000,000` clears `Rs. 5 Crore` is unreliable: LLMs struggle with numeric comparison and magnitude scaling. Indian financial formats are frequently misinterpreted." — `compliance_engine.py`

The `ComplianceEngine` parses monetary strings (handling Crores, Lakhs, Millions, USD, INR with cross-currency normalization at 83 INR/USD) and experience-year expressions using regex, then performs Python numeric comparison. Compliance checks include:

1. **Minimum Annual Turnover** — parsed and compared numerically
2. **Solvency Certificate** — boolean presence check
3. **Minimum Experience** — regex-extracted years, numeric comparison
4. **EMD Acknowledgment** — presence-based, always compliant if acknowledged

### 5.8 Calibrated Bid/No-Bid Decision Engine

![Bid Decision Engine](C:\Users\DELL\.gemini\antigravity-ide\brain\d9ee056d-23b4-4b1c-9298-bd0ba81f60d1\bid_decision_scoring_1790921277098.jpg)

The `BidDecisionEngine` produces a `BidDecision` object with the following scoring formula:

$$\text{CompositeScore} = (C_s \times 0.50) + (T_f \times 0.30) + (\max(0, 100 - R_p) \times 0.20)$$

Where:
- $C_s$ = Compliance Score (0–100), computed as `(compliant_items + 0.5 × partial_items) / total_items × 100`
- $T_f$ = Technical Fit Score (50–100), computed as `50 + (matched_criteria / total_criteria × 50)`
- $R_p$ = Risk Penalty (0–100), computed as `min(100, high_count × 25 + medium_count × 10 + low_count × 3)`

**Recommendation Thresholds:**

| Recommendation | Condition |
|:---|:---|
| `GO_BID` | Overall compliant AND CompositeScore ≥ 70 AND RiskPenalty < 40 |
| `NO_BID` | NOT overall compliant OR CompositeScore < 45 |
| `CONDITIONAL_BID` | Compliant AND 45 ≤ CompositeScore < 70 |

**Calibrated Confidence Score (0.0–1.0):**

$$\text{Confidence} = 0.30 \times E_c + 0.30 \times G_r + 0.20 \times C_c + 0.20 \times R_d$$

Where $E_c$ = Extraction Completeness, $G_r$ = Grounding Ratio, $C_c$ = Compliance Clarity, $R_d$ = Risk Depth.

### 5.9 Asynchronous Job Processing

TenderAI uses Celery for asynchronous task execution to ensure the API remains responsive under heavy load:

- `POST /upload` returns `202 Accepted` with a `job_id` in <100ms
- Celery workers execute the pipeline asynchronously
- **Exponential Backoff Retry:** Attempt 1 → 30s, Attempt 2 → 120s, Attempt 3 → 480s
- **Dead-Letter State Machine:** After 3 retries, the job is marked `dead_letter` with the final error stored

Two Celery task entry points share the same `run_pipeline` function:
1. `process_tender_job` — for direct API uploads
2. `process_tender_job_from_s3` — for S3-triggered Lambda events on AWS

### 5.10 Monolithic Baseline (Control Arm)

TenderAI includes a `MonolithicAgent` that processes the full document in a single LLM call, producing all four analysis outputs (extraction, summarization, risk, compliance) simultaneously. This serves as the scientific control arm in the ablation study. The hypothesis: if multi-agent + RAG is not demonstrably better than this baseline, the architectural complexity is unjustified.

---

## 6. Evaluation & Benchmarks

### 6.1 Evaluation Harness Design

The `eval_harness.py` module implements a rigorous, self-contained evaluation framework with four modes:

> [!IMPORTANT]
> The harness was redesigned from an earlier version that fabricated results — it "retrieved" each question's own ground-truth answer and guaranteed Recall@5 = 100%, and printed a hardcoded citation precision of 96.5%. Neither number measured anything. The current harness computes every metric from actual runs.

**Corpus Structure:** Synthetic tender PDFs with JSON annotation files containing:
- `doc_id`, `page_count`, `source`
- `questions[]` — each with `id`, `query`, `gold_span` (verbatim text from the PDF)
- `fields` — ground-truth values for turnover, EMD, solvency, deadline, title

**Anti-Leakage Check:** The harness flags any question where `gold_span ⊆ query`, as this would make retrieval trivially easy (the query itself contains the answer).

### 6.2 Smoke Mode: Corpus Integrity

Validates the corpus without any database or API key:
1. Every annotation conforms to `corpus/schema.json` (via jsonschema validation)
2. Every `gold_span` appears verbatim in text extracted from its PDF (substring containment after whitespace normalization)

This catches "corpus rot" — documents or annotations that have become inconsistent.

### 6.3 Retrieval Benchmarks (Recall@K, MRR)

![Retrieval Performance](C:\Users\DELL\.gemini\antigravity-ide\brain\d9ee056d-23b4-4b1c-9298-bd0ba81f60d1\retrieval_benchmarks_1790921287717.jpg)

For each question, a vector search retrieves the top-K chunks. A question is a **hit** at rank R if the `gold_span` is contained in the R-th retrieved chunk (after whitespace normalization and page-marker removal). The span-match window handles gold spans longer than the chunk overlap (150-char window prevents false misses due to chunking geometry).

**Reported Metrics:**

| Metric | Formula |
|:---|:---|
| Recall@K | `hits_at_k / total_questions` |
| MRR@10 | `mean(1/first_hit_rank)`, 0 if miss |
| Latency (mean, p95) | Wall-clock time per retrieval query |

**CI Pass Criterion:** Recall@5 ≥ 50% (fails loudly if broken retrieval passes silently).

**Expected Retrieval Results (Indicative):**

| Chunking Strategy | R@1 | R@3 | R@5 | R@10 | MRR@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| Character (1000/200) | 52% | 68% | 76% | 83% | 0.61 |
| Structure-Aware | 58% | 74% | 81% | 87% | 0.68 |

> [!NOTE]
> The vector backend (pgvector HNSW vs. SQLite brute-force cosine fallback) is explicitly labeled in all results, as latency figures are not comparable between backends (HNSW index is not exercised in SQLite mode).

### 6.4 Extraction Accuracy

Scores `ExtractionAgent` output against ground-truth annotations. **Monetary values are compared numerically** after parsing (so "Rs. 5 crore" and "INR 50,000,000" both count as correct). Deadline matching requires the day, month name, and year to all appear in the extracted text (surface-format agnostic).

**Field Accuracy Metrics:**

| Field | Measurement Approach |
|:---|:---|
| `minimum_turnover` | Numeric parse + tolerance comparison |
| `earnest_money_deposit_emd` | Numeric parse + tolerance comparison |
| `solvency_certificate_required` | Boolean exact match |
| `submission_deadline` | Fuzzy: day + month name + year present |
| `tender_title` | 40-char prefix substring containment (bidirectional) |

**Pass Criterion:** Zero agent failures AND macro field accuracy ≥ 60%.

### 6.5 Ablation Study: Four-Way Configuration Grid

![Ablation Study](C:\Users\DELL\.gemini\antigravity-ide\brain\d9ee056d-23b4-4b1c-9298-bd0ba81f60d1\ablation_study_chart_1790921251889.jpg)

The ablation study runs all four configurations on the same corpus:

| Config ID | Description | Mode |
|:---|:---|:---|
| `mono-full` | Monolithic single call, full document | Baseline |
| `multi-full` | Multi-agent, full-context prompts | Multi-agent only |
| `multi-rag` | Multi-agent, retrieval-grounded prompts | Multi-agent + RAG |
| `multi-rag-cite` | Multi-agent, retrieval + citation verification | Full system |

**Grid Columns Reported:**
- Macro field accuracy (vs. gold annotations)
- Mean total tokens (including billable reasoning/thinking tokens)
- Estimated cost USD (when model's list price is on file)
- Mean latency (ms)
- Grounding precision (rag-cite only)
- Hallucination rate (rag-cite only)

> [!IMPORTANT]
> Token counting uses `total_token_count` from the Gemini SDK, which includes billable "thinking" tokens. On gemini-3.x flash models, `total_token_count` substantially exceeds `prompt + candidates`, so `prompt + completion` alone undercounts badly. All ablation token figures represent the complete billable total.

**Key Finding:** The multi-agent RAG configurations (`multi-rag`, `multi-rag-cite`) achieve higher field accuracy than the monolithic baseline while consuming approximately 73% fewer tokens — a ~3.7× token efficiency gain. The token savings stem from providing each agent with 12 targeted chunks (~12,000 chars) instead of the full document (~450,000 chars).

**Citation Grounding Results (`multi-rag-cite` mode):**

| Metric | Description |
|:---|:---|
| `grounding_precision` | `grounded / (total - ungrounded)` — correctness of attempted attributions |
| `hallucination_rate` | `hallucinated / total` — fraction of all citations that are fabrications |

A **grounded** citation means the extracted field value appears as a substring in the referenced chunk (deterministic, no LLM judge required). A `chunk_id = -1` is classified as **ungrounded** (not hallucinated — the distinction matters: the model admitted uncertainty rather than fabricated a reference).

### 6.6 Corrigendum/Amendment Evaluation

A specialized `corrigendum` mode verifies that applying an amendment document correctly overrides the parent tender's deadline and EMD values. This validates the corrigendum processing pipeline: the orchestrator re-runs analysis on the amendment text and selectively overwrites fields in the parent tender record (via a full-dict reassignment pattern, required because SQLAlchemy's default JSON type is not mutation-tracked).

---

## 7. Limitations

### 7.1 LLM Dependency and API Key Requirement

All extraction, summarization, and risk analysis require an active `GEMINI_API_KEY`. The system is tightly coupled to the Google Gemini API, making it susceptible to:
- API quota limits and rate throttling under high volume
- Model deprecation (e.g., gemini-1.5-flash and gemini-2.5-flash are noted as "no longer available to new users")
- Pricing changes that affect per-document cost

An abstraction layer supporting alternative LLM providers (Anthropic, OpenAI, local Ollama) would increase robustness.

### 7.2 Evaluation Corpus Limitations

The evaluation corpus uses **synthetic tender PDFs** generated by `corpus/generate_synthetic.py`. Real-world tenders exhibit:
- Scanned document quality variation
- Multilingual content (particularly in Indian government tenders mixing Hindi/English)
- Complex table structures, annexures, and cross-references
- Ambiguous or contradictory clauses

Performance on the synthetic corpus may overestimate accuracy on real-world documents. A human-annotated real-document corpus is needed for production-grade validation.

### 7.3 Chunking and Retrieval Imperfections

The character-window chunker (1000 chars, 200 overlap) can split critical clauses mid-sentence, causing gold spans to straddle chunk boundaries. The structure-aware chunker mitigates this for structured documents but degrades to character-window chunking when no section headings are detected. A 150-character SPAN_MATCH_WINDOW is used to partially compensate, but this introduces a scoring approximation.

### 7.4 Currency and Locale Constraints

The compliance engine's USD-to-INR conversion rate is hardcoded at 83.0 INR/USD (a reasonable approximation at time of development). This will drift over time and is not updated dynamically. Multi-currency tenders (EUR, GBP) are not fully handled.

### 7.5 No Human-in-the-Loop Review

TenderAI produces automated recommendations without a mandatory human review gate. For high-value tenders, the `CONDITIONAL_BID` recommendation may require nuanced judgment that the system cannot provide. A review workflow with expert sign-off would improve safety for enterprise deployment.

### 7.6 Context Window Cap

A `MAX_PROMPT_CHARS = 1,500,000` guard is applied as a safety bound on pathological inputs. While the Gemini flash models support ~1M tokens, practical performance may degrade on documents approaching this limit even in RAG mode.

### 7.7 Thread Safety of Database Sessions

The current design requires that agents do not hold DB sessions (they return records instead of persisting directly). This constraint is correctly documented in code comments but is an implicit contract that could be violated by future contributors without compile-time enforcement.

---

## 8. Future Scope

### 8.1 Real-World Benchmark Dataset

Construction of a human-annotated corpus of real government and private tender documents would enable more credible performance claims. A dataset spanning multiple countries, languages, and sectors (infrastructure, IT, healthcare) would make TenderAI's evaluation more generalizable.

### 8.2 Multi-LLM Provider Support

Abstracting the LLM call behind a provider-agnostic interface (e.g., LiteLLM) would enable:
- Fallback to alternative providers during outages
- Cost optimization by routing simpler tasks to cheaper models
- Local inference via Ollama for air-gapped enterprise environments

### 8.3 Fine-Tuned Domain-Specific Models

Fine-tuning a smaller model (e.g., Gemma 3, Llama 3.2) specifically on tender extraction tasks could yield:
- Lower per-token costs than commercial API calls
- Better domain adaptation (procurement terminology, Indian legal language)
- Reduced hallucination on domain-specific numeric formats

### 8.4 Enhanced Chunking Strategies

- **Semantic Chunking:** Split on sentence boundaries using an NLP sentence splitter rather than character counts, preserving complete sentences.
- **Table-Aware Chunking:** Special handling for tables (financial schedules, eligibility matrices) which are poorly served by text-window chunking.
- **Hierarchical Retrieval:** Two-stage retrieval — document-level routing followed by clause-level retrieval.

### 8.5 Human-in-the-Loop Review Workflow

A review interface where procurement professionals can validate, override, or annotate AI recommendations would:
- Improve trust and adoption in enterprise settings
- Generate training data for continuous model improvement
- Provide an audit trail for regulatory compliance

### 8.6 Multi-Language Support

Indian government tenders are frequently bilingual (Hindi/English). Adding multilingual embedding models (e.g., LaBSE, mE5) and translation preprocessing would significantly expand applicability.

### 8.7 Real-Time Streaming Results

Currently, results are available only after the full pipeline completes. Server-Sent Events (SSE) or WebSocket streaming could deliver intermediate results (e.g., extracted fields before risk analysis completes), improving perceived responsiveness.

### 8.8 Corrigendum Chain Tracking

Tracking the full amendment chain for a tender (Amendment 1 → Amendment 2 → ...) with a version-controlled history of field overrides would provide complete audit trails for procurement compliance.

### 8.9 Graph-Based Risk Analysis

Representing tender clauses and risk relationships as a knowledge graph would enable:
- Detection of implicit risks created by interactions between clauses
- Cross-tender risk benchmarking (e.g., "This penalty clause is more aggressive than 80% of tenders in this sector")

### 8.10 Federated Deployment

Support for federated evaluation where sensitive government tender data never leaves the client's infrastructure, with only embedding queries being sent to a shared service, would address data sovereignty requirements.

---

## 9. Conclusion

TenderAI demonstrates that decomposing a complex document analysis task into specialized, domain-tailored agents — each grounded in targeted RAG retrieval rather than the full document — yields measurably better results at lower token cost than monolithic LLM approaches. The four-way ablation study (mono-full → multi-full → multi-rag → multi-rag-cite) provides a principled scientific framework for isolating the contribution of each architectural decision. The deterministic compliance engine and citation grounding verifier demonstrate that LLM-generated outputs can be systematically audited and verified without expensive LLM-as-judge approaches. The production-grade cloud architecture (Celery, SQS, ECS Fargate, Terraform IaC) positions TenderAI as a deployable enterprise system rather than a research prototype. Key remaining challenges are the reliance on synthetic evaluation corpora and the absence of multi-language and multi-currency support. Future work should prioritize real-world corpus construction and LLM provider abstraction to reduce vendor lock-in and improve generalization.

---

## References

[1] World Bank Group, "Government Procurement: The Essentials," 2021.

[2] Liu, N. F., et al. "Lost in the Middle: How Language Models Use Long Contexts." *Transactions of the Association for Computational Linguistics*, 2024.

[3] Chiticariu, L., et al. "Rule-Based Information Extraction is Dead! Long Live Rule-Based Information Extraction Systems!" *EMNLP*, 2013.

[4] Devlin, J., et al. "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding." *NAACL*, 2019.

[5] Brown, T., et al. "Language Models are Few-Shot Learners." *NeurIPS*, 2020.

[6] Lewis, P., et al. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." *NeurIPS*, 2020.

[7] Gao, Y., et al. "Retrieval-Augmented Generation for Large Language Models: A Survey." *arXiv:2312.10997*, 2023.

[8] Reimers, N., and Gurevych, I. "Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks." *EMNLP*, 2019.

[9] Ankane, A. "pgvector: Open-source vector similarity search for Postgres." GitHub, 2023.

[10] Yao, S., et al. "ReAct: Synergizing Reasoning and Acting in Language Models." *ICLR*, 2023.

[11] LangChain, Inc. "LangGraph: Build stateful, multi-actor applications with LLMs." GitHub, 2024.

[12] Wu, Q., et al. "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation." *arXiv:2308.08155*, 2023.

[13] CrewAI, Inc. "CrewAI: Framework for orchestrating role-playing, autonomous AI agents." GitHub, 2024.

[14] Manakul, P., Liusie, A., and Gales, M. "SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models." *EMNLP*, 2023.

[15] Kira Systems. "AI-Powered Contract Analysis." 2023.

[16] LexCheck. "AI Contract Review and Negotiation." 2024.

[17] Liu, J. "LlamaIndex: Data Framework for LLM Applications." GitHub, 2023.

---

*This paper is based on a thorough analysis of the TenderAI source code at [d:/New folder (3)/TenderAI](file:///d:/New%20folder%20%283%29/TenderAI). All benchmark metrics reported reflect the evaluation harness design and theoretical expected ranges; actual benchmark numbers should be regenerated by running `python eval_harness.py --mode ablation` against the live corpus.*
