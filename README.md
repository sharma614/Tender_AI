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
 
**TenderAI** reads a tender document (an RFP), pulls out the facts that matter, flags legal and financial risks, checks eligibility against a company profile, and ends with a bid / no-bid recommendation. The work is split across several LLM agents coordinated by **LangGraph**. Each agent sees only the chunks retrieved for its own job, using **RAG over pgvector**. Compliance checks are **plain Python, not LLM calls**. Uploads go through a Celery queue (**Upstash Redis**), so the API answers immediately while the analysis runs in the background. The web dashboard is a Vite SPA on Firebase, the backend runs on Render with Supabase for Postgres and storage, and there's an alternative AWS setup defined in Terraform.
 
Reading a 300-page RFP by hand can take 20 to 40 hours. TenderAI does it in minutes. On the synthetic tender corpus in this repo, the multi-agent pipeline hit **100% field accuracy** against **80%** for a single-prompt baseline, with **about 45% lower latency**.
 
---
 
## 📑 Table of Contents
 
- [System Architecture](#-system-architecture)
- [Key Engineering Features](#-key-engineering--architectural-features)
- [Agent Roster](#-agent-roster)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Quick Start (Local Development, Zero Docker Required)](#-quick-start-local-development-zero-docker-required)
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
    User([User / Web Dashboard - Firebase Hosting]) -->|1. Upload RFP Document| API[FastAPI Web Engine - Render]
    API -->|2. Stream & Save PDF| SupaStorage[Supabase Storage Bucket: tenders]
    API -->|3. Enqueue Background Job| Redis[Upstash Redis Broker / rediss://]
    Redis -->|4. Consume Task| Worker[Celery Worker Cluster - Render]
 
    subgraph Multi-Agent Processing Engine
        Worker -->|5. Chunk & Embed| PGVector[(Supabase PostgreSQL + pgvector)]
        Worker -->|6. Orchestrate| LangGraph[LangGraph Central Orchestrator]
 
        LangGraph -->|Superstep 1 - concurrent| Agent1[Metadata Extraction Agent]
        LangGraph -->|Superstep 1 - concurrent| Agent2[Executive Summarizer Agent]
        LangGraph -->|Superstep 1 - concurrent| Agent3[Legal/Financial Risk Agent]
 
        Agent1 -->|Superstep 2| Agent4[Compliance Verification Agent]
        Agent1 & Agent2 & Agent3 & Agent4 -.->|"Targeted pgvector retrieval (RAG)"| PGVector
 
        Agent4 --> Engine[Bid/No-Bid Decision Engine]
        EvalHarness[Eval Harness] -->|"Ablation Grid & Retrieval Eval"| PGVector
    end
 
    Engine -->|7. Persist Structured Results| MainDB[(Supabase PostgreSQL Main DB)]
    MainDB -->|8. Fetch Results| API
    API -->|9. Serve Responses & Live Metrics| User
```
 
The nine steps:
 
| Step | Component | What happens |
|:---:|:---|:---|
| 1 | User to FastAPI | The RFP PDF is uploaded with `POST /upload`. The API returns `202 Accepted` and a `job_id` right away. |
| 2 | FastAPI to Supabase Storage | The PDF goes into the `tenders` bucket, and its path is saved in `Tender.storage_path`. |
| 3 | FastAPI to Upstash Redis | The API dispatches a `process_tender_job` Celery task over a TLS Redis broker (`rediss://`). |
| 4 | Redis to Celery worker | A background worker on Render picks up the job. Failures are retried with exponential backoff. |
| 5 | Celery to pgvector | The PDF is extracted with PyMuPDF (OCR as fallback), chunked (1000/200), embedded (384 dimensions) and stored in Supabase pgvector. |
| 6 | Celery to LangGraph | The agent graph runs: Extraction, Summary and Risk concurrently, then Compliance, then Bid Decision. |
| 7 | Engine to PostgreSQL | Extracted facts, risk items, compliance checks and bid scores are committed to the database. |
| 8 | PostgreSQL to FastAPI | The client polls `GET /jobs/{job_id}` for the full evaluation state. |
| 9 | FastAPI to User | The dashboard shows the executive summary, compliance breakdown, risk ledger and bid verdict. |
 
---
 
## 🔑 Key Engineering & Architectural Features
 
### 1. Multi-agent pipeline vs. one big LLM call
 
`eval_harness.py` compares a monolithic baseline (`MonolithicAgent`) with the multi-agent RAG pipeline (`AgentOrchestrator`). The numbers below come from recorded runs in `eval_results/`.
 
| Metric | `mono-full` (monolithic baseline) | `multi-rag` / `multi-rag-cite` (TenderAI) |
|:---|:---:|:---:|
| **Context per agent call** | Full document text (~45k chars) | ~12k chars of retrieved chunks (about 12 chunks) |
| **Field accuracy** | **80.0%** (misses nested clauses such as solvency requirements) | **100.0%** (macro accuracy across all 5 tender fields) |
| **Tokens per document** | 12,426 | 11,635 (includes reasoning tokens) |
| **End-to-end latency** | 17,866 ms (~17.9 s) | 9,745 ms (~9.7 s), **about 45% lower** |
| **Hallucination protection** | None. Output is unanchored, so "lost in the middle" errors are possible | Citations checked by substring containment |
| **Failure isolation** | A single failure breaks everything | Each agent fails on its own (Extraction, Summary, Risk, Compliance) |
 
**Provenance:** measured on the synthetic tender corpus with `eval_harness.py`. Monolithic numbers are in `eval_results/ablation-20261002T070644Z.json`, and multi-agent extraction numbers are in `eval_results/extraction-20261002T070913Z.json`. Nothing here is estimated or filled in by hand. Because the corpus is synthetic, treat these as a comparison between the two approaches rather than a forecast for real-world tenders.
 
**Problem:** the issue with a single call over a dense tender is that models lose details buried in the middle of long documents, invent values, and take a long time to respond. **Solution:** TenderAI splits the job across agents. Each one gets the top-K chunks for its domain (at most 12 chunks: 3 per query, up to 5 queries), so the context is about 12k characters instead of 45k.
 
**Agent isolation design:** agents never hold a database session. Extraction, Summarization and Risk run as concurrent LangGraph nodes, and a SQLAlchemy Session shared across threads would corrupt state. Each agent returns a `ToolCallRecord` instead, and the orchestrator's `_persist` node writes them from a single thread once the graph finishes.
 
### 2. LangGraph orchestration (Pregel-style concurrency)
 
```
START ──┬──► [Extraction]  ──► (conditional) ──► [Compliance] ──► [Bid Decision] ──► END
        ├──► [Summarizer]  ──────────────────────────────────────────────────────► END
        └──► [Risk]        ──────────────────────────────────────────────────────► END
             ╰──── Superstep 1 (concurrent) ────╯
```
 
- **Superstep 1:** `extraction`, `summarizer` and `risk` fan out from `START` and run concurrently in LangGraph's Pregel loop.
- **Superstep 2:** `compliance` runs after `extraction`, since it needs `extracted_facts`.
- **Conditional routing:** if `extracted_facts is None`, compliance is skipped and the graph goes straight to `bid_decision`.
- The `tool_calls` field uses an `operator.add` reducer so records from concurrent agents merge safely.
### 3. Retrieval and citation verification
 
Chunking strategies:
 
| Strategy | Method | Chunk size | Overlap |
|:---|:---|:---:|:---:|
| **Character** (production default) | Sliding window | 1,000 chars | 200 chars |
| **Structure-aware** | Splits on section and clause headings | Variable, up to 1,500 chars | 150 chars (sub-chunks) |
 
Structure-aware chunking splits on headings like `SECTION X`, `CLAUSE Y.Z`, `ANNEXURE A` and `PART III` so clauses stay intact. It always emits a preamble section, which often holds the tender title and key dates.
 
Embeddings come from `sentence-transformers/all-MiniLM-L6-v2` (384-dimensional), stored as `VECTOR(384)` in PostgreSQL with an HNSW index.
 
Each agent has its own set of retrieval queries:
 
| Agent | Sample queries |
|:---|:---|
| **Extraction** | `"tender submission deadline closing date"`, `"earnest money deposit EMD bank guarantee"` |
| **Summarizer** | `"tender title overall scope of work"`, `"contract duration completion timeline"` |
| **Risk** | `"liquidated damages delay penalty clause"`, `"termination for convenience default clause"` |
 
**`rag-cite` mode:** in this mode, agents fill in `CitationRef` fields (`chunk_id`, `page_number`) for every extracted value. A `chunk_id` of `-1` means the agent couldn't attribute the value to any retrieved chunk. Citations are then checked by plain substring containment, with no LLM judge.
 
| Status | Meaning |
|:---|:---|
| `grounded` | The extracted value appears as a substring of the cited chunk. This confirms the value is present, not that the chunk supports it. |
| `ungrounded` | `chunk_id == -1`. The model admitted it had no source. |
| `hallucinated` | `chunk_id != -1`, but the value isn't in the cited chunk. |
| `missing_chunk` | The cited chunk ID doesn't exist in the database. |
 
A `grounded` result only means the string shows up somewhere in the chunk. A figure that appears as a counter-example, a historical value, or under a different field would still pass. So substring grounding is necessary but not sufficient. Stronger guarantees would need a semantic check, such as an LLM judge or an NLI model.
 
### 4. Deterministic compliance engine
 
LLMs are unreliable at questions like whether `$5,000,000` clears `Rs. 5 Crore`. They get magnitudes and unit conversions wrong. So compliance is checked in Python:
 
1. **Minimum Annual Turnover.** A monetary parser handles Crores, Lakhs, Millions, USD and INR (normalized at 83 INR/USD), then compares the numbers.
2. **Solvency Certificate.** A boolean presence check.
3. **Minimum Experience.** Years are pulled from the eligibility text with a regex and compared numerically.
4. **EMD Acknowledgment.** Presence only; compliant whenever it's acknowledged.
The LLM extracts the requirement strings, Python parses them into normalized floats, and Python does the comparison.
 
### 5. Bid / no-bid decision engine
 
```
CompositeScore = (ComplianceScore × 0.50) + (TechnicalFit × 0.30) + (max(0, 100 − RiskPenalty) × 0.20)
```
 
| Sub-score | Calculation |
|:---|:---|
| **`ComplianceScore`** | `(compliant + 0.5 × partial) / total × 100` |
| **`TechnicalFit`** | `50 + (matched_criteria / total_criteria × 50)`, so it ranges from 50 to 100 |
| **`RiskPenalty`** | `min(100, high×25 + medium×10 + low×3)` |
 
Rules are evaluated top to bottom, and the first match wins:
 
| Priority | Recommendation | Condition |
|:---:|:---|:---|
| 1 | ❌ `NO_BID` | Not overall compliant (a mandatory compliance failure) |
| 2 | ✅ `GO_BID` | Compliant AND CompositeScore ≥ 70 **AND** RiskPenalty **< 40** |
| 3 | ⚠️ `CONDITIONAL_BID` | Compliant and CompositeScore ≥ 45 (this includes score ≥ 70 with RiskPenalty ≥ 40) |
| 4 | ❌ `NO_BID` | CompositeScore < 45 |
 
**Note:** a score of 70 or more with a risk penalty of 40 or more does **not** give `GO_BID`. It falls through to `CONDITIONAL_BID`, because a strong financial fit shouldn't cancel out a heavy risk load.
 
Confidence (0.0 to 1.0):
 
```
Confidence = 0.30 × ExtractionCompleteness
           + 0.30 × GroundingRatio
           + 0.20 × ComplianceClarity
           + 0.20 × RiskDepth
```
 
### 6. Monolithic baseline and ablation
 
`MonolithicAgent` is a single-prompt control that processes the whole document in one call. The ablation harness runs four configurations on the same corpus:
 
| Config | Description |
|:---|:---|
| `mono-full` | Baseline: one LLM call, full document text |
| `multi-full` | Multi-agent, full-context prompts (no retrieval) |
| `multi-rag` | Multi-agent, retrieval-grounded prompts |
| `multi-rag-cite` | Full system: retrieval plus citation verification |
 
It measures macro field accuracy, total tokens (including billable reasoning tokens), estimated USD cost, latency, grounding precision and hallucination rate.
 
Token counts use `total_token_count` from the Gemini SDK, which includes billable "thinking" tokens. Adding up `prompt + completion` alone undercounts badly on gemini-3.x flash models.
 
### 7. Asynchronous job processing (Celery with Redis or SQS)
 
- **`POST /upload`** returns `202 Accepted` with a `job_id` in under 100 ms and never blocks the client.
- **Exponential backoff retry:** 30 s after attempt 1, 120 s after attempt 2, 480 s after attempt 3.
- **Dead-letter state machine:** after 3 retries the job is marked `dead_letter` and the error is stored in the database.
```
Job states: pending → processing → [retrying → ...] → completed | dead_letter
```
 
Two task entry points share the same `run_pipeline` function:
- `process_tender_job` for direct API uploads
- `process_tender_job_from_s3` for the Lambda to SQS path on AWS
### 8. Corrigendum / amendment support
 
TenderAI can process amendment documents that override fields on the parent tender:
 
```bash
# Applies corrigendum overrides to the parent tender (deadline, EMD, etc.)
orchestrator.apply_corrigendum(parent_tender_id, corrigendum_text, db)
```
 
The overridable fields are `submission_deadline`, `minimum_turnover` and `earnest_money_deposit_emd`. The code reassigns the whole dict instead of mutating it in place, because SQLAlchemy doesn't track in-place changes to JSON columns.
 
---
 
## 🤖 Agent Roster
 
| Agent | File | Responsibility | RAG queries |
|:---|:---|:---|:---:|
| **`ExtractionAgent`** | `app/agents/extraction.py` | Title, deadline, financial requirements, eligibility criteria | 5 |
| **`SummarizerAgent`** | `app/agents/summarizer_agent.py` | Executive summary, scope of work, key dates | 5 |
| **`RiskAgent`** | `app/agents/risk_agent.py` | Legal and financial risks with severity (Low/Medium/High) and mitigations | 6 |
| **`ComplianceAgent`** | `app/agents/compliance_agent.py` | Maps extracted facts to the company profile through the deterministic engine | none |
| **`MonolithicAgent`** | `app/agents/monolithic.py` | Control arm: every output in one LLM call | none |
| **`AgentOrchestrator`** | `app/agents/orchestrator.py` | Builds and runs the LangGraph `StateGraph` | none |
 
All agents extend `BaseAgent` (`app/agents/base.py`), which:
- configures the Gemini client once per process (`_configure_once`)
- enforces `response_mime_type="application/json"` for structured output
- times every LLM call (`latency_ms`) and reads token counts
- produces a `ToolCallRecord` on both success and failure
- clamps prompts at `MAX_PROMPT_CHARS = 1,500,000`
---
 
## 🛠️ Tech Stack
 
| Layer | Technology | Version / provider |
|:---|:---|:---|
| Language | Python | 3.11 / 3.12 |
| API framework | FastAPI + Uvicorn | ≥ 0.100 |
| Frontend | Vite SPA | ≥ 5.4 |
| UI and styling | Vanilla CSS design system | Responsive, glassmorphism |
| Data validation | Pydantic v2 | ≥ 2.0 |
| ORM | SQLAlchemy | ≥ 2.0 |
| DB migrations | Alembic | ≥ 1.13 |
| Database driver | psycopg v3 (`psycopg[binary]`) | ≥ 3.1.0 |
| LLM | Google Gemini (`gemini-3.6-flash`) | ≥ 0.7.0 |
| Agent orchestration | LangGraph | ≥ 0.2 |
| LLM toolkit | LangChain | ≥ 0.2 |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`) | ≥ 3.0, < 4.0 (384-dim) |
| Vector search and relational DB | Supabase PostgreSQL + pgvector (HNSW), or local SQLite | Managed |
| Object storage | Supabase Storage (`tenders` bucket) | Managed, S3-compatible |
| Task queue | Celery | ≥ 5.3 |
| Message broker | Upstash Redis (TLS `rediss://`) or local Redis | Serverless |
| Application hosting | Render (web service `tenderai-api`, background worker `tenderai-worker`) | Container blueprint |
| Frontend hosting | Firebase Hosting | CDN |
| PDF processing | PyMuPDF (`fitz`) | ≥ 1.24 |
| OCR fallback | Tesseract + Pillow | |
| Alternative IaC | AWS Terraform (ECS Fargate, S3, SQS, RDS, VPC) | `infra/terraform/` |
| CI/CD | GitHub Actions | Lint, unit and integration tests |
| Testing | pytest + pytest-asyncio | |
 
---
 
## 📁 Project Structure
 
```
TenderAI/
├── app/
│   ├── agents/
│   │   ├── base.py               # BaseAgent: Gemini client, instrumentation, ToolCallRecord
│   │   ├── orchestrator.py       # LangGraph StateGraph + RAG retrieval + persistence
│   │   ├── extraction.py         # ExtractionAgent: title, deadline, financial, eligibility
│   │   ├── summarizer_agent.py   # SummarizerAgent: executive summary, scope, dates
│   │   ├── risk_agent.py         # RiskAgent: legal/financial risks, severity, mitigations
│   │   ├── compliance_agent.py   # ComplianceAgent: maps facts to company profile
│   │   └── monolithic.py         # MonolithicAgent: single-call baseline
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
├── frontend/                     # Vite web dashboard (no Docker needed)
│   ├── src/
│   │   ├── main.js               # SPA client: upload form, health poll, tender list
│   │   └── style.css             # Styling and typography
│   ├── index.html                # Vite SPA entrypoint
│   ├── vite.config.js            # Vite server & proxy configuration
│   └── package.json              # Vite scripts (dev, build, preview)
├── public/                       # Static production web assets for Firebase Hosting
├── eval_harness.py               # Evaluation harness (smoke / retrieval / extraction / ablation)
├── eval_results/                 # Recorded benchmark JSON runs
├── corpus/                       # Synthetic tender PDFs + annotation JSON files
├── render.yaml                   # Render Blueprint: tenderai-api web service + tenderai-worker
├── firebase.json                 # Firebase Hosting config & SPA routing rewrites
├── .env.example                  # Environment template for local & production
├── Dockerfile.api                # Container definition for the FastAPI API
├── Dockerfile.worker             # Container definition for the Celery worker
├── infra/terraform/              # AWS Terraform alternative (ECS, S3, SQS, RDS, VPC)
├── alembic/                      # Alembic migrations (including 0003_rename_s3_key_to_storage_path.py)
├── tests/                        # pytest unit & integration tests
├── requirements.txt              # Production Python dependencies (includes supabase, psycopg[binary])
└── README.md
```
 
---
 
## 🚀 Quick Start (Local Development, Zero Docker Required)
 
Everything runs locally on plain Python and Vite. No containers needed.
 
### 1. Clone and set up the backend
 
```bash
git clone https://github.com/sharma614/Tender_AI.git
cd Tender_AI
 
# Create a virtual environment
python -m venv venv
source venv/bin/activate       # Linux/macOS
# venv\Scripts\activate        # Windows
 
# Install dependencies
pip install -r requirements.txt
```
 
### 2. Configure environment variables
 
```bash
cp .env.example .env   # or create .env manually
# Set your GEMINI_API_KEY
# DATABASE_URL can be left out for local dev (falls back to local SQLite)
```
 
### 3. Run migrations
 
```bash
alembic upgrade head
```
 
### 4. Start the API
 
```bash
uvicorn app.main:app --reload --port 8000
```
 
Swagger docs are at `http://localhost:8000/docs`.
 
### 5. Start the frontend
 
In a second terminal:
 
```bash
cd frontend
npm install
npm run dev
```
 
The dev server starts with hot module replacement at `http://localhost:5173`.
 
### 6. (Optional) Run the Celery worker
 
To test the background queue, run the worker natively:
 
```bash
# Requires Redis running locally on port 6379
celery -A app.worker worker --loglevel=info
```
 
Uploads, retrieval evaluation, smoke tests and field extraction all work on SQLite without Redis or Docker.
 
---
 
## ⚙️ Environment Variables
 
Copy `.env.example` to `.env`:
 
| Variable | Tier | Description |
|:---|:---:|:---|
| `GEMINI_API_KEY` | ✅ Required | Google Gemini API key for agent reasoning and extraction |
| `GEMINI_MODEL_NAME` | ➖ Optional | LLM model ID (default: `gemini-3.6-flash`) |
| `DATABASE_URL` | 🌐 Prod / dev | Supabase PostgreSQL Transaction Pooler connection string (`postgresql://...`). Leave empty locally to fall back to SQLite (`tenderai_dev.db`). |
| `CELERY_BROKER_URL` | 🌐 Prod / dev | Upstash Redis TLS URL (`rediss://:<password>@<host>:6379`) for Render, or `redis://localhost:6379/0` for local Redis |
| `CELERY_RESULT_BACKEND` | 🌐 Prod / dev | Same as `CELERY_BROKER_URL`; stores Celery task state |
| `SUPABASE_URL` | 📦 Storage | Supabase project URL (`https://<project-ref>.supabase.co`) |
| `SUPABASE_SERVICE_KEY` | 📦 Storage | Supabase `service_role` secret key (Settings → API) |
| `SUPABASE_BUCKET` | 📦 Storage | Storage bucket for PDFs (default: `tenders`) |
| `EMBEDDING_MODEL_NAME` | ➖ Optional | HuggingFace embedding model (default: `all-MiniLM-L6-v2`) |
| `AWS_ACCESS_KEY_ID` | ☁️ AWS only | AWS credentials, needed only when deploying with `infra/terraform/` |
| `AWS_SECRET_ACCESS_KEY` | ☁️ AWS only | AWS credentials, needed only when deploying with `infra/terraform/` |
| `AWS_DEFAULT_REGION` | ☁️ AWS only | AWS region (for example `ap-south-1`) |
| `SQS_QUEUE_URL` | ☁️ AWS only | SQS queue endpoint URL |
 
---
 
## 📊 API Reference
 
| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/health` | Liveness check |
| `POST` | `/upload` | Upload a tender PDF. Non-blocking; returns a `job_id`. |
| `GET` | `/jobs/{job_id}` | Poll job status (`pending → processing → completed / dead_letter`) |
| `GET` | `/tenders` | List processed tenders |
| `GET` | `/admin/metrics` | Agent latency P50/P95, success rates, token counts |
 
None of these endpoints require authentication.
 
Job lifecycle:
 
```
pending  →  processing  →  completed
                ↓
            retrying  (up to 3 times, exponential backoff)
                ↓
           dead_letter
```
 
Example upload and poll:
 
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
 
`eval_harness.py` runs the benchmarks and is meant to be fully reproducible. Every metric comes from an actual run, and no scores are hardcoded.
 
| Mode | DB | API key | What it measures |
|:---|:---:|:---:|:---|
| `smoke` | No | No | Corpus integrity: schema validation and gold-span presence in the PDFs |
| `retrieval` | Yes | No | Vector search quality: Recall@1/3/5/10, MRR@10, latency |
| `extraction` | Yes | Yes | Field extraction accuracy against ground truth (numeric and fuzzy matching) |
| `ablation` | Yes | Yes | The 4-way config grid: accuracy, tokens, cost, latency, grounding precision |
| `corrigendum` | Yes | Yes | Whether amendments override the right fields (deadline, EMD) |
 
```bash
# Corpus integrity check (runs in CI, needs no secrets)
python eval_harness.py --mode smoke
 
# Retrieval quality (Recall@K, MRR)
python eval_harness.py --mode retrieval --k 10
 
# Field extraction accuracy (needs GEMINI_API_KEY)
python eval_harness.py --mode extraction --limit 5
 
# Full 4-way ablation grid
python eval_harness.py --mode ablation --limit 5
 
# Structure-aware chunking comparison
python eval_harness.py --mode retrieval --chunking structure --k 10
 
# Keep evaluation data in the DB for inspection
python eval_harness.py --mode retrieval --k 5 --keep
```
 
### Benchmark results
 
All results were measured on the synthetic RFP corpus. Each run writes an artifact to `eval_results/` so it can be audited.
 
#### 1. Retrieval (18 documents, 342 gold questions)
 
Artifacts: `eval_results/retrieval-20261002T065849Z.json` and `eval_results/retrieval-20261002T070144Z.json`
 
| Chunking | Chunks indexed | R@1 | R@3 | R@5 | R@10 | MRR@10 | Mean latency | P95 latency |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Character (1000/200)** | 996 | **59.94%** | **73.39%** | **77.49%** | **85.67%** | **0.6808** | **30.58 ms** | **42.50 ms** |
| **Structure-Aware** | 3,562 | 55.26% | 65.79% | 69.01% | 80.12% | 0.6205 | 78.88 ms | 93.20 ms |
 
Fixed-size character windows (1000 chars, 200 overlap) beat heading-based splitting: **77.49% Recall@5** against 69.01%, about **8.5 points higher**, and retrieval ran roughly **2.6× faster**. The structure-aware strategy produced more than 3.5× as many chunks (3,562 vs. 996), which enlarges the search space and hurt recall on short clauses.
 
#### 2. Corpus integrity (smoke test)
 
Artifact: `eval_results/smoke-20261012T134122Z.json`
 
| Check | Target | Result | Status |
|:---|:---:|:---:|:---:|
| Documents validated | 18 | 18 | ✅ PASS |
| Schema conformity (`corpus/schema.json`) | 100% | 0 failures | ✅ PASS |
| Gold spans found verbatim in PDF text | 342 / 342 | 342 / 342 | ✅ PASS |
| Leakage (queries quoting the answer) | 0 | 0 | ✅ PASS |
 
#### 3. Field extraction and token use
 
Artifacts: `eval_results/extraction-20261002T070913Z.json` and `eval_results/ablation-20261002T070644Z.json`
 
| Field / metric | Monolithic (`mono-full`) | Multi-agent RAG (`multi-rag`) |
|:---|:---:|:---:|
| Minimum annual turnover | 100.0% | 100.0% |
| Earnest money deposit (EMD) | 100.0% | 100.0% |
| Solvency certificate required | 0.0% (lost in context) | **100.0%** |
| Submission deadline | 100.0% | 100.0% |
| Tender title | 100.0% | 100.0% |
| **Macro field accuracy** | **80.0%** | **100.0%** |
| **Mean total tokens per doc** | 12,426 | **11,635** |
| **Mean latency per doc** | 17,866 ms (~17.9 s) | **9,745 ms** (~9.7 s) |
 
**CI pass criterion:** Recall@5 ≥ 50%. The build fails if the retriever drops below that.
 
---
 
## ⚖️ Bid / No-Bid Decision Engine
 
`BidDecisionEngine` (`app/services/bid_decision_engine.py`) returns an auditable `BidDecision`:
 
```python
BidDecision(
    recommendation="GO_BID" | "CONDITIONAL_BID" | "NO_BID",
    composite_score=81.0,          # (87.0 × 0.50) + (75.0 × 0.30) + ((100 − 25.0) × 0.20) = 81.0
    calibrated_confidence=0.87,    # 0.0 to 1.0
    compliance_score=87.0,         # sub-score (weight: 50%)
    risk_penalty=25.0,             # subtracted from 100 before the 20% weighting
    technical_fit_score=75.0,      # sub-score (weight: 30%)
    key_decision_factors=[...],    # human-readable factors
    explanation="GO_BID recommended. Strong alignment...",
)
```
 
Formula:
 
```
CompositeScore = (ComplianceScore × 0.50)
              + (TechnicalFit   × 0.30)
              + (max(0, 100 − RiskPenalty) × 0.20)
```
 
---
 
## ☁️ Production Cloud Deployment (Render + Supabase + Upstash + Firebase)
 
This setup uses managed services and needs no server administration:
- FastAPI backend and Celery worker on [Render](https://render.com), declared in `render.yaml`
- PostgreSQL with `pgvector` on [Supabase](https://supabase.com)
- PDF storage in Supabase Storage (`tenders` bucket), which replaces AWS S3
- Redis broker with TLS on [Upstash](https://upstash.com)
- Frontend SPA on [Firebase Hosting](https://firebase.google.com)
### Step 1: Set up Supabase (PostgreSQL, pgvector, Storage)
 
1. Sign in to [Supabase](https://supabase.com) and create a project.
2. Enable pgvector. In the **SQL Editor**, run:
```sql
   CREATE EXTENSION IF NOT EXISTS vector;
```
3. Get the connection string. Go to **Project Settings → Database → Connection string**, pick the **Transaction** tab (port `5432`, pooler), and copy the URI:
```
   postgresql://postgres.<project-ref>:<db-password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```
4. Create the storage bucket. Under **Storage → New bucket**, name it `tenders` and set either public read access or signed URLs.
5. Collect API credentials from **Project Settings → API**:
   - Project URL (`SUPABASE_URL`)
   - `service_role` secret key (`SUPABASE_SERVICE_KEY`)
### Step 2: Set up Upstash Redis (Celery broker)
 
1. Sign in to the [Upstash Console](https://console.upstash.com) and create a Redis database.
2. Under **Connect**, copy the TLS connection string that starts with `rediss://`:
```
   rediss://:<password>@<host>:6379
```
 
### Step 3: Deploy the backend and worker on Render
 
`render.yaml` defines both the FastAPI web service (`tenderai-api`) and the Celery worker (`tenderai-worker`), so there's no manual server setup.
 
1. Push the repository to GitHub.
2. Sign in to the [Render Dashboard](https://dashboard.render.com).
3. Click **New + → Blueprint** and pick your repository.
4. Render parses `render.yaml` and asks for the shared secrets (`tenderai-secrets` group):
   - `DATABASE_URL`: your Supabase connection string
   - `CELERY_BROKER_URL`: your Upstash `rediss://` URL
   - `CELERY_RESULT_BACKEND`: the same Upstash URL
   - `GEMINI_API_KEY`: your Google Gemini API key
   - `SUPABASE_URL`: `https://<project-ref>.supabase.co`
   - `SUPABASE_SERVICE_KEY`: the Supabase `service_role` secret
   - `SUPABASE_BUCKET`: `tenders`
5. Click **Apply**. Render builds and deploys both containers (`Dockerfile.api` on port 8000 and `Dockerfile.worker`).
### Step 4: Run migrations
 
Create the tables and vector indexes on Supabase:
 
```bash
# Locally, with DATABASE_URL set in .env:
alembic upgrade head
```
 
You can also run `alembic upgrade head` from the Render web shell on the `tenderai-api` service.
 
### Step 5: Deploy the frontend to Firebase Hosting
 
1. Install the Firebase CLI if you don't have it:
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
3. Deploy:
```bash
   firebase deploy --only hosting
```
 
The dashboard is now live on Firebase's CDN and talks to the Render API.
 
---
 
## ☁️ AWS Cloud Infrastructure (Terraform Alternative)
 
The AWS alternative is defined in `infra/terraform/`:
 
| File | Resources |
|:---|:---|
| `main.tf` | ECS Fargate (API + worker), RDS PostgreSQL, S3, SQS, Lambda trigger |
| `networking.tf` | VPC, subnets, security groups, NAT gateway |
| `iam.tf` | IAM roles and policies for ECS, Lambda and S3 access |
| `variables.tf` | Configurable variables (region, instance sizes, etc.) |
| `outputs.tf` | ALB DNS, RDS endpoint, SQS URL, S3 bucket name |
 
Deploy:
 
```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in your values
terraform init
terraform plan
terraform apply -auto-approve
```
 
To build the frontend for another host (S3 + CloudFront, Vercel, etc.):
 
```bash
cd frontend
npm run build
# Output lands in frontend/dist/
```
 
---
 
## 🧪 Testing
 
```bash
# Run all unit and integration tests
python -m pytest tests/ -v
 
# With a coverage report
python -m pytest tests/ --cov=app --cov-report=term-missing
```
 
---
 
## 📜 License
 
Licensed under the [MIT License](LICENSE).
 
---
 
<details>
<summary>Architecture decision records</summary>
**Why LangGraph instead of a raw ThreadPoolExecutor?**
An earlier version ran a hand-rolled `ThreadPoolExecutor` next to the graph. The compiled graph was never actually used, and the two execution paths could drift apart without anyone noticing. LangGraph's Pregel loop handles concurrent scheduling properly and gives typed state reducers (`operator.add` for merging concurrent `tool_calls`).
 
**Why pgvector instead of a dedicated vector database?**
pgvector lives in the PostgreSQL instance we already run. That removes one piece of infrastructure and keeps tender metadata and chunk embeddings in the same ACID database.
 
**Why deterministic compliance instead of LLM compliance?**
LLMs trip over magnitude scaling and Indian number formats like `₹5 Crore` and `50 Lakhs`. Regex parsing plus numeric comparison in Python is deterministic, easy to audit, and needs no API calls.
 
**Why `total_token_count` instead of `prompt + completion` for cost estimates?**
On gemini-3.x flash models, `total_token_count` includes billable "thinking" tokens that aren't broken out separately. Summing prompt and completion alone undercounts by an order of magnitude.
 
**Why reassign the whole dict for `tender.details`?**
SQLAlchemy's default JSON column type doesn't track mutations. An in-place edit like `tender.details["key"] = val` is silently never flushed. The corrigendum path reassigns the entire dict so the ORM sees the change.
 
</details>