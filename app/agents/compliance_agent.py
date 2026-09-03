import time
from typing import Tuple

from .base import BaseAgent
from ..schemas import ComplianceAssessment, CompanyProfile, TenderDetails, ToolCallRecord
from ..services.compliance_engine import evaluate_compliance_deterministic


class ComplianceAgent(BaseAgent):
    agent_name = "ComplianceAgent"

    def __init__(self):
        super().__init__(temperature=0.0)

    def evaluate_compliance(
        self,
        extracted_facts: TenderDetails,
        company_profile: CompanyProfile,
    ) -> Tuple[ComplianceAssessment, ToolCallRecord]:
        """
        Compare extracted tender requirements against the bidder's profile.

        Uses deterministic Python evaluation for numerical thresholds (turnover,
        experience, solvency) to ensure absolute correctness ("LLM extracts, code decides").
        """
        t0 = time.perf_counter()

        assessment = evaluate_compliance_deterministic(
            extracted_facts=extracted_facts,
            company_profile=company_profile,
        )

        latency_ms = int((time.perf_counter() - t0) * 1000)

        record = ToolCallRecord(
            agent_name=self.agent_name,
            tool_name="evaluate_compliance_deterministic",
            input_arguments={
                "company_name": company_profile.company_name,
                "mode": "deterministic_python",
            },
            output_response=assessment.model_dump(),
            model_name="python-rules-engine",
            latency_ms=latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            status="success",
        )

        return assessment, record
