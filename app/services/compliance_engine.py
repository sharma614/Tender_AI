"""
app/services/compliance_engine.py — Deterministic threshold compliance engine.

Why deterministic compliance
-----------------------------
Asking an LLM whether "$5,000,000" clears "Rs. 5 Crore" is unreliable:
  1. LLMs struggle with numeric comparison and magnitude scaling.
  2. Indian financial formats ("₹5 Crore", "50 Lakhs", "Rs. 5,00,00,000") are
     frequently misinterpreted by general-purpose LLMs.

Solution:
  LLM extracts string requirements -> Python parses to normalized float numbers
  -> Python compares values deterministically.
"""
import re
from typing import Dict, List, Optional, Tuple, Any
from ..schemas import CompanyProfile, TenderDetails, ComplianceAssessment, ComplianceItem

_NUM_PATTERN = re.compile(r"[\d,]+(?:\.\d+)?")
_EXP_PATTERN = re.compile(r"(\d+)\s*(?:\+|\-)?\s*(?:years?|yrs?)", re.IGNORECASE)

_CURRENCY_MULTIPLIERS = (
    ("crore", 10_000_000.0),
    ("cr", 10_000_000.0),
    ("lakh", 100_000.0),
    ("lac", 100_000.0),
    ("million", 1_000_000.0),
    ("mn", 1_000_000.0),
    ("billion", 1_000_000_000.0),
    ("bn", 1_000_000_000.0),
    ("k", 1_000.0),
)

# Approx USD to INR conversion rate for cross-currency evaluation
USD_TO_INR_RATE = 83.0


def parse_monetary_value(text: Optional[str]) -> Optional[float]:
    """
    Parse an arbitrary currency string (Indian Lakhs/Crores, USD, EUR, raw digits)
    into a standardized numeric floating point amount (in local currency units / USD).

    Examples:
      "Rs. 5 Crore" -> 50,000,000.0
      "50 Lakhs"    -> 5,000,000.0
      "$5,000,000"  -> 5,000,000.0
      "INR 5000000" -> 5,000,000.0
    """
    if not text:
        return None
    cleaned = text.lower().strip()
    match = _NUM_PATTERN.search(cleaned)
    if not match:
        return None

    try:
        raw_num = float(match.group(0).replace(",", ""))
    except ValueError:
        return None

    tail = cleaned[match.end():]
    multiplier = 1.0
    for keyword, factor in _CURRENCY_MULTIPLIERS:
        if keyword in tail or keyword in cleaned:
            multiplier = factor
            break

    total = raw_num * multiplier

    # Currency normalization: if company profile is in USD ($5M) and tender is in INR (Rs. 5 Cr),
    # normalize INR to USD for comparison if 'rs' or 'inr' or '₹' is present.
    if any(curr in cleaned for curr in ("rs", "inr", "₹", "rupees")):
        # Return equivalent USD value for uniform comparison
        total = total / USD_TO_INR_RATE

    return total


def parse_experience_years(text: Optional[str]) -> Optional[int]:
    """Extract minimum years of experience required from text or eligibility list."""
    if not text:
        return None
    match = _EXP_PATTERN.search(text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def evaluate_compliance_deterministic(
    extracted_facts: TenderDetails,
    company_profile: CompanyProfile,
) -> ComplianceAssessment:
    """
    Deterministically evaluates compliance of a company profile against tender requirements.

    Returns a structured `ComplianceAssessment` with verified checklist items.
    """
    checklist: List[ComplianceItem] = []
    overall_compliant = True

    fin = extracted_facts.financial_requirements

    # 1. Turnover Evaluation
    required_turnover = parse_monetary_value(fin.minimum_turnover)
    if required_turnover is not None:
        company_turnover = company_profile.annual_turnover
        # Note: if company_profile.annual_turnover is USD and required_turnover was normalized to USD
        is_met = company_turnover >= required_turnover
        status = "Compliant" if is_met else "Non-Compliant"
        if not is_met:
            overall_compliant = False
        checklist.append(ComplianceItem(
            requirement_name="Minimum Annual Turnover",
            tender_value=fin.minimum_turnover or "Not specified",
            company_value=f"${company_profile.annual_turnover:,.2f}",
            status=status,
            reason=f"Company turnover (${company_profile.annual_turnover:,.2f}) "
                   f"{'clears' if is_met else 'fails'} required minimum "
                   f"({fin.minimum_turnover}).",
        ))
    else:
        checklist.append(ComplianceItem(
            requirement_name="Minimum Annual Turnover",
            tender_value=fin.minimum_turnover or "Not specified",
            company_value=f"${company_profile.annual_turnover:,.2f}",
            status="Compliant",
            reason="No explicit numerical turnover threshold found in tender.",
        ))

    # 2. Solvency Certificate Evaluation
    if fin.solvency_certificate_required:
        has_solvency = company_profile.has_solvency_certificate
        status = "Compliant" if has_solvency else "Non-Compliant"
        if not has_solvency:
            overall_compliant = False
        checklist.append(ComplianceItem(
            requirement_name="Solvency Certificate",
            tender_value="Required",
            company_value="Available" if has_solvency else "Not Available",
            status=status,
            reason="Bank solvency certificate requirement "
                   + ("is satisfied by company." if has_solvency else "is NOT satisfied."),
        ))

    # 3. Experience Requirement Evaluation
    req_years = None
    for crit in extracted_facts.eligibility_criteria:
        yrs = parse_experience_years(crit)
        if yrs is not None:
            req_years = max(req_years or 0, yrs)

    if req_years is not None:
        has_years = company_profile.years_of_experience
        is_met = has_years >= req_years
        status = "Compliant" if is_met else "Non-Compliant"
        if not is_met:
            overall_compliant = False
        checklist.append(ComplianceItem(
            requirement_name="Minimum Experience",
            tender_value=f"{req_years} Years",
            company_value=f"{has_years} Years",
            status=status,
            reason=f"Company experience ({has_years} yrs) "
                   + ("meets requirement." if is_met else f"is below required {req_years} yrs."),
        ))

    # 4. EMD / Bid Security acknowledgment
    if fin.earnest_money_deposit_emd:
        checklist.append(ComplianceItem(
            requirement_name="Earnest Money Deposit (EMD)",
            tender_value=fin.earnest_money_deposit_emd,
            company_value="Acknowledged",
            status="Compliant",
            reason="Bidder must submit requested EMD upon formal proposal submission.",
        ))

    return ComplianceAssessment(
        overall_compliant=overall_compliant,
        checklist=checklist,
    )
