"""
app/services/bid_decision_engine.py — Calibrated Bid/No-Bid Decision Engine.

Engineers an explicit, auditable Bid/No-Bid score and recommendation based on:
1. Compliance assessment results (pass/fail rate).
2. Risk assessment ratings and severity penalties.
3. Technical capability alignment between CompanyProfile and TenderDetails.
4. Calibrated confidence metric computed from grounding citation verification and data completeness.
"""
from typing import Dict, List, Optional, Tuple, Any
from app.schemas import (
    AgentState,
    BidDecision,
    CompanyProfile,
    ComplianceAssessment,
    RiskAnalysis,
    TenderDetails,
    DEFAULT_COMPANY_PROFILE,
)


def calculate_calibrated_confidence(
    extracted_facts: Optional[TenderDetails],
    risk_analysis: Optional[RiskAnalysis],
    compliance_assessment: Optional[ComplianceAssessment],
    retrieved_chunks_count: int = 0,
) -> float:
    """
    Calculate a calibrated confidence score between 0.0 and 1.0.

    Confidence is grounded in empirical observations:
    - Extraction completeness (are core required fields present?).
    - Citation grounding (ratio of citation refs with valid chunk attributions).
    - Risk analysis quality (are risks identified with severity ratings?).
    - Compliance clarity (were deterministic checks runnable?).
    """
    factors: List[float] = []

    # 1. Extraction Completeness (weight 30%)
    if extracted_facts:
        ext_score = 0.0
        if extracted_facts.tender_title:
            ext_score += 0.2
        if extracted_facts.submission_deadline:
            ext_score += 0.2
        if extracted_facts.financial_requirements and extracted_facts.financial_requirements.minimum_turnover:
            ext_score += 0.2
        if extracted_facts.financial_requirements and extracted_facts.financial_requirements.earnest_money_deposit_emd:
            ext_score += 0.2
        if extracted_facts.eligibility_criteria:
            ext_score += 0.2
        factors.append(ext_score)
    else:
        factors.append(0.0)

    # 2. Grounding Ratio (weight 30%)
    if extracted_facts and extracted_facts.field_citations:
        total_cites = len(extracted_facts.field_citations)
        grounded_cites = sum(1 for c in extracted_facts.field_citations if c.chunk_id != -1)
        factors.append(grounded_cites / total_cites if total_cites > 0 else 0.5)
    else:
        # Full-text mode or no citations requested
        factors.append(0.75 if extracted_facts else 0.0)

    # 3. Compliance Assessment Clarity (weight 20%)
    if compliance_assessment and compliance_assessment.checklist:
        factors.append(1.0)
    else:
        factors.append(0.3)

    # 4. Risk Depth (weight 20%)
    if risk_analysis and risk_analysis.identified_risks:
        factors.append(1.0)
    elif risk_analysis:
        factors.append(0.7)
    else:
        factors.append(0.2)

    weights = [0.30, 0.30, 0.20, 0.20]
    confidence = sum(f * w for f, w in zip(factors, weights))
    return round(max(0.0, min(1.0, confidence)), 3)


