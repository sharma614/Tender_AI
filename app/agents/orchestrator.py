from sqlalchemy.orm import Session
from ..schemas import AgentState, CompanyProfile, TenderDetails
from ..models import Tender

from .extraction import ExtractionAgent
from .summarizer_agent import SummarizerAgent
from .risk_agent import RiskAgent
from .compliance_agent import ComplianceAgent

# Fictional company profile used as default for compliance matching
DEFAULT_COMPANY_PROFILE = CompanyProfile(
    company_name="AeroCorp Solutions Ltd",
    annual_turnover=5000000.0,       # $5M
    years_of_experience=5,            # 5 years
    has_solvency_certificate=True,
    technical_capabilities=[
        "Information Technology Solutions",
        "Cloud Migration Services",
        "Enterprise Architecture",
        "Cybersecurity Operations"
    ]
)

class AgentOrchestrator:
    def __init__(self):
        self.extraction_agent = ExtractionAgent()
        self.summarizer_agent = SummarizerAgent()
        self.risk_agent = RiskAgent()
        self.compliance_agent = ComplianceAgent()

    def run_analysis(
        self, 
        tender_id: int, 
        raw_text: str, 
        db: Session, 
        company_profile: CompanyProfile = None
    ) -> AgentState:
        """
        Orchestrates the execution of multiple agents: Fact Extraction,
        Summarization, Risk Analysis, and Compliance matching against a company profile.
        Passes a shared AgentState object between steps, logging operations to tool_calls,
        and saves intermediate state to the Tender record.
        """
        # If no profile provided, fall back to the default fictional company profile
        if not company_profile:
            company_profile = DEFAULT_COMPANY_PROFILE

        # Initialize the shared AgentState
        state = AgentState(
            tender_id=tender_id,
            raw_text=raw_text,
            company_profile=company_profile
        )

        print(f"--- Starting Orchestrated Analysis for Tender ID: {tender_id} ---")

        # Step 1: Extract Key Facts
        print("Running Extraction Agent...")
        state.extracted_facts = self.extraction_agent.extract_details(
            raw_text=state.raw_text, 
            tender_id=state.tender_id, 
            db=db
        )

        # Step 2: Generate Summary
        print("Running Summarization Agent...")
        state.summary = self.summarizer_agent.generate_summary(
            raw_text=state.raw_text, 
            tender_id=state.tender_id, 
            db=db
        )

        # Step 3: Assess Risks
        print("Running Risk Agent...")
        state.risk_analysis = self.risk_agent.evaluate_risks(
            raw_text=state.raw_text, 
            tender_id=state.tender_id, 
            db=db
        )

        # Step 4: Evaluate Compliance
        print("Running Compliance Agent...")
        state.compliance_assessment = self.compliance_agent.evaluate_compliance(
            extracted_facts=state.extracted_facts,
            company_profile=state.company_profile,
            tender_id=state.tender_id,
            db=db
        )

        # Persist structured analysis outputs back to the Tender record in PostgreSQL
        try:
            db_tender = db.query(Tender).filter(Tender.id == tender_id).first()
            if db_tender:
                db_tender.summary = state.summary.model_dump()
                db_tender.risks = state.risk_analysis.model_dump()
                db_tender.compliance_status = state.compliance_assessment.model_dump()
                db.commit()
                print("Analysis results successfully persisted to the database.")
        except Exception as e:
            db.rollback()
            print(f"Error persisting analysis results to database: {e}")

        print("--- Orchestration Complete ---")
        return state
