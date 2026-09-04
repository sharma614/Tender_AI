from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any, Annotated
from datetime import datetime
import operator

# --- Citation reference (chunk_id + page from pgvector retrieval) ---
class CitationRef(BaseModel):
    """
    Points a field or risk item at the chunk it was extracted from.

    Grounding verification: the harness does `field_value ⊆ chunk.text_content`
    (deterministic substring containment, not an LLM judge). A citation whose
    value does not appear in the referenced chunk counts as a hallucination.
    chunk_id == -1 is the sentinel the LLM should use when it cannot attribute
    a value to any retrieved chunk (i.e. it is fabricating).
    """
    chunk_id: int = Field(
        -1,
        description="Primary key of the TenderChunk this value was extracted from. -1 = no attribution.",
    )
    page_number: Optional[int] = Field(
        None, description="Source page in the original PDF, copied from the chunk's page_number column."
    )


# --- Existing Extracted Tender Facts ---
class FinancialRequirements(BaseModel):
    minimum_turnover: Optional[str] = Field(
        None, description="Minimum annual turnover required from the bidder."
    )
    earnest_money_deposit_emd: Optional[str] = Field(
        None, description="Earnest Money Deposit (EMD) or Bid Security details."
    )
    solvency_certificate_required: bool = Field(
        False, description="Is a bank solvency certificate required?"
    )
    additional_financial_conditions: List[str] = Field(
        default_factory=list, description="Any other financial guarantees or conditions."
    )
    citations: List[CitationRef] = Field(
        default_factory=list,
        description="Source chunks for each financial field, in field order.",
    )

class TenderDetails(BaseModel):
    tender_title: str = Field(..., description="Official title or identifier of the tender.")
    submission_deadline: Optional[str] = Field(
        None, description="The final submission deadline date and time."
    )
    eligibility_criteria: List[str] = Field(
        default_factory=list, description="Key technical, experience, or administrative requirements for bidders."
    )
    financial_requirements: FinancialRequirements = Field(
        ..., description="The financial benchmarks and guarantees required."
    )
    overall_summary: str = Field(
        ..., description="A 2-3 sentence overview of the tender's scope of work."
    )
    field_citations: List[CitationRef] = Field(
        default_factory=list,
        description=(
            "One citation per top-level field in order: tender_title, "
            "submission_deadline, overall_summary. chunk_id=-1 when full-text mode."
        ),
    )

# --- Observability: per-LLM-call instrumentation ---
class ToolCallRecord(BaseModel):
    """
    Instrumentation for a single agent LLM invocation, produced by the agent and
    persisted by the orchestrator *after* the graph completes.

    Agents deliberately do not hold a DB Session: extraction, summarization and
    risk run as concurrent LangGraph nodes, and SQLAlchemy Sessions are not
    thread-safe. Returning a record instead of writing one keeps persistence
    single-threaded in the `persist` node.
    """
    agent_name: str = Field(..., description="Agent that made the call, e.g. 'RiskAgent'.")
    tool_name: str = Field(..., description="Logical operation, e.g. 'evaluate_risks'.")
    input_arguments: Dict[str, Any] = Field(default_factory=dict, description="Non-sensitive call inputs.")
    output_response: Optional[Any] = Field(None, description="Parsed model output, if the call succeeded.")

    model_name: Optional[str] = Field(None, description="LLM model identifier used for the call.")
    latency_ms: Optional[int] = Field(None, description="Wall-clock duration of the LLM call.")
    prompt_tokens: Optional[int] = Field(None, description="Input tokens reported by the provider.")
    completion_tokens: Optional[int] = Field(None, description="Output tokens reported by the provider.")
    total_tokens: Optional[int] = Field(None, description="Total tokens reported by the provider.")
    status: str = Field("success", description="'success' or 'error'.")
    error_message: Optional[str] = Field(None, description="Exception detail when status == 'error'.")


# --- Retrieval ---
class RetrievedChunk(BaseModel):
    """A single chunk returned by vector similarity search."""
    chunk_id: int = Field(..., description="Primary key of the TenderChunk row.")
    chunk_index: int = Field(..., description="Ordinal position of the chunk within the document.")
    text: str = Field(..., description="Chunk text content.")
    distance: float = Field(..., description="Cosine distance to the query (lower is closer).")
    page_number: Optional[int] = Field(None, description="Source page in the original PDF, if known.")
    char_start: Optional[int] = Field(None, description="Offset of the chunk into the document text.")

    @property
    def similarity(self) -> float:
        """Cosine similarity in [-1, 1], derived from the pgvector cosine distance."""
        return 1.0 - self.distance


