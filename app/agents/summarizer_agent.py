import os
import json
import google.generativeai as genai
from ..schemas import TenderSummary

class SummarizerAgent:
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

    def generate_summary(self, raw_text: str, tender_id: int = None, db = None) -> TenderSummary:
        """
        Generates a rich executive summary, scope of work highlights, and key dates
        conforming to the TenderSummary Pydantic schema.
        """
        max_chars = 1500000
        truncated_text = raw_text[:max_chars]

        prompt = f"""
You are a Senior Proposal Specialist. Analyze this tender and provide a high-quality summary.

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "executive_summary": "<rich 2-3 paragraph overview of scope and objectives>",
  "scope_of_work": ["<key deliverable or milestone>", ...],
  "important_dates": {{
    "<event name>": "<date string>",
    ...
  }}
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
                    agent_name="SummarizerAgent",
                    tool_name="generate_summary",
                    input_arguments={"text_length": len(truncated_text)},
                    output_response=data
                )

            return TenderSummary(**data)

        except Exception as e:
            print(f"Error during summarization generation: {e}")
            raise RuntimeError(f"SummarizerAgent failed: {str(e)}")
