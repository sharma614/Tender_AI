"""
tests/test_bid_scoring.py — Tests for the Bid/No-Bid Decision Engine and Calibrated Confidence.
"""
import pytest
from app.schemas import (
    AgentState,
    CompanyProfile,
    ComplianceAssessment,
    ComplianceItem,
    FinancialRequirements,
    RiskAnalysis,
    RiskPoint,
    TenderDetails,
    CitationRef,
    DEFAULT_COMPANY_PROFILE,
)
from app.services.bid_decision_engine import evaluate_bid_decision, calculate_calibrated_confidence


def test_bid_decision_go_bid():
    facts = TenderDetails(
        tender_title="Regional Cloud Modernization Tender",
        submission_deadline="2026-10-15",
        eligibility_criteria=["5 years experience in Cloud Infrastructure"],
        financial_requirements=FinancialRequirements(
            minimum_turnover="Rs. 5 Crore",
            earnest_money_deposit_emd="Rs. 2,00,000",
            solvency_certificate_required=True,
        ),
        overall_summary="Migration of enterprise applications to multi-cloud infrastructure.",
        field_citations=[CitationRef(chunk_id=1, page_number=1)],
    )

    risks = RiskAnalysis(
        overall_risk_rating="Low",
        identified_risks=[
            RiskPoint(
                risk_item="Aggressive Timeline",
                description="6 month migration window",
                severity="Low",
                mitigation_strategy="Parallel team deployment",
            )
        ],
    )

    compliance = ComplianceAssessment(
        overall_compliant=True,
        checklist=[
            ComplianceItem(
                requirement_name="Minimum Annual Turnover",
                tender_value="Rs. 5 Crore",
                company_value="$15,000,000.00",
                status="Compliant",
                reason="Turnover clears minimum.",
            ),
            ComplianceItem(
                requirement_name="Solvency Certificate",
                tender_value="Required",
                company_value="Available",
                status="Compliant",
                reason="Satisfied.",
            ),
        ],
    )

    state = AgentState(
        tender_id=101,
        raw_text="Sample text",
        extracted_facts=facts,
        risk_analysis=risks,
        compliance_assessment=compliance,
        company_profile=DEFAULT_COMPANY_PROFILE,
    )

    decision = evaluate_bid_decision(state)

    assert decision.recommendation == "GO_BID"
    assert decision.composite_score >= 70.0
    assert decision.calibrated_confidence > 0.6
    assert decision.compliance_score == 100.0
    assert decision.risk_penalty < 10.0


def test_bid_decision_no_bid_compliance_failure():
    facts = TenderDetails(
        tender_title="Mega Infrastructure Project",
        submission_deadline="2026-12-01",
        eligibility_criteria=["15 years experience required"],
        financial_requirements=FinancialRequirements(
            minimum_turnover="Rs. 500 Crore",
            solvency_certificate_required=True,
        ),
        overall_summary="Construction of heavy bridge.",
    )

    compliance = ComplianceAssessment(
        overall_compliant=False,
        checklist=[
            ComplianceItem(
                requirement_name="Minimum Annual Turnover",
                tender_value="Rs. 500 Crore",
                company_value="$15,000,000.00",
                status="Non-Compliant",
                reason="Turnover insufficient.",
            )
        ],
    )

    state = AgentState(
        tender_id=102,
        raw_text="Sample text",
        extracted_facts=facts,
        compliance_assessment=compliance,
        company_profile=DEFAULT_COMPANY_PROFILE,
    )

    decision = evaluate_bid_decision(state)

    assert decision.recommendation == "NO_BID"
    assert "failing mandatory compliance" in decision.explanation.lower()


def test_bid_decision_conditional_high_risk():
    facts = TenderDetails(
        tender_title="High Risk Defense Logistics",
        submission_deadline="2026-11-01",
        eligibility_criteria=["Cloud Infrastructure"],
        financial_requirements=FinancialRequirements(minimum_turnover="Rs. 2 Crore"),
        overall_summary="Logistics management.",
    )

    risks = RiskAnalysis(
        overall_risk_rating="High",
        identified_risks=[
            RiskPoint(
                risk_item="Liquidated Damages",
                description="20% penalty for 1-day delay",
                severity="High",
                mitigation_strategy="Unclear",
            ),
            RiskPoint(
                risk_item="Unlimited Indemnity",
                description="Bidder covers all third-party losses",
                severity="High",
                mitigation_strategy="Insurance",
            ),
        ],
    )

    compliance = ComplianceAssessment(
        overall_compliant=True,
        checklist=[
            ComplianceItem(
                requirement_name="Minimum Annual Turnover",
                tender_value="Rs. 2 Crore",
                company_value="$15,000,000.00",
                status="Compliant",
                reason="Clears threshold.",
            )
        ],
    )

    state = AgentState(
        tender_id=103,
        raw_text="Sample text",
        extracted_facts=facts,
        risk_analysis=risks,
        compliance_assessment=compliance,
        company_profile=DEFAULT_COMPANY_PROFILE,
    )

    decision = evaluate_bid_decision(state)

    assert decision.risk_penalty >= 50.0
    assert decision.recommendation in ("CONDITIONAL_BID", "NO_BID")


def test_calibrated_confidence_grounding():
    facts_grounded = TenderDetails(
        tender_title="Test Title",
        submission_deadline="2026-09-30",
        eligibility_criteria=["Cloud"],
        financial_requirements=FinancialRequirements(minimum_turnover="Rs 1 Cr"),
        overall_summary="Summary text",
        field_citations=[CitationRef(chunk_id=10, page_number=1)],
    )

    facts_ungrounded = TenderDetails(
        tender_title="Test Title",
        financial_requirements=FinancialRequirements(),
        overall_summary="Summary text",
        field_citations=[CitationRef(chunk_id=-1)],
    )

    conf_grounded = calculate_calibrated_confidence(
        extracted_facts=facts_grounded,
        risk_analysis=None,
        compliance_assessment=None,
    )

    conf_ungrounded = calculate_calibrated_confidence(
        extracted_facts=facts_ungrounded,
        risk_analysis=None,
        compliance_assessment=None,
    )

    assert conf_grounded > conf_ungrounded
