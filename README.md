# TenderAI: Enterprise Multi-Agent Tender Analysis, Risk Evaluation & Compliance System

[![CI/CD Pipeline](https://github.com/sharma614/Tender_AI/actions/workflows/ci.yml/badge.svg)](https://github.com/sharma614/Tender_AI/actions/workflows/ci.yml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Vite](https://img.shields.io/badge/Vite-5.4%2B-646CFF.svg)](https://vitejs.dev)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-FF6B35.svg)](https://github.com/langchain-ai/langgraph)
[![Google Gemini](https://img.shields.io/badge/Gemini-3.6--flash-4285F4.svg)](https://ai.google.dev)
[![pgvector](https://img.shields.io/badge/pgvector-HNSW%20Index-336791.svg)](https://github.com/pgvector/pgvector)
[![Render](https://img.shields.io/badge/Render-Cloud%20Services-46E3B7.svg)](https://render.com)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL%20%26%20Storage-3ECF8E.svg)](https://supabase.com)
[![Upstash](https://img.shields.io/badge/Upstash-Redis%20Broker-00E599.svg)](https://upstash.com)
[![Firebase](https://img.shields.io/badge/Firebase-Hosting-FFCA28.svg)](https://firebase.google.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**TenderAI** is a production-grade, enterprise-scale automated tender evaluation platform powered by a **Multi-Agent AI System** orchestrated by LangGraph, a **Vite SPA Web Dashboard** (hosted on Firebase), **Retrieval-Augmented Generation (RAG)** with pgvector cosine similarity search, a **Calibrated Bid/No-Bid Decision Engine**, a **Deterministic Compliance Engine**, and **Celery/Upstash Redis Asynchronous Queues** deployed seamlessly on **Render** (with Supabase PostgreSQL & Storage), alongside alternative **AWS Cloud Microservices** provisioned via **Terraform Infrastructure-as-Code**.

Evaluating a 300-page RFP manually can take 20–40 hours. TenderAI reduces this to minutes by decomposing the problem into specialized agents — each grounded in targeted retrieved context rather than the full document — achieving 100% extraction accuracy (vs. 80% monolithic baseline) with ~45% faster execution latency.

---

## 📑 Table of Contents

- [System Architecture](#-system-architecture)
- [Key Engineering Features](#-key-engineering--architectural-features)
- [Agent Roster](#-agent-roster)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Quick Start (Local Development — Zero Docker Required)](#-quick-start-local-development--zero-docker-required)
- [Environment Variables](#-environment-variables)
- [API Reference](#-api-reference)
- [Evaluation Harness & Empirical Benchmarks](#-evaluation-harness)
- [Bid Decision Engine](#-bid--no-bid-decision-engine)
- [Production Cloud Deployment (Render + Supabase + Upstash + Firebase)](#-production-cloud-deployment-render--supabase--upstash--firebase)
- [AWS Cloud Infrastructure (Terraform Alternative)](#-aws-cloud-infrastructure-terraform-alternative)
- [Testing](#-testing)
- [License](#-license)

---

## 🏛️ System Architecture

```mermaid
graph TD
    User([User / Web Dashboard — Firebase Hosting]) -->|1. Upload RFP Document| API[FastAPI Web Engine — Render]
    API -->|2. Stream & Save PDF| SupaStorage[Supabase Storage Bucket: tenders]
    API -->|3. Enqueue Background Job| Redis[Upstash Redis Broker / rediss://]
    Redis -->|4. Consume Task| Worker[Celery Worker Cluster — Render]

    subgraph Multi-Agent Processing Engine
        Worker -->|5. Chunk & Embed| PGVector[(Supabase PostgreSQL + pgvector)]
        Worker -->|6. Orchestrate| LangGraph[LangGraph Central Orchestrator]

        LangGraph -->|Superstep 1 — concurrent| Agent1[Metadata Extraction Agent]
        LangGraph -->|Superstep 1 — concurrent| Agent2[Executive Summarizer Agent]
        LangGraph -->|Superstep 1 — concurrent| Agent3[Legal/Financial Risk Agent]

        Agent1 -->|Superstep 2| Agent4[Compliance Verification Agent]
        Agent1 & Agent2 & Agent3 & Agent4 -.->|"Targeted pgvector retrieval (RAG)"| PGVector

        Agent4 --> Engine[Bid/No-Bid Decision Engine]
        EvalHarness[Eval Harness] -->|"Ablation Grid & Retrieval Eval"| PGVector
    end

    Engine -->|7. Persist Structured Results| MainDB[(Supabase PostgreSQL Main DB)]
    MainDB -->|8. Fetch Results| API
    API -->|9. Serve Responses & Live Metrics| User
```

**9-Step Data Flow:**

| Step | Component | Action |
|:---:|:---|:---|
| 1 | User → FastAPI | Upload RFP PDF via `POST /upload` — returns `202 Accepted` + `job_id` instantly |
| 2 | FastAPI → Supabase Storage | PDF binary saved to Supabase Storage bucket (`tenders`), saving file path to `Tender.storage_path` |
| 3 | FastAPI → Upstash Redis | API dispatches `process_tender_job` Celery task via TLS-encrypted Redis broker (`rediss://`) |
| 4 | Redis → Celery Worker | Dedicated background worker on Render consumes job with exponential backoff fault tolerance |
| 5 | Celery → pgvector | PDF extracted via PyMuPDF/OCR, chunked (1000/200), embedded (384d), and stored in Supabase pgvector |
| 6 | Celery → LangGraph | Multi-agent graph executes: Extraction, Summary, Risk (concurrent) → Compliance → Bid Decision |
| 7 | Engine → PostgreSQL | Structured facts, risk items, compliance checks, and bid scores committed to Supabase DB |
| 8 | PostgreSQL → FastAPI | Client polls `GET /jobs/{job_id}` to retrieve comprehensive evaluation state |
| 9 | FastAPI → User | Dashboard visualizes executive summary, compliance breakdown, risk ledger, and bid verdict |

---

## 🔑 Key Engineering & Architectural Features

### 1. Multi-Agent System vs. Monolithic LLM Call

The system was evaluated using `eval_harness.py` against both the monolithic baseline (`MonolithicAgent`) and multi-agent RAG pipeline (`AgentOrchestrator`). The empirical metrics below reflect real recorded runs from `eval_results/` artifacts:

| Metric / Dimension | `mono-full` (Monolithic Baseline) | `multi-rag` / `multi-rag-cite` (TenderAI Multi-Agent) |
|:---|:---:|:---:|
| **Context per agent call** | Full document text (~45k chars) | ~12k chars targeted chunks (~12 retrieved chunks) |
| **Field Accuracy (Measured)** | **80.0%** (misses nested clauses like solvency requirements) | **100.0%** (1.0 macro accuracy across all 5 tender fields) |
| **Token Consumption** | 12,426 tokens / doc | 11,635 tokens / doc (includes reasoning tokens) |
| **End-to-End Latency** | 17,866 ms (~17.9s) | 9,745 ms (~9.7s — **~45% lower latency**) |
| **Hallucination Protection** | Unanchored generation ("Lost in the Middle" risk) | Grounded citations verified via deterministic substring containment |
| **Failure Isolation** | Monolithic (single point of failure) | Per-agent fail-soft (Extraction, Summary, Risk, Compliance) |

> ℹ️ **Provenance:** Measured on synthetic tender corpus via `eval_harness.py`. Monolithic metrics recorded in `eval_results/ablation-20261002T070644Z.json`; multi-agent extraction metrics recorded in `eval_results/extraction-20261002T070913Z.json`. Zero synthetic or placeholder numbers.

**Problem:** Monolithic LLM calls on dense tenders suffer from context window degradation (*"Lost in the Middle"*), ungrounded value fabrication, and slow generation latencies.

**Solution:** TenderAI breaks evaluation into specialized, domain-tailored agents. Each agent receives only the top-K retrieved chunks most relevant to its domain (max 12 chunks, 3 per targeted query × up to 5 queries), producing higher signal-to-noise context (~12k chars vs. ~45k chars full document text).

**Agent Isolation Design:** Agents never hold a database Session. Because Extraction, Summarization, and Risk run as concurrent LangGraph nodes, a shared SQLAlchemy Session across threads would corrupt state. Each agent returns a `ToolCallRecord`; the orchestrator's `_persist` node writes them single-threaded after the graph completes.

### 2. LangGraph Orchestration (Pregel-style Concurrent Execution)

```
START ──┬──► [Extraction]  ──► (conditional) ──► [Compliance] ──► [Bid Decision] ──► END
        ├──► [Summarizer]  ──────────────────────────────────────────────────────► END
        └──► [Risk]        ──────────────────────────────────────────────────────► END
             ╰──── Superstep 1 (concurrent) ────╯
```

- **Superstep 1:** `extraction`, `summarizer`, and `risk` fan out from `START` and run **concurrently** via LangGraph's Pregel loop.
- **Superstep 2:** `compliance` runs after `extraction` (depends on `extracted_facts`).
- **Conditional routing:** If `extracted_facts is None`, skip compliance and route directly to `bid_decision`.
- The `tool_calls` field uses an `operator.add` reducer to safely merge concurrent agent instrumentation records.

### 3. Retrieval-Augmented Generation (RAG) & Citation Verification

**Chunking Strategies:**

| Strategy | Method | Chunk Size | Overlap |
|:---|:---|:---:|:---:|
| **Character** (production default) | Sliding window | 1,000 chars | 200 chars |
| **Structure-Aware** | Section/clause heading splits | Variable (up to 1,500 chars) | 150 chars (sub-chunk) |

Structure-aware chunking splits on `SECTION X`, `CLAUSE Y.Z`, `ANNEXURE A`, `PART III` headings to preserve clause semantics. A preamble section is always emitted (often contains the tender title and key dates).

**Embedding:** `sentence-transformers/all-MiniLM-L6-v2` → 384-dimensional dense vectors stored as `VECTOR(384)` in PostgreSQL via pgvector with an HNSW index.

**Targeted Query Sets (per agent):**

| Agent | Sample Queries |
|:---|:---|
| Extraction | `"tender submission deadline closing date"`, `"earnest money deposit EMD bank guarantee"` |
| Summarizer | `"tender title overall scope of work"`, `"contract duration completion timeline"` |
| Risk | `"liquidated damages delay penalty clause"`, `"termination for convenience default clause"` |

**`rag-cite` mode:** Agents populate `CitationRef` fields (`chunk_id`, `page_number`) for each extracted value. `chunk_id = -1` is the sentinel meaning "I cannot attribute this value to any retrieved chunk." Citations are then verified by deterministic substring containment — no LLM judge required.

| Citation Status | Meaning |
|:---|:---|
| `grounded` | The extracted value string appears as a substring of the cited chunk (verifies **presence**, not semantic support) |
| `ungrounded` | `chunk_id == -1` — LLM admitted no attribution |
| `hallucinated` | `chunk_id != -1` but value string **not found** in cited chunk |
| `missing_chunk` | Cited chunk ID not found in the database |

> **Limitation of substring grounding:** A `grounded` verdict means the value string is present somewhere in the chunk — it does not confirm that the chunk *contextually supports* the extracted claim. A figure that appears in the chunk as a counter-example, a historical value, or in a different field would still pass the containment check. This is a necessary but not sufficient condition for full semantic grounding; a semantic verification step (e.g. an LLM judge or NLI model) would be required for stronger guarantees.


### 4. Deterministic Compliance Engine

> "Asking an LLM whether `$5,000,000` clears `Rs. 5 Crore` is unreliable — LLMs struggle with numeric comparison and magnitude scaling."

Compliance checks are performed **deterministically in Python**, not by the LLM:

1. **Minimum Annual Turnover** — Monetary parser handles Crores, Lakhs, Millions, USD, INR (83 INR/USD normalization) → numeric comparison
2. **Solvency Certificate** — Boolean presence check
3. **Minimum Experience** — Regex-extracted years from eligibility criteria → numeric comparison
4. **EMD Acknowledgment** — Presence-based (always compliant if acknowledged)

LLM extracts string requirements → Python parses to normalized floats → Python compares values.

### 5. Calibrated Bid/No-Bid Decision Engine

**Composite Score Formula:**

```
CompositeScore = (ComplianceScore × 0.50) + (TechnicalFit × 0.30) + (max(0, 100 − RiskPenalty) × 0.20)
```

| Sub-Score | Calculation |
|:---|:---|
| `ComplianceScore` | `(compliant + 0.5 × partial) / total × 100` |
| `TechnicalFit` | `50 + (matched_criteria / total_criteria × 50)` — range 50–100 |
| `RiskPenalty` | `min(100, high×25 + medium×10 + low×3)` |

**Recommendation Thresholds** (evaluated top-to-bottom, first match wins):

| Priority | Recommendation | Condition |
|:---:|:---|:---|
| 1 | ❌ `NO_BID` | NOT overall compliant (mandatory compliance failure) |
| 2 | ✅ `GO_BID` | Compliant AND CompositeScore ≥ 70 **AND** RiskPenalty **< 40** |
| 3 | ⚠️ `CONDITIONAL_BID` | Compliant AND CompositeScore ≥ 45 (includes the case where score ≥ 70 but RiskPenalty ≥ 40) |
| 4 | ❌ `NO_BID` | CompositeScore < 45 |

> **Note:** A composite score ≥ 70 with a risk penalty ≥ 40 does **not** produce `GO_BID`. The `GO_BID` branch requires both conditions; if RiskPenalty ≥ 40 the rule falls through to priority 3 and returns `CONDITIONAL_BID`, reflecting that strong financial alignment cannot override a severe risk burden.

**Calibrated Confidence (0.0–1.0):**

```
Confidence = 0.30 × ExtractionCompleteness
           + 0.30 × GroundingRatio
           + 0.20 × ComplianceClarity
           + 0.20 × RiskDepth
```

### 6. Monolithic Baseline & Scientific Ablation

Includes `MonolithicAgent` as a single-prompt control arm processing the full document in one call. The ablation harness runs all four configs on the same corpus, measuring:

| Config | Description |
|:---|:---|
| `mono-full` | Baseline: single LLM call, full document text |
| `multi-full` | Multi-agent, full-context prompts (no retrieval) |
| `multi-rag` | Multi-agent, retrieval-grounded prompts |
| `multi-rag-cite` | Full system: retrieval + citation verification |

Metrics: macro field accuracy, total tokens (including billable reasoning tokens), estimated USD cost, latency, grounding precision, hallucination rate.

> ⚠️ Token counts use `total_token_count` from the Gemini SDK (includes billable "thinking" tokens). `prompt + completion` alone undercounts badly on gemini-3.x flash models.

### 7. Asynchronous Job Processing (Celery + SQS/Redis)

- `POST /upload` returns `202 Accepted` with `job_id` in <100ms — never blocks the client.
- **Exponential Backoff Retry:** Attempt 1 → 30s, Attempt 2 → 120s, Attempt 3 → 480s.
- **Dead-Letter State Machine:** After 3 retries, job marked `dead_letter` with error stored in DB.

```
Job States: pending → processing → [retrying → ...] → completed | dead_letter
```

Two task entry points share the same `run_pipeline` function:
- `process_tender_job` — for direct API uploads
- `process_tender_job_from_s3` — for Lambda → SQS path on AWS

### 8. Corrigendum / Amendment Support

TenderAI can process amendment documents that override parent tender fields:

```bash
# Applies corrigendum overrides to parent tender (deadline, EMD, etc.)
orchestrator.apply_corrigendum(parent_tender_id, corrigendum_text, db)
```

Fields overridden: `submission_deadline`, `minimum_turnover`, `earnest_money_deposit_emd`. Uses full-dict reassignment (not in-place mutation) to satisfy SQLAlchemy's JSON mutation tracking.

---

## 🤖 Agent Roster

| Agent | File | Responsibility | RAG Queries |
|:---|:---|:---|:---:|
| `ExtractionAgent` | `app/agents/extraction.py` | Title, deadline, financial requirements, eligibility criteria | 5 queries |
| `SummarizerAgent` | `app/agents/summarizer_agent.py` | Executive summary, scope of work, key dates | 5 queries |
| `RiskAgent` | `app/agents/risk_agent.py` | Legal/financial risks with severity (Low/Medium/High) and mitigations | 6 queries |
| `ComplianceAgent` | `app/agents/compliance_agent.py` | Maps extracted facts to company profile via deterministic engine | — |
| `MonolithicAgent` | `app/agents/monolithic.py` | Control arm: all outputs in a single LLM call | — |
| `AgentOrchestrator` | `app/agents/orchestrator.py` | Builds and runs the LangGraph `StateGraph` | — |

All agents extend `BaseAgent` (`app/agents/base.py`) which:
- Configures the Gemini client once per process (`_configure_once`)
- Enforces `response_mime_type="application/json"` for structured output
- Times every LLM call (`latency_ms`) and reads token counts
- Produces a `ToolCallRecord` on both success and failure paths
- Clamps prompts at `MAX_PROMPT_CHARS = 1,500,000`

---

## 🛠️ Tech Stack

| Layer | Technology | Version / Provider |
|:---|:---|:---|
| Language | Python | 3.11 / 3.12 |
| API Framework | FastAPI + Uvicorn | ≥ 0.100 |
| Frontend Engine | Vite (SPA, Fast Dev Server & HMR) | ≥ 5.4 |
| UI & Styling | Vanilla CSS Design System + Modern DOM | Responsive Glassmorphism |
| Data Validation | Pydantic v2 | ≥ 2.0 |
| ORM | SQLAlchemy | ≥ 2.0 |
| DB Migrations | Alembic | ≥ 1.13 |
| Database Driver | psycopg v3 (`psycopg[binary]`) | ≥ 3.1.0 |
| LLM Provider | Google Gemini (`gemini-3.6-flash`) | ≥ 0.7.0 |
| Agent Orchestration | LangGraph | ≥ 0.2 |
| LLM Toolkit | LangChain | ≥ 0.2 |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`) | ≥ 3.0, < 4.0 (384-dim dense) |
| Vector Search & Relational DB | Supabase PostgreSQL + pgvector (HNSW) / Local SQLite | Production Managed |
| Object Storage | Supabase Storage (`tenders` bucket) | Managed Cloud S3-compatible |
| Task Queue | Celery | ≥ 5.3 |
| Message Broker | Upstash Redis (TLS `rediss://`) / Local Redis | Serverless Cloud Redis |
| Application Hosting | Render (Web Service `tenderai-api` + Background Worker `tenderai-worker`) | Container Blueprint |
| Frontend Hosting | Firebase Hosting | Production CDN |
| PDF Processing | PyMuPDF (`fitz`) | ≥ 1.24 |
| OCR Fallback | Tesseract + Pillow | — |
| Alternative Cloud IaC | AWS Terraform (ECS Fargate, S3, SQS, RDS, VPC) | `infra/terraform/` |
| CI/CD | GitHub Actions | Automated lint, unit & integration test suite |
| Testing | pytest + pytest-asyncio | 100% passing test suite |

---

## 📁 Project Structure

```
TenderAI/
├── app/
│   ├── agents/
│   │   ├── base.py               # BaseAgent: Gemini client, instrumentation, ToolCallRecord
│   │   ├── orchestrator.py       # LangGraph StateGraph + RAG retrieval + persistence
│   │   ├── extraction.py         # ExtractionAgent — title, deadline, financial, eligibility
│   │   ├── summarizer_agent.py   # SummarizerAgent — executive summary, scope, dates
│   │   ├── risk_agent.py         # RiskAgent — legal/financial risks, severity, mitigations
│   │   ├── compliance_agent.py   # ComplianceAgent — maps facts to company profile
│   │   └── monolithic.py         # MonolithicAgent — single-call baseline
│   ├── services/
│   │   ├── pdf_processor.py      # PyMuPDF extraction, OCR fallback, chunking strategies
│   │   ├── embedder.py           # sentence-transformers TextEmbedder (384d)
│   │   ├── retriever.py          # pgvector / SQLite cosine retrieval
│   │   ├── bid_decision_engine.py # Composite score, recommendation, calibrated confidence
│   │   ├── compliance_engine.py  # Deterministic numeric compliance checks
│   │   └── citation_verifier.py  # Substring-based hallucination detection
│   ├── main.py                   # FastAPI app, endpoints, startup, direct file upload
│   ├── tasks.py                  # Celery background pipeline & Supabase Storage upload helper
│   ├── worker.py                 # Celery app config with Upstash Redis TLS support
│   ├── models.py                 # SQLAlchemy ORM models (Tender with storage_path, TenderChunk, Job, ToolCall)
│   ├── schemas.py                # Pydantic v2 schemas (AgentState, BidDecision, etc.)
│   ├── database.py               # DB engine, SessionLocal, pgvector/SQLite fallback
│   └── metrics.py                # /admin/metrics observability endpoint
├── frontend/                     # Modern Vite Web Dashboard (Zero Docker Required)
│   ├── src/
│   │   ├── main.js               # Reactive SPA client: upload form, health poll, tender list
│   │   └── style.css             # Sleek modern styling & typography
│   ├── index.html                # Vite SPA entrypoint
│   ├── vite.config.js            # Vite server & proxy configuration
│   └── package.json              # Vite scripts (dev, build, preview)
├── public/                       # Static production web assets for Firebase Hosting
├── eval_harness.py               # Evaluation harness (smoke / retrieval / extraction / ablation)
├── eval_results/                 # Recorded empirical benchmark JSON runs (reproducible tables)
├── corpus/                       # Synthetic tender PDFs + annotation JSON files
├── render.yaml                   # Render Blueprint: tenderai-api web service + tenderai-worker background service
├── firebase.json                 # Firebase Hosting configuration & SPA routing rewrites
├── .env.example                  # Environment configuration template for local & production
├── Dockerfile.api                # Container definition for FastAPI API
├── Dockerfile.worker             # Container definition for Celery Worker
├── infra/terraform/              # AWS Terraform IaC alternative (ECS, S3, SQS, RDS, VPC)
├── alembic/                      # Alembic migrations (including 0003_rename_s3_key_to_storage_path.py)
├── tests/                        # pytest unit & integration tests
├── requirements.txt              # Production Python dependencies (includes supabase, psycopg[binary])
└── README.md
```

---

## 🚀 Quick Start (Local Development — Zero Docker Required)

TenderAI runs entirely on native Python and Vite locally — **no Docker containers are required**.

### 1. Clone & Set Up Backend Environment

```bash
git clone https://github.com/sharma614/Tender_AI.git
cd Tender_AI

# Create virtual environment
python -m venv venv
source venv/bin/activate       # Linux/macOS
# venv\Scripts\activate        # Windows

# Install Python dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
cp .env.example .env   # or create .env manually
# Set your GEMINI_API_KEY
# DATABASE_URL can be omitted for local dev (automatically falls back to local SQLite)
```

### 3. Run Database Migrations

```bash
alembic upgrade head
```

### 4. Start the FastAPI Server

```bash
uvicorn app.main:app --reload --port 8000
```

The API Swagger docs are available at `http://localhost:8000/docs`.

### 5. Start the Frontend (Vite)

In a separate terminal, launch the modern web frontend using Vite:

```bash
cd frontend
npm install
npm run dev
```

The frontend development server will launch instantly with Hot Module Replacement (HMR) at:
👉 **`http://localhost:5173`**

### 6. (Optional) Run Asynchronous Celery Worker

For background async queue testing, run the Celery worker natively:

```bash
# If local Redis is running natively on port 6379:
celery -A app.worker worker --loglevel=info
```
*(Note: Core API upload, retrieval evaluation, smoke testing, and field extraction operate out-of-the-box on SQLite without needing Redis or Docker).*

---

## ⚙️ Environment Variables

Copy `.env.example` to `.env` to configure your environment:

| Variable | Tier | Description |
|:---|:---:|:---|
| `GEMINI_API_KEY` | ✅ Required | Google Gemini API key for agent reasoning and extraction |
| `GEMINI_MODEL_NAME` | ➖ Optional | LLM model ID (default: `gemini-3.6-flash`) |
| `DATABASE_URL` | 🌐 Prod / Dev | Supabase PostgreSQL Transaction Pooler connection string (`postgresql://...`). Leave empty in local dev to automatically fall back to SQLite (`tenderai_dev.db`) |
| `CELERY_BROKER_URL` | 🌐 Prod / Dev | Upstash Redis TLS URL (`rediss://:<password>@<host>:6379`) for Render/production or `redis://localhost:6379/0` for local Docker Redis |
| `CELERY_RESULT_BACKEND` | 🌐 Prod / Dev | Same as `CELERY_BROKER_URL` for Celery async task state persistence |
| `SUPABASE_URL` | 📦 Storage | Supabase project URL (`https://<project-ref>.supabase.co`) |
| `SUPABASE_SERVICE_KEY` | 📦 Storage | Supabase `service_role` secret key (Settings → API) |
| `SUPABASE_BUCKET` | 📦 Storage | Supabase Storage bucket for PDF documents (default: `tenders`) |
| `EMBEDDING_MODEL_NAME` | ➖ Optional | HuggingFace embedding model (default: `all-MiniLM-L6-v2`) |
| `AWS_ACCESS_KEY_ID` | ☁️ AWS IaC | AWS credentials (only when deploying via `infra/terraform/`) |
| `AWS_SECRET_ACCESS_KEY` | ☁️ AWS IaC | AWS credentials (only when deploying via `infra/terraform/`) |
| `AWS_DEFAULT_REGION` | ☁️ AWS IaC | AWS region (e.g. `ap-south-1`) |
| `SQS_QUEUE_URL` | ☁️ AWS IaC | SQS queue endpoint URL |

> ✅ Required &nbsp;&nbsp; 🌐 Managed Cloud / Local &nbsp;&nbsp; 📦 Supabase Storage &nbsp;&nbsp; ➖ Optional &nbsp;&nbsp; ☁️ AWS Terraform only

---

## 📊 API Reference

### Core Endpoints

| Method | Endpoint | Auth | Description |
|:---|:---|:---:|:---|
| `GET` | `/health` | — | Service liveness check |
| `POST` | `/upload` | — | Upload tender PDF — non-blocking, returns `job_id` |
| `GET` | `/jobs/{job_id}` | — | Poll job status (`pending → processing → completed / dead_letter`) |
| `GET` | `/tenders` | — | List all processed tenders |
| `GET` | `/admin/metrics` | — | Agent latency P50/P95, success rates, token counts |

### Job Lifecycle States

```
pending  →  processing  →  completed
                ↓
            retrying  (up to 3 times, exponential backoff)
                ↓
           dead_letter
```

### Example: Upload & Poll

```bash
# 1. Upload a tender PDF
curl -X POST http://localhost:8000/upload \
  -F "file=@tender.pdf" \
  -F "company_profile={\"company_name\":\"Acme Tech\",\"annual_turnover\":15000000}"

# Response: {"job_id": 42, "status": "pending"}

# 2. Poll for results
curl http://localhost:8000/jobs/42
# Response: full analysis including bid_decision, risks, compliance, summary
```

---

## 🧪 Evaluation Harness

`eval_harness.py` implements a rigorous, fully reproducible evaluation framework.

> ⚠️ Every metric is computed from actual runs. There are no hardcoded scores.

### Modes

| Mode | DB Required | API Key Required | What It Measures |
|:---|:---:|:---:|:---|
| `smoke` | ❌ | ❌ | Corpus integrity: schema validation + gold span presence in PDFs |
| `retrieval` | ✅ | ❌ | Vector search quality: Recall@1/3/5/10, MRR@10, latency |
| `extraction` | ✅ | ✅ | Field extraction accuracy vs. ground truth (numeric + fuzzy matching) |
| `ablation` | ✅ | ✅ | 4-way config grid: accuracy, tokens, cost, latency, grounding precision |
| `corrigendum` | ✅ | ✅ | Amendment override correctness (deadline, EMD) |

### Commands

```bash
# Corpus integrity check — runs in CI, no secrets needed
python eval_harness.py --mode smoke

# Retrieval quality (Recall@K, MRR)
python eval_harness.py --mode retrieval --k 10

# Field extraction accuracy (requires GEMINI_API_KEY)
python eval_harness.py --mode extraction --limit 5

# Full 4-way ablation grid
python eval_harness.py --mode ablation --limit 5

# Structure-aware chunking comparison
python eval_harness.py --mode retrieval --chunking structure --k 10

# Keep evaluation data in DB for inspection
python eval_harness.py --mode retrieval --k 5 --keep
```

### Empirical Benchmark Results (Real Harness Runs)

Unlike synthetic estimates or speculative claims, all TenderAI benchmarks are empirically measured on the synthetic RFP corpus via `eval_harness.py`. Every run emits an immutable artifact in `eval_results/` for scientific auditability.

#### 1. Retrieval Benchmarks (18 Documents, 342 Gold Questions)
*Artifacts: `eval_results/retrieval-20261002T065849Z.json` & `eval_results/retrieval-20261002T070144Z.json`*

| Chunking Strategy | Chunks Indexed | R@1 | R@3 | R@5 | R@10 | MRR@10 | Mean Latency | P95 Latency |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Character (1000/200)** | 996 | **59.94%** | **73.39%** | **77.49%** | **85.67%** | **0.6808** | **30.58 ms** | **42.50 ms** |
| **Structure-Aware** | 3,562 | **55.26%** | **65.79%** | **69.01%** | **80.12%** | **0.6205** | **78.88 ms** | **93.20 ms** |

> 📌 **Key Architectural Finding:** Fixed-size character windowing (1000 chars, 200 overlap) achieved **+8.48% higher Recall@5** (77.49% vs 69.01%) and **2.6× faster retrieval latency** than heading-based structure splitting. The structure-aware strategy produced over 3.5× more chunks (3,562 vs. 996 chunks), resulting in higher search-space overhead and lower recall on short clauses.

#### 2. Corpus Integrity & Smoke Test (Zero Data Leakage)
*Artifact: `eval_results/smoke-20261012T134122Z.json`*

| Metric | Target | Measured Result | Status |
|:---|:---:|:---:|:---:|
| Documents Validated | 18 | 18 | ✅ PASS |
| Schema Conformity (`corpus/schema.json`) | 100% | 0 failures (100% valid) | ✅ PASS |
| Gold Spans Verified Verbatim in PDF Text | 342 / 342 | 342 / 342 (100% verified) | ✅ PASS |
| Anti-Leakage (Queries quoting answer) | 0 | 0 (No leakage) | ✅ PASS |

#### 3. Field Extraction & Token Efficiency
*Artifacts: `eval_results/extraction-20261002T070913Z.json` & `eval_results/ablation-20261002T070644Z.json`*

| Field / Metric | Monolithic Baseline (`mono-full`) | TenderAI Multi-Agent RAG (`multi-rag`) |
|:---|:---:|:---:|
| Minimum Annual Turnover | 100.0% | **100.0%** |
| Earnest Money Deposit (EMD) | 100.0% | **100.0%** |
| Solvency Certificate Required | 0.0% (lost in context) | **100.0%** |
| Submission Deadline | 100.0% | **100.0%** |
| Tender Title | 100.0% | **100.0%** |
| **Macro Field Accuracy** | **80.0%** | **100.0%** |
| **Mean Total Tokens / Doc** | 12,426 | **11,635** |
| **Mean Latency / Doc** | 17,866 ms (~17.9s) | **9,745 ms (~9.7s)** |

**CI Pass Criterion:** Recall@5 ≥ 50% (fails automatically if retriever degrades).

---

## ⚖️ Bid / No-Bid Decision Engine

The `BidDecisionEngine` (`app/services/bid_decision_engine.py`) produces a fully auditable `BidDecision`:

```python
BidDecision(
    recommendation="GO_BID" | "CONDITIONAL_BID" | "NO_BID",
    composite_score=81.0,          # (87.0 × 0.50) + (75.0 × 0.30) + ((100 − 25.0) × 0.20) = 81.0
    calibrated_confidence=0.87,    # 0.0–1.0
    compliance_score=87.0,         # sub-score (weight: 50%)
    risk_penalty=25.0,             # subtracted from 100 before 20% weighting
    technical_fit_score=75.0,      # sub-score (weight: 30%)
    key_decision_factors=[...],    # human-readable factors
    explanation="GO_BID recommended. Strong alignment...",
)
```

**Formula:**
```
CompositeScore = (ComplianceScore × 0.50)
              + (TechnicalFit   × 0.30)
              + (max(0, 100 − RiskPenalty) × 0.20)
```

---

## ☁️ Production Cloud Deployment (Render + Supabase + Upstash + Firebase)

TenderAI provides a high-performance, cost-effective, zero-maintenance cloud architecture using modern managed platforms:
- **FastAPI Backend & Celery Worker**: Hosted on [Render](https://render.com) using container runtimes declared in `render.yaml`.
- **Database & Vector Store**: Managed PostgreSQL with `pgvector` on [Supabase](https://supabase.com).
- **PDF File Storage**: Supabase Storage (`tenders` bucket) replacing AWS S3.
- **Asynchronous Message Broker**: Serverless Redis with TLS encryption on [Upstash](https://upstash.com).
- **Frontend SPA**: Global edge static hosting on [Firebase Hosting](https://firebase.google.com).

### Step 1: Provision Supabase (PostgreSQL, pgvector & Storage)
1. Sign in to [Supabase](https://supabase.com) and create a new project.
2. **Enable pgvector**: Open the Supabase **SQL Editor** and run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
3. **Get Database Connection String**: Navigate to **Project Settings → Database → Connection string**, select the **Transaction** mode tab (Port `5432` / pooler), and copy the URI:
   ```
   postgresql://postgres.<project-ref>:<db-password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```
4. **Create PDF Storage Bucket**: Go to **Storage → New bucket**, name it `tenders`, and configure public read access or signed URL access.
5. **Collect API Credentials**: Under **Project Settings → API**, copy:
   - Project URL (`SUPABASE_URL`)
   - `service_role` secret key (`SUPABASE_SERVICE_KEY`)

### Step 2: Provision Upstash Redis (Celery Message Broker)
1. Sign in to [Upstash Console](https://console.upstash.com) and create a new Redis database.
2. Under the **Connect** section, copy the TLS-enabled connection string starting with `rediss://`:
   ```
   rediss://:<password>@<host>:6379
   ```

### Step 3: Deploy Backend & Worker on Render via Blueprint
TenderAI includes a complete `render.yaml` Blueprint that deploys both the FastAPI web service (`tenderai-api`) and Celery worker (`tenderai-worker`) with zero manual server configuration.

1. Push your repository to GitHub.
2. Sign in to the [Render Dashboard](https://dashboard.render.com).
3. Click **New + → Blueprint** and select your repository.
4. Render will parse `render.yaml` and prompt you to fill in the shared secret environment variables (`tenderai-secrets` group):
   - `DATABASE_URL`: Your Supabase connection string.
   - `CELERY_BROKER_URL`: Your Upstash Redis `rediss://` URL.
   - `CELERY_RESULT_BACKEND`: Same Upstash Redis `rediss://` URL.
   - `GEMINI_API_KEY`: Your Google Gemini API key.
   - `SUPABASE_URL`: `https://<project-ref>.supabase.co`
   - `SUPABASE_SERVICE_KEY`: Supabase `service_role` secret.
   - `SUPABASE_BUCKET`: `tenders`
5. Click **Apply**. Render will build and deploy both containers (`Dockerfile.api` on port 8000 and `Dockerfile.worker`).

### Step 4: Run Database Migrations
Run Alembic migrations to create tables and vector indexes on Supabase:
```bash
# Locally with your DATABASE_URL set in .env:
alembic upgrade head
```
*(Or execute `alembic upgrade head` via the Render Web Shell on the `tenderai-api` service).*

### Step 5: Deploy Frontend to Firebase Hosting
1. Install Firebase CLI (if not already installed):
   ```bash
   npm install -g firebase-tools
   firebase login
   ```
2. Build the Vite production bundle:
   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```
3. Deploy to Firebase Hosting:
   ```bash
   firebase deploy --only hosting
   ```
Your web dashboard is now live globally on Firebase CDN, streaming tenders directly to your Render API and Supabase/Upstash backend!

---

## ☁️ AWS Cloud Infrastructure (Terraform Alternative)

Full infrastructure defined as code in `infra/terraform/`:

| File | Resources |
|:---|:---|
| `main.tf` | ECS Fargate (API + Worker), RDS PostgreSQL, S3, SQS, Lambda trigger |
| `networking.tf` | VPC, subnets, security groups, NAT gateway |
| `iam.tf` | IAM roles and policies for ECS, Lambda, S3 access |
| `variables.tf` | Configurable variables (region, instance sizes, etc.) |
| `outputs.tf` | ALB DNS, RDS endpoint, SQS URL, S3 bucket name |

### Deploy Infrastructure

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in your values
terraform init
terraform plan
terraform apply -auto-approve
```

### Frontend Production Build (Vite)

To compile the frontend SPA for production deployment (e.g. AWS S3 + CloudFront or Vercel):

```bash
cd frontend
npm run build
# Production bundle output generated in frontend/dist/
```

---

## 🧪 Testing

```bash
# Run all unit & integration tests
python -m pytest tests/ -v

# Run with coverage report
python -m pytest tests/ --cov=app --cov-report=term-missing
```

---

## 📜 License

Licensed under the [MIT License](LICENSE).

---

<details>
<summary>📋 Architecture Decision Records (ADRs)</summary>

**Why LangGraph over raw ThreadPoolExecutor?**
An earlier version used a hand-rolled `ThreadPoolExecutor` alongside the graph, meaning the compiled graph was never exercised and the two execution paths could silently diverge. LangGraph's Pregel loop provides correct concurrent scheduling and typed state reducers (`operator.add` for concurrent `tool_calls` accumulation).

**Why pgvector over a dedicated vector database?**
pgvector integrates directly into the existing PostgreSQL instance, eliminating an additional infrastructure component, reducing operational complexity, and maintaining transactional consistency between tender metadata and chunk embeddings in a single ACID-compliant database.

**Why deterministic compliance instead of LLM compliance?**
LLMs struggle with numeric magnitude scaling and Indian financial formats (`₹5 Crore`, `50 Lakhs`). Python regex parsing + numeric comparison is deterministic, auditable, and requires zero API calls.

**Why `total_token_count` instead of `prompt + completion` for cost estimation?**
On gemini-3.x flash models, `total_token_count` includes billable "thinking" (reasoning) tokens that are not broken out separately. Using `prompt + completion` alone undercounts by an order of magnitude.

**Why full-dict reassignment for `tender.details`?**
SQLAlchemy's default JSON column type is not mutation-tracked. In-place dict edits (e.g., `tender.details["key"] = val`) are silently not flushed. The corrigendum path always reassigns the entire dict to guarantee the ORM sees the change.

</details>
