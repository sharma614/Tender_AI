"""
tests/test_agents.py — Unit tests for Compliance and Risk agent schemas.
"""
from app.schemas import (
    CompanyProfile,
    ComplianceAssessment,
    ComplianceItem,
    RiskAnalysis,
    RiskPoint,
)

def test_company_profile_schema():
    profile = CompanyProfile(
        company_name="AeroCorp",
        annual_turnover=10000000.0,
        years_of_experience=8,
        has_solvency_certificate=True,
        technical_capabilities=["AI", "Cloud", "DevOps"],
    )
    assert profile.company_name == "AeroCorp"
    assert profile.annual_turnover == 10000000.0
    assert profile.has_solvency_certificate is True

def test_compliance_assessment_structure():
    item = ComplianceItem(
        requirement_name="Minimum Turnover",
        tender_value="USD 5,000,000",
        company_value="USD 10,000,000",
        status="Compliant",
        reason="Company turnover exceeds minimum requirement.",
    )
    assessment = ComplianceAssessment(
        overall_compliant=True,
        checklist=[item],
    )
    assert assessment.overall_compliant is True
    assert len(assessment.checklist) == 1
    assert assessment.checklist[0].status == "Compliant"

def test_risk_analysis_structure():
    risk = RiskPoint(
        risk_item="Aggressive Timeline",
        description="Delivery schedule is constrained to 30 days.",
        severity="High",
        mitigation_strategy="Deploy parallel engineering teams.",
    )
    analysis = RiskAnalysis(
        overall_risk_rating="Medium",
        identified_risks=[risk],
    )
    assert analysis.overall_risk_rating == "Medium"
    assert analysis.identified_risks[0].severity == "High"
