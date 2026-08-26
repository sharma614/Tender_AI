import os
import json
import google.generativeai as genai
from ..schemas import ComplianceAssessment, TenderDetails, CompanyProfile

class ComplianceAgent:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            )
        )

    def evaluate_compliance(
        self,
        extracted_facts: TenderDetails,
        company_profile: CompanyProfile,
        tender_id: int = None,
        db = None
    ) -> ComplianceAssessment:
        """
        Compares extracted tender requirements against the bidder company's profile
        and returns a structured ComplianceAssessment.
        """
        prompt = f"""
You are an expert Bid Manager evaluating whether a company can apply for a tender.

Tender Requirements:
- Title: {extracted_facts.tender_title}
- Eligibility Criteria: {extracted_facts.eligibility_criteria}
- Submission Deadline: {extracted_facts.submission_deadline}
- Minimum Turnover Required: {extracted_facts.financial_requirements.minimum_turnover}
- Earnest Money Deposit (EMD): {extracted_facts.financial_requirements.earnest_money_deposit_emd}
- Solvency Certificate Required: {extracted_facts.financial_requirements.solvency_certificate_required}
- Additional Financial Conditions: {extracted_facts.financial_requirements.additional_financial_conditions}

Bidder Company Profile:
- Company Name: {company_profile.company_name}
- Annual Turnover: ${company_profile.annual_turnover:,.2f}
- Years of Experience: {company_profile.years_of_experience} years
- Solvency Certificate Available: {company_profile.has_solvency_certificate}
- Technical Capabilities: {company_profile.technical_capabilities}

Perform a careful item-by-item comparison. overall_compliant should be true only if the bidder
meets all mandatory thresholds (turnover, experience, solvency).

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "overall_compliant": <true|false>,
  "checklist": [
    {{
      "requirement_name": "<criteria name>",
      "tender_value": "<what the tender demands>",
      "company_value": "<what the company has>",
      "status": "<Compliant|Non-Compliant|Partial>",
      "reason": "<reasoning for this rating>"
    }},
    ...
  ]
}}
"""

        try:
            response = self.model.generate_content(prompt)
            data = json.loads(response.text)

            if db and tender_id:
                from ..database import log_tool_call
                log_tool_call(
                    db=db,
                    tender_id=tender_id,
                    agent_name="ComplianceAgent",
                    tool_name="evaluate_compliance",
                    input_arguments={
                        "company_name": company_profile.company_name,
                        "tender_id": tender_id
                    },
                    output_response=data
                )

            return ComplianceAssessment(**data)

        except Exception as e:
            print(f"Error during compliance evaluation: {e}")
            raise RuntimeError(f"ComplianceAgent failed: {str(e)}")