def evaluate_bid_decision(
    state: AgentState,
    company_profile: Optional[CompanyProfile] = None,
) -> BidDecision:
    """
    Evaluates the complete AgentState and returns a calibrated BidDecision.
    """
    profile = company_profile or state.company_profile or DEFAULT_COMPANY_PROFILE
    facts = state.extracted_facts
    risks = state.risk_analysis
    compliance = state.compliance_assessment

    key_factors: List[str] = []

    # 1. Compliance Score (0-100)
    compliance_score = 100.0
    overall_compliant = True
    if compliance and compliance.checklist:
        total_items = len(compliance.checklist)
        compliant_items = sum(1 for item in compliance.checklist if item.status == "Compliant")
        partial_items = sum(1 for item in compliance.checklist if item.status == "Partial")
        compliance_score = ((compliant_items + 0.5 * partial_items) / total_items) * 100.0
        overall_compliant = compliance.overall_compliant

        if overall_compliant:
            key_factors.append("Passes mandatory compliance benchmarks.")
        else:
            non_comp = [i.requirement_name for i in compliance.checklist if i.status == "Non-Compliant"]
            key_factors.append(f"Failed compliance check(s): {', '.join(non_comp)}.")
    elif compliance:
        overall_compliant = compliance.overall_compliant
        compliance_score = 100.0 if overall_compliant else 0.0
    else:
        compliance_score = 50.0
        key_factors.append("Compliance assessment was not conducted.")

    # 2. Risk Penalty (0-100)
    risk_penalty = 0.0
    if risks and risks.identified_risks:
        high_count = sum(1 for r in risks.identified_risks if r.severity.lower() == "high")
        med_count = sum(1 for r in risks.identified_risks if r.severity.lower() == "medium")
        low_count = sum(1 for r in risks.identified_risks if r.severity.lower() == "low")

        risk_penalty = min(100.0, (high_count * 25.0) + (med_count * 10.0) + (low_count * 3.0))

        if high_count > 0:
            key_factors.append(f"Identified {high_count} High-severity risk item(s).")
        if med_count > 0:
            key_factors.append(f"Identified {med_count} Medium-severity risk item(s).")
    elif risks:
        key_factors.append(f"Overall risk rating: {risks.overall_risk_rating}.")
        if risks.overall_risk_rating.lower() == "high":
            risk_penalty = 50.0

    # 3. Technical Fit Score (0-100)
    technical_fit_score = 70.0  # baseline assumption
    if profile.technical_capabilities and facts and facts.eligibility_criteria:
        matched = 0
        profile_caps = [c.lower() for c in profile.technical_capabilities]
        for crit in facts.eligibility_criteria:
            if any(cap in crit.lower() for cap in profile_caps):
                matched += 1
        if facts.eligibility_criteria:
            match_ratio = matched / len(facts.eligibility_criteria)
            technical_fit_score = round(50.0 + (match_ratio * 50.0), 1)
            if matched > 0:
                key_factors.append(f"Technical capabilities match {matched}/{len(facts.eligibility_criteria)} eligibility criteria.")

    # 4. Composite Score Calculation
    # Formula: (Compliance * 0.50) + (TechnicalFit * 0.30) + (Max(0, 100 - RiskPenalty) * 0.20)
    risk_subscore = max(0.0, 100.0 - risk_penalty)
    composite_score = round(
        (compliance_score * 0.50) + (technical_fit_score * 0.30) + (risk_subscore * 0.20),
        1
    )

    # 5. Recommendation Logic
    if not overall_compliant:
        recommendation = "NO_BID"
        explanation = (
            f"NO_BID recommended due to failing mandatory compliance requirements. "
            f"Composite score: {composite_score}/100."
        )
    elif composite_score >= 70.0 and risk_penalty < 40.0:
        recommendation = "GO_BID"
        explanation = (
            f"GO_BID recommended. Strong alignment with company profile, mandatory compliance met, "
            f"and manageable risk penalty ({risk_penalty:.0f} pts). Composite score: {composite_score}/100."
        )
    elif composite_score >= 45.0:
        recommendation = "CONDITIONAL_BID"
        explanation = (
            f"CONDITIONAL_BID recommended. Bidder satisfies basic compliance but requires risk mitigation "
            f"or further review due to composite score of {composite_score}/100 (Risk penalty: {risk_penalty:.0f} pts)."
        )
    else:
        recommendation = "NO_BID"
        explanation = (
            f"NO_BID recommended due to poor composite score ({composite_score}/100) or excessive risk penalty ({risk_penalty:.0f} pts)."
        )

    # 6. Calibrated Confidence
    confidence = calculate_calibrated_confidence(
        extracted_facts=facts,
        risk_analysis=risks,
        compliance_assessment=compliance,
        retrieved_chunks_count=len(state.extraction_chunks) + len(state.risk_chunks),
    )

    return BidDecision(
        recommendation=recommendation,
        composite_score=composite_score,
        calibrated_confidence=confidence,
        compliance_score=round(compliance_score, 1),
        risk_penalty=round(risk_penalty, 1),
        technical_fit_score=round(technical_fit_score, 1),
        key_decision_factors=key_factors,
        explanation=explanation,
    )