# --- Fictional Company Profile for Compliance Matching ---
class CompanyProfile(BaseModel):
    company_name: str = Field(..., description="Name of the company bidding.")
    annual_turnover: float = Field(..., description="Company's annual turnover in USD/preferred currency.")
    years_of_experience: int = Field(..., description="Number of years operating in the target field.")
    has_solvency_certificate: bool = Field(False, description="Does the company have a bank solvency certificate?")
    technical_capabilities: List[str] = Field(default_factory=list, description="List of technical specialities or certifications.")


DEFAULT_COMPANY_PROFILE = CompanyProfile(
    company_name="Acme Tech Solutions",
    annual_turnover=15000000.0,
    years_of_experience=8,
    has_solvency_certificate=True,
    technical_capabilities=["Cloud Infrastructure", "AI & Analytics", "Cybersecurity Operations"]
)


# --- Risk Agent Schema ---
class RiskPoint(BaseModel):
    risk_item: str = Field(..., description="Short hazard title, e.g., 'Aggressive Timelines', 'Liquidated Damages'.")
    description: str = Field(..., description="Detailed description of the operational or financial risk.")
    severity: str = Field(..., description="Risk severity level: Low, Medium, or High.")
    mitigation_strategy: str = Field(..., description="Suggested approach to mitigate this risk.")
    citation: CitationRef = Field(
        default_factory=CitationRef,
        description="Source chunk this risk was identified in. chunk_id=-1 when full-text mode.",
    )

class RiskAnalysis(BaseModel):
    overall_risk_rating: str = Field(..., description="Overall risk rating of this tender (Low, Medium, High).")
    identified_risks: List[RiskPoint] = Field(default_factory=list, description="List of identified hazards.")

# --- Compliance Agent Schema ---
class ComplianceItem(BaseModel):
    requirement_name: str = Field(..., description="Name of the criteria evaluated, e.g., 'Turnover Requirement'.")
    tender_value: str = Field(..., description="The required specification specified in the tender document.")
    company_value: str = Field(..., description="The corresponding value from the company profile.")
    status: str = Field(..., description="Evaluation status: Compliant, Non-Compliant, or Partial.")
    reason: str = Field(..., description="Reasoning or details explaining the compliance rating.")

class ComplianceAssessment(BaseModel):
    overall_compliant: bool = Field(..., description="Is the bidder company eligible to apply for this tender?")
    checklist: List[ComplianceItem] = Field(default_factory=list, description="Detailed item-by-item compliance analysis.")

# --- Summarization Agent Schema ---
class TenderSummary(BaseModel):
    executive_summary: str = Field(..., description="A rich 2-3 paragraph executive summary of the tender scope and objectives.")
    scope_of_work: List[str] = Field(default_factory=list, description="Key deliverables and tasks.")
    important_dates: Dict[str, str] = Field(
        default_factory=dict,
        description="Dictionary of critical milestones, e.g., {'Pre-bid meeting': '2026-09-01', 'Submission': '2026-10-01'}"
    )
    citations: List[CitationRef] = Field(
        default_factory=list,
        description="Source chunks that support the summary, in retrieval order.",
    )

# --- Monolithic baseline output (one LLM call, full document) ---
class MonolithicOutput(BaseModel):
    """
    Output of the single-call baseline agent.

    All four analysis outputs are requested in a single prompt so the baseline
    measures what a monolithic LLM call can do. This is the control arm in the
    ablation — if multi-agent + retrieval is not demonstrably better, the
    architectural complexity is not justified.
    """
    tender_title: str = Field(..., description="Official title of the tender.")
    submission_deadline: Optional[str] = Field(None)
    eligibility_criteria: List[str] = Field(default_factory=list)
    minimum_turnover: Optional[str] = Field(None)
    earnest_money_deposit_emd: Optional[str] = Field(None)
    overall_risk_rating: str = Field(..., description="Low | Medium | High")
    top_risks: List[str] = Field(
        default_factory=list, description="Up to 5 risk items as plain strings."
    )
    executive_summary: str = Field(..., description="2-3 paragraph executive summary.")
    overall_compliant: Optional[bool] = Field(
        None, description="Whether the default company profile is eligible."
    )


