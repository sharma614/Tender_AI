from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime

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

# --- Fictional Company Profile for Compliance Matching ---
class CompanyProfile(BaseModel):
    company_name: str = Field(..., description="Name of the company bidding.")
    annual_turnover: float = Field(..., description="Company's annual turnover in USD/preferred currency.")
    years_of_experience: int = Field(..., description="Number of years operating in the target field.")
    has_solvency_certificate: bool = Field(False, description="Does the company have a bank solvency certificate?")
    technical_capabilities: List[str] = Field(default_factory=list, description="List of technical specialities or certifications.")

# --- Risk Agent Schema ---
class RiskPoint(BaseModel):
    risk_item: str = Field(..., description="Short hazard title, e.g., 'Aggressive Timelines', 'Liquidated Damages'.")
    description: str = Field(..., description="Detailed description of the operational or financial risk.")
    severity: str = Field(..., description="Risk severity level: Low, Medium, or High.")
    mitigation_strategy: str = Field(..., description="Suggested approach to mitigate this risk.")

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

# --- AgentState (Shared State Object) ---
class AgentState(BaseModel):
    tender_id: int = Field(..., description="ID of the processed Tender record.")
    raw_text: str = Field(..., description="Raw text of the extracted tender document.")
    company_profile: Optional[CompanyProfile] = Field(None, description="Bidder company profile data.")
    
    # State accumulated by agents during orchestration
    extracted_facts: Optional[TenderDetails] = Field(None, description="Extracted basic details.")
    summary: Optional[TenderSummary] = Field(None, description="Generated executive summary.")
    risk_analysis: Optional[RiskAnalysis] = Field(None, description="Identified risk points.")
    compliance_assessment: Optional[ComplianceAssessment] = Field(None, description="Final compliance audit results.")

# --- FastAPI Response Schemas ---
class TenderBase(BaseModel):
    title: str

class TenderResponse(TenderBase):
    id: int
    summary: Optional[Dict] = None
    risks: Optional[Dict] = None
    compliance_status: Optional[Dict] = None
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

