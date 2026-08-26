import os
import json
import google.generativeai as genai
from ..schemas import RiskAnalysis

class RiskAgent:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
            )
        )

    def evaluate_risks(self, raw_text: str, tender_id: int = None, db = None) -> RiskAnalysis:
        """
        Evaluates potential legal, operational, and financial risks from the tender text,
        returning a structured RiskAnalysis.
        """
        max_chars = 1500000
        truncated_text = raw_text[:max_chars]

        prompt = f"""
You are an expert Tender Risk Compliance Officer. Analyze this tender document and identify all risk factors.
Look specifically for:
- Severe penalty clauses, liquidated damages, or performance bank guarantees
- Aggressive delivery schedules or milestone timelines
- Strict payment terms, delays in reimbursement, or retention clauses
- Liability limits, indemnity clauses, and termination conditions

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "overall_risk_rating": "<Low|Medium|High>",
  "identified_risks": [
    {{
      "risk_item": "<short hazard title>",
      "description": "<detailed description of the risk>",
      "severity": "<Low|Medium|High>",
      "mitigation_strategy": "<concrete mitigation recommendation>"
    }},
    ...
  ]
}}

Tender Document Text:
{truncated_text}
"""

        try:
            response = self.model.generate_content(prompt)
            data = json.loads(response.text)

            if db and tender_id:
                from ..database import log_tool_call
                log_tool_call(
                    db=db,
                    tender_id=tender_id,
                    agent_name="RiskAgent",
                    tool_name="evaluate_risks",
                    input_arguments={"text_length": len(truncated_text)},
                    output_response=data
                )

            return RiskAnalysis(**data)

        except Exception as e:
            print(f"Error during risk evaluation: {e}")
            raise RuntimeError(f"RiskAgent failed: {str(e)}")
