"""
app/agents/monolithic.py — Single-call baseline agent.

One Gemini call, full document, all four analysis outputs in a single JSON
response. This is the control arm for the ablation grid.

Why this matters
----------------
The README claims multi-agent avoids "lost in the middle" degradation and
reduces hallucination on long tenders. That claim is untested without a
baseline. MonolithicAgent provides it: if multi-agent + RAG is not
demonstrably better than this, the architectural complexity is unjustified.

What it does NOT do
-------------------
- It does not call pgvector. The whole document goes into one prompt.
- It does not split concerns by agent. All four tasks are in one instruction.
- It does not emit citations. There are no chunk_ids to reference.

The ablation harness measures both arms on the same 18-document corpus with
identical evaluation metrics, so the comparison is apples-to-apples.
"""
from typing import Tuple

from .base import BaseAgent
from ..schemas import CompanyProfile, MonolithicOutput, ToolCallRecord

# Default company profile used for the compliance question — same as orchestrator
_DEFAULT_PROFILE = CompanyProfile(
    company_name="AeroCorp Solutions Ltd",
    annual_turnover=5_000_000.0,
    years_of_experience=5,
    has_solvency_certificate=True,
    technical_capabilities=[
        "Information Technology Solutions",
        "Cloud Migration Services",
        "Enterprise Architecture",
        "Cybersecurity Operations",
    ],
)


class MonolithicAgent(BaseAgent):
    """
    Baseline: one Gemini call, full document, four outputs.

    Token count from this agent is the denominator of the "RAG shrinks prompts
    ~20×" claim — the harness reports both so the ratio is measured, not assumed.
    """

    agent_name = "MonolithicAgent"

    def __init__(self):
        super().__init__(temperature=0.1)

    def analyze(
        self,
        raw_text: str,
        company_profile: CompanyProfile = None,
    ) -> Tuple[MonolithicOutput, ToolCallRecord]:
        """
        Run a single combined analysis call over the full document.

        Returns (MonolithicOutput, ToolCallRecord).
        Raises AgentError (with its record) on transport or parse failure.
        """
        profile = company_profile or _DEFAULT_PROFILE
        text = self._bound(raw_text)

        prompt = f"""You are a senior tender analyst. Read the tender document below and \
produce ALL of the following in a single JSON response:

1. Key extracted facts (title, deadline, eligibility, financial requirements).
2. Top risk factors (up to 5).
3. A 2-3 paragraph executive summary.
4. Whether the following company profile meets the tender's eligibility:
   Company: {profile.company_name}
   Turnover: ${profile.annual_turnover:,.0f}
   Experience: {profile.years_of_experience} years
   Solvency certificate: {profile.has_solvency_certificate}
   Capabilities: {', '.join(profile.technical_capabilities)}

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "tender_title": "<string>",
  "submission_deadline": "<string or null>",
  "eligibility_criteria": ["<string>", ...],
  "minimum_turnover": "<string or null>",
  "earnest_money_deposit_emd": "<string or null>",
  "overall_risk_rating": "<Low|Medium|High>",
  "top_risks": ["<risk item string>", ...],
  "executive_summary": "<2-3 paragraph summary>",
  "overall_compliant": <true|false>
}}

Tender Document:
{text}
"""

        return self._invoke(
            prompt=prompt,
            response_model=MonolithicOutput,
            tool_name="analyze_tender_monolithic",
            input_arguments={
                "mode": "mono-full",
                "prompt_chars": len(text),
            },
        )
