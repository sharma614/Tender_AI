import os
import json
import google.generativeai as genai
from ..schemas import TenderDetails

class ExtractionAgent:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Warning: GEMINI_API_KEY environment variable not found. LLM calls will fail unless configured.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            )
        )

    def extract_details(self, raw_text: str, tender_id: int = None, db = None) -> TenderDetails:
        """
        Sends the extracted tender text to Gemini to extract structured eligibility,
        deadlines, and financial requirements conforming to the Pydantic schema.
        """
        # Truncate text only if it's excessively large (e.g., > 1,500,000 characters)
        # Gemini 2.5/3.5 Flash handles up to 1M+ tokens, so full text is typically safe.
        max_chars = 1500000
        truncated_text = raw_text[:max_chars]
        
        prompt = f"""
You are an expert Tender Analyst Agent. Analyze the following tender document and extract all relevant information.
Be precise. If a detail is not explicitly mentioned, use null for strings or an empty array for lists.

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "tender_title": "<string>",
  "submission_deadline": "<string or null>",
  "eligibility_criteria": ["<string>", ...],
  "financial_requirements": {{
    "minimum_turnover": "<string or null>",
    "earnest_money_deposit_emd": "<string or null>",
    "solvency_certificate_required": <true|false>,
    "additional_financial_conditions": ["<string>", ...]
  }},
  "overall_summary": "<string>"
}}

Tender Document Text:
{truncated_text}
"""

        try:
            response = self.model.generate_content(prompt)
            data = json.loads(response.text)
            
            # Log tool call to database if session is provided
            if db and tender_id:
                from ..database import log_tool_call
                log_tool_call(
                    db=db,
                    tender_id=tender_id,
                    agent_name="ExtractionAgent",
                    tool_name="extract_tender_details",
                    input_arguments={"text_length": len(truncated_text)},
                    output_response=data
                )
                
            return TenderDetails(**data)
            
        except Exception as e:
            # Fallback to general generation or raise
            print(f"Error during structured generation: {e}")
            raise RuntimeError(f"Failed to extract structured details from LLM: {str(e)}")