# --- AgentState (LangGraph state schema) ---
class AgentState(BaseModel):
    """
    Shared state for the LangGraph orchestration graph.

    `tool_calls` uses an `operator.add` reducer because the extraction,
    summarization and risk nodes run concurrently and each append their own
    record. Every other field is written by exactly one node, so no reducer is
    needed — concurrent writes to the same un-reduced key would raise
    InvalidUpdateError.
    """
    tender_id: int = Field(..., description="ID of the processed Tender record.")
    raw_text: str = Field(..., description="Raw text of the extracted tender document.")
    company_profile: Optional[CompanyProfile] = Field(None, description="Bidder company profile data.")

    # Run configuration — propagated through state so persist/logging can record it
    mode: Literal["full", "rag", "rag-cite"] = Field(
        "full",
        description=(
            "full = full-context prompts; "
            "rag  = retrieval-grounded prompts (no citation requests); "
            "rag-cite = retrieval + citation fields in schema."
        ),
    )

    # Retrieved chunks per agent — populated by orchestrator before node dispatch
    extraction_chunks: List[RetrievedChunk] = Field(default_factory=list)
    summary_chunks: List[RetrievedChunk] = Field(default_factory=list)
    risk_chunks: List[RetrievedChunk] = Field(default_factory=list)

    # State accumulated by agents during orchestration
    extracted_facts: Optional[TenderDetails] = Field(None, description="Extracted basic details.")
    summary: Optional[TenderSummary] = Field(None, description="Generated executive summary.")
    risk_analysis: Optional[RiskAnalysis] = Field(None, description="Identified risk points.")
    compliance_assessment: Optional[ComplianceAssessment] = Field(None, description="Final compliance audit results.")
    bid_decision: Optional["BidDecision"] = Field(None, description="Calibrated Bid/No-Bid decision & score.")

    # Instrumentation accumulated across all agent calls, persisted once at the end.
    tool_calls: Annotated[List[ToolCallRecord], operator.add] = Field(
        default_factory=list, description="Per-call latency/token/status records."
    )

    # Nodes are fail-soft so one agent's failure cannot discard its siblings'
    # results or their instrumentation. The orchestrator inspects this after the
    # graph completes and raises, which is what triggers the Celery retry path.
    errors: Annotated[List[str], operator.add] = Field(
        default_factory=list, description="Failure messages from agent nodes."
    )

# --- Bid/No-Bid Decision Engine Schema ---
class BidDecision(BaseModel):
    recommendation: Literal["GO_BID", "NO_BID", "CONDITIONAL_BID"] = Field(
        ..., description="Final Bid/No-Bid recommendation."
    )
    composite_score: float = Field(
        ..., description="Overall score from 0.0 to 100.0 considering compliance, risks, and technical fit."
    )
    calibrated_confidence: float = Field(
        ..., description="Calibrated confidence score from 0.0 to 1.0 based on citation grounding and extraction completeness."
    )
    compliance_score: float = Field(..., description="Compliance sub-score (0-100).")
    risk_penalty: float = Field(..., description="Subtracted risk penalty points (0-100).")
    technical_fit_score: float = Field(..., description="Technical alignment sub-score (0-100).")
    key_decision_factors: List[str] = Field(
        default_factory=list, description="Key drivers behind the Bid/No-Bid decision."
    )
    explanation: str = Field(..., description="Executive narrative explaining the recommendation.")

# --- FastAPI Response Schemas ---
class TenderBase(BaseModel):
    title: str

class TenderResponse(TenderBase):
    id: int
    summary: Optional[Dict] = None
    risks: Optional[Dict] = None
    compliance_status: Optional[Dict] = None
    bid_decision: Optional[Dict] = None
    created_at: datetime

    class Config:
        from_attributes = True

class OrchestrationResponse(BaseModel):
    status: str
    tender_id: int
    analysis: AgentState


# --- Async Job Queue Schemas ---
class JobEnqueuedResponse(BaseModel):
    """Returned immediately from POST /upload — tells the client the job_id to poll."""
    job_id: int
    status: str = "pending"
    message: str = "Job enqueued. Poll GET /jobs/{job_id} for status updates."

class JobStatusResponse(BaseModel):
    """Returned from GET /jobs/{job_id} — full job state including result when done."""
    job_id: int
    tender_id: Optional[int] = None
    celery_task_id: Optional[str] = None
    status: str                              # pending | processing | retrying | completed | dead_letter
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    result: Optional[Dict] = None            # Populated when status == "completed"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

