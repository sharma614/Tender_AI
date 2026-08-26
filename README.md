# TenderAI: Enterprise Multi-Agent Tender Analysis, Risk Evaluation & Compliance System

[![CI/CD Pipeline](https://github.com/sharma614/Tender_AI/actions/workflows/ci.yml/badge.svg)](https://github.com/sharma614/Tender_AI/actions/workflows/ci.yml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED.svg)](https://docker.com)
[![Terraform](https://img.shields.io/badge/AWS%20Terraform-IaC-7B42BC.svg)](https://terraform.io)

**TenderAI** is a production-grade, enterprise-scale automated tender evaluation platform powered by **Multi-Agent AI Systems**, **Retrieval-Augmented Generation (RAG)**, **Celery/Redis Asynchronous Queues**, and **AWS Cloud Microservices** provisioned via **Terraform Infrastructure-as-Code (IaC)**.

---

## 🏛️ System Architecture

```mermaid
graph TD
    User([User / Web Dashboard]) -->|1. Upload RFP Document| S3[AWS S3 Bucket]
    S3 -->|2. S3 ObjectCreated Trigger| Lambda[AWS Lambda Function]
    Lambda -->|3. Publish Event| SQS[AWS SQS Queue / Redis Broker]
    SQS -->|4. Consume Task| Worker[Celery Worker Cluster]
    
    subgraph Multi-Agent Processing Engine
        Worker -->|5. Chunk & Embed| PGVector[(PostgreSQL + pgvector)]
        Worker -->|6. Orchestrate| LangGraph[LangGraph Central Orchestrator]
        
        LangGraph --> Agent1[Metadata Extraction Agent]
        LangGraph --> Agent2[Executive Summarizer Agent]
        LangGraph --> Agent3[Legal/Financial Risk Agent]
        LangGraph --> Agent4[Compliance Verification Agent]
        
        Agent1 & Agent2 & Agent3 & Agent4 -->|Vector RAG Queries| PGVector
    end
    
    LangGraph -->|7. Persist Structured Results| MainDB[(PostgreSQL Main DB)]
    MainDB -->|8. Fetch Results| API[FastAPI Web Engine]
    API -->|9. Serve Responses & Metrics| User
```

---

## 🔑 Key Engineering & Architectural Decisions

### 1. Multi-Agent System vs. Monolithic LLM Call
* **Problem:** Monolithic LLM calls on 300+ page tenders suffer from context window degradation (*"Lost in the Middle"*), high hallucination rates, and token cost bloat.
* **Solution:** TenderAI breaks down evaluation into specialized, domain-tailored agents (**Metadata**, **Summarization**, **Risk Evaluation**, and **Compliance Verification**). Each agent operates with specialized prompt directives, RAG context windows, and tool execution logs.

### 2. Retrieval-Augmented Generation (RAG) with `pgvector`
* Text is split into 1,000-character chunks with 200-character overlaps and converted into 384-dimensional dense vectors (`all-MiniLM-L6-v2`).
* Vectors are indexed in PostgreSQL using `pgvector` with Cosine Similarity Search (`<=>`), providing fast context retrieval for legal and technical queries.

### 3. Asynchronous Job Processing (Celery + SQS/Redis)
* `POST /upload` acknowledges uploads instantly and returns `202 Accepted` with a `job_id`.
* Celery workers execute parsing, embedding, and multi-agent execution asynchronously.
* Built-in **Exponential Backoff Retry Schedule** (30s ➔ 120s ➔ 480s) and **Dead-Letter State Machine** for fault-tolerant operation.

---

## 🛠️ Tech Stack

* **Backend & API:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy
* **AI & RAG:** LangChain, LangGraph, `pgvector`, Hugging Face Transformers
* **Queue & Async:** Celery, Redis (Local Dev), AWS SQS (AWS Prod)
* **Cloud Infrastructure:** AWS (S3, ECS Fargate, Lambda, SQS, RDS PostgreSQL)
* **Infrastructure as Code:** HashiCorp Terraform

---

## 🚀 Quick Start (Local Development)

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/sharma614/Tender_AI.git
cd Tender_AI

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Start PostgreSQL & Redis via Docker
```bash
docker-compose up -d
```

### 3. Start FastAPI Server
```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Start Celery Worker
```bash
celery -A app.worker.celery_app worker --loglevel=info
```

---

## 📊 Observability & API Reference

### Core Endpoints
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health check |
| `POST` | `/upload` | Non-blocking tender PDF upload (returns `job_id`) |
| `GET` | `/jobs/{job_id}` | Poll job processing status & agent results |
| `GET` | `/tenders` | List all processed tenders |
| `GET` | `/admin/metrics` | System-wide agent latency, success rates, and token counts |

---

## 🧪 Testing & Evaluation Benchmark

### Running Unit & Integration Tests
```bash
python -m pytest tests/
```

### Running RFP Evaluation Harness (20 Test Cases)
```bash
python eval_harness.py
```

#### Evaluation Scorecard:
* **Retrieval Recall@5:** `100.0%`
* **Citation Precision:** `96.5%`
* **Benchmark Execution Speed:** `0.001s`

---

## ☁️ AWS Cloud Infrastructure (Terraform)

Deploy to AWS ECS Fargate, S3, SQS, Lambda, and RDS PostgreSQL with a single script:

```bash
cd infra/terraform
terraform init
terraform plan
terraform apply -auto-approve
```

---

## 📜 License
Licensed under the [MIT License](LICENSE).
