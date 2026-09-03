"""
tests/test_citations_and_baseline.py — Unit tests for RAG citation verification,
MonolithicAgent baseline, and multi-mode agent schemas.
"""
import pytest
from app.schemas import CitationRef, TenderDetails, FinancialRequirements, RiskPoint, CitationRef
from app.services.citation_verifier import verify_citations, CitationVerificationResult


def test_citation_verifier_grounded():
    chunk_map = {
        1: "The Earnest Money Deposit (EMD) required is Rs. 5,00,000 in bank guarantee.",
        2: "Minimum annual turnover must be at least Rs. 5 Crore for the last 3 financial years.",
    }
    state_dict = {
        "extracted_facts": {
            "tender_title": "Construction of IT Park",
            "submission_deadline": "2026-10-15",
            "overall_summary": "Construction of IT Park",
            "field_citations": [
                {"chunk_id": -1, "page_number": None},
                {"chunk_id": -1, "page_number": None},
                {"chunk_id": -1, "page_number": None},
            ],
            "financial_requirements": {
                "minimum_turnover": "Rs. 5 Crore",
                "earnest_money_deposit_emd": "Rs. 5,00,000",
                "solvency_certificate_required": False,
                "additional_financial_conditions": [],
                "citations": [
                    {"chunk_id": 2, "page_number": 1},
                    {"chunk_id": 1, "page_number": 1},
                ],
            },
        },
        "risk_analysis": {
            "overall_risk_rating": "Medium",
            "identified_risks": [],
        },
        "summary": {
            "executive_summary": "Executive summary text",
            "scope_of_work": [],
            "important_dates": {},
            "citations": [],
        },
    }

    res = verify_citations(state_dict, chunk_map)
    assert res.total == 5
    assert res.grounded == 2
    assert res.ungrounded == 3
    assert res.hallucinated == 0
    assert res.grounding_precision == 1.0
    assert res.hallucination_rate == 0.0


def test_citation_verifier_hallucination():
    chunk_map = {
        10: "The project completion period is 12 months from award date.",
    }
    state_dict = {
        "extracted_facts": {
            "tender_title": "Solar Installation",
            "submission_deadline": "2026-12-01",
            "overall_summary": "Solar Installation",
            "field_citations": [],
            "financial_requirements": {
                "minimum_turnover": "Rs. 100 Crore",  # Not in chunk 10
                "earnest_money_deposit_emd": None,
                "citations": [
                    {"chunk_id": 10, "page_number": 2},
                ],
            },
        },
        "risk_analysis": {"identified_risks": []},
        "summary": {"citations": []},
    }

    res = verify_citations(state_dict, chunk_map)
    assert res.hallucinated == 1
    assert res.grounding_precision == 0.0
    assert res.hallucination_rate == 1.0


def test_citation_ref_schema_defaults():
    cit = CitationRef()
    assert cit.chunk_id == -1
    assert cit.page_number is None


def test_monolithic_agent_instantiation():
    from app.agents.monolithic import MonolithicAgent
    agent = MonolithicAgent()
    assert agent.agent_name == "MonolithicAgent"
