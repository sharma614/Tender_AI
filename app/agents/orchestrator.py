from typing import Any, Dict, List, Literal

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from .base import BaseAgent, AgentError
from .compliance_agent import ComplianceAgent
from .extraction import ExtractionAgent
from .risk_agent import RiskAgent
from .summarizer_agent import SummarizerAgent
from ..models import Tender, ToolCall
from ..schemas import (
    DEFAULT_COMPANY_PROFILE,
    AgentState,
    CompanyProfile,
    RetrievedChunk,
    TenderDetails,
    TenderSummary,
    RiskAnalysis,
    ComplianceAssessment,
)


# Targeted RAG query definitions for pre-graph retrieval
EXTRACTION_QUERIES = [
    "tender submission deadline closing date",
    "minimum annual turnover financial requirement",
    "earnest money deposit EMD bank guarantee",
    "solvency certificate requirements",
    "bidder technical eligibility criteria years experience",
]

SUMMARY_QUERIES = [
    "tender title overall scope of work",
    "project deliverables technical specifications",
    "contract duration completion timeline",
    "important dates pre-bid meeting submission date",
    "issuing authority organization department name",
]

RISK_QUERIES = [
    "liquidated damages delay penalty clause",
    "termination for convenience default clause",
    "indemnity unlimited liability clause",
    "payment terms milestone payment retention money",
    "force majeure definition obligations",
    "project execution delivery risk obligations",
]

CHUNKS_PER_QUERY = 3
MAX_CHUNKS_PER_AGENT = 12


def _retrieve_multi(
    db: Session,
    tender_id: int,
    queries: List[str],
    embedder,
    retriever_fn,
    k: int = CHUNKS_PER_QUERY,
    cap: int = MAX_CHUNKS_PER_AGENT,
) -> List[RetrievedChunk]:
    seen: Dict[int, RetrievedChunk] = {}
    for query in queries:
        qvec = embedder.embed_text(query)
        results = retriever_fn(db, tender_id, query, k=k, query_vector=qvec)
        for chunk in results:
            if chunk.chunk_id not in seen or chunk.distance < seen[chunk.chunk_id].distance:
                seen[chunk.chunk_id] = chunk
    return sorted(seen.values(), key=lambda c: c.distance)[:cap]


class AgentOrchestrator:
    def __init__(self):
        self.extraction_agent = ExtractionAgent()
        self.summarizer_agent = SummarizerAgent()
        self.risk_agent = RiskAgent()
        self.compliance_agent = ComplianceAgent()
        self.graph = self._build_graph()

    # ------------------------------------------------------------------
    # Graph nodes — each returns a partial state update
    # ------------------------------------------------------------------

    def _node_extraction(self, state: AgentState) -> Dict[str, Any]:
        cite = state.mode == "rag-cite"
        chunks = state.extraction_chunks if state.mode != "full" else None
        try:
            facts, record = self.extraction_agent.extract_details(
                state.raw_text, context_chunks=chunks, cite=cite
            )
            return {"extracted_facts": facts, "tool_calls": [record]}
        except AgentError as exc:
            return {"tool_calls": [exc.record], "errors": [str(exc)]}

    def _node_summarizer(self, state: AgentState) -> Dict[str, Any]:
        cite = state.mode == "rag-cite"
        chunks = state.summary_chunks if state.mode != "full" else None
        try:
            summary, record = self.summarizer_agent.generate_summary(
                state.raw_text, context_chunks=chunks, cite=cite
            )
            return {"summary": summary, "tool_calls": [record]}
        except AgentError as exc:
            return {"tool_calls": [exc.record], "errors": [str(exc)]}

    def _node_risk(self, state: AgentState) -> Dict[str, Any]:
        cite = state.mode == "rag-cite"
        chunks = state.risk_chunks if state.mode != "full" else None
        try:
            risks, record = self.risk_agent.evaluate_risks(
                state.raw_text, context_chunks=chunks, cite=cite
            )
            return {"risk_analysis": risks, "tool_calls": [record]}
        except AgentError as exc:
            return {"tool_calls": [exc.record], "errors": [str(exc)]}

    def _node_compliance(self, state: AgentState) -> Dict[str, Any]:
        profile = state.company_profile or DEFAULT_COMPANY_PROFILE
        try:
            assessment, record = self.compliance_agent.evaluate_compliance(
                extracted_facts=state.extracted_facts,
                company_profile=profile,
            )
            return {"compliance_assessment": assessment, "tool_calls": [record]}
        except AgentError as exc:
            return {"tool_calls": [exc.record], "errors": [str(exc)]}

    def _node_bid_decision(self, state: AgentState) -> Dict[str, Any]:
        from ..services.bid_decision_engine import evaluate_bid_decision
        try:
            decision = evaluate_bid_decision(state)
            return {"bid_decision": decision}
        except Exception as exc:
            return {"errors": [f"Bid decision calculation failed: {exc}"]}

    @staticmethod
    def _route_after_extraction(state: AgentState) -> str:
        return "compliance" if state.extracted_facts is not None else "bid_decision"

    def _build_graph(self):
        graph = StateGraph(AgentState)

        graph.add_node("extraction", self._node_extraction)
        graph.add_node("summarizer", self._node_summarizer)
        graph.add_node("risk", self._node_risk)
        graph.add_node("compliance", self._node_compliance)
        graph.add_node("bid_decision", self._node_bid_decision)

        graph.add_edge(START, "extraction")
        graph.add_edge(START, "summarizer")
        graph.add_edge(START, "risk")

        graph.add_conditional_edges(
            "extraction",
            self._route_after_extraction,
            {"compliance": "compliance", "bid_decision": "bid_decision"},
        )
        graph.add_edge("compliance", "bid_decision")
        graph.add_edge("summarizer", END)
        graph.add_edge("risk", END)
        graph.add_edge("bid_decision", END)

        return graph.compile()

    # ------------------------------------------------------------------

    def run_analysis(
        self,
        tender_id: int,
        raw_text: str,
        db: Session,
        company_profile: CompanyProfile = None,
        mode: Literal["full", "rag", "rag-cite"] = "full",
    ) -> AgentState:
        """
        Execute the agent graph for one tender and persist the results.

        Execution is the compiled LangGraph in `self.graph`. Extraction,
        summarization and risk fan out from START, so LangGraph's Pregel loop
        schedules them in one superstep and runs them concurrently on its own
        executor; compliance runs in the next superstep because it depends on
        `extracted_facts`. There is deliberately only one execution path —
        an earlier version kept a hand-rolled ThreadPoolExecutor alongside the
        graph and defaulted to it, which meant the compiled graph was never
        actually exercised and the two paths could silently diverge.

        Retrieval runs here rather than inside a node because it needs the DB
        Session, and agents must not hold one (see app/agents/base.py).
        """
        extraction_chunks: List[RetrievedChunk] = []
        summary_chunks: List[RetrievedChunk] = []
        risk_chunks: List[RetrievedChunk] = []

        if mode in ("rag", "rag-cite"):
            from ..services.embedder import TextEmbedder
            from ..services.retriever import retrieve

            embedder = TextEmbedder()
            extraction_chunks = _retrieve_multi(
                db, tender_id, EXTRACTION_QUERIES, embedder, retrieve
            )
            summary_chunks = _retrieve_multi(
                db, tender_id, SUMMARY_QUERIES, embedder, retrieve
            )
            risk_chunks = _retrieve_multi(
                db, tender_id, RISK_QUERIES, embedder, retrieve
            )

        initial = AgentState(
            tender_id=tender_id,
            raw_text=raw_text,
            company_profile=company_profile or DEFAULT_COMPANY_PROFILE,
            mode=mode,
            extraction_chunks=extraction_chunks,
            summary_chunks=summary_chunks,
            risk_chunks=risk_chunks,
        )

        print(f"--- Starting Agent Analysis (mode={mode}, LangGraph) for Tender ID: {tender_id} ---")

        result = self.graph.invoke(initial)
        state = AgentState(**result) if isinstance(result, dict) else result

        self._persist(state, db)

        if state.errors:
            raise RuntimeError(
                f"{len(state.errors)} agent(s) failed for tender {tender_id}: "
                + " | ".join(state.errors)
            )

        print(f"--- Orchestration Complete (mode={mode}) ---")
        return state

    def apply_corrigendum(
        self,
        parent_tender_id: int,
        corrigendum_text: str,
        db: Session,
        company_profile: CompanyProfile = None,
    ) -> AgentState:
        """
        Process a Corrigendum / Amendment document.

        Overrides base tender extracted facts (e.g. updated submission deadline or EMD)
        and merges new risk/summary updates into the parent tender record in DB.
        """
        print(f"--- Processing Corrigendum for Parent Tender ID: {parent_tender_id} ---")
        corrigendum_state = self.run_analysis(
            tender_id=parent_tender_id,
            raw_text=corrigendum_text,
            db=db,
            company_profile=company_profile,
            mode="full",
        )

        tender = db.query(Tender).filter(Tender.id == parent_tender_id).first()
        if tender and corrigendum_state.extracted_facts:
            corr_facts = corrigendum_state.extracted_facts
            existing_details = dict(tender.details or {})

            # Override extracted fields if corrigendum specifies them.
            # Everything is written into Tender.details, which is the only place
            # these values are actually persisted — Tender has no dedicated
            # submission_deadline column, so assigning one would be a silent no-op.
            if corr_facts.submission_deadline and corr_facts.submission_deadline.lower() != "not specified":
                existing_details["submission_deadline"] = corr_facts.submission_deadline

            fin = corr_facts.financial_requirements
            existing_fin = dict(existing_details.get("financial_requirements") or {})
            if fin.minimum_turnover:
                existing_fin["minimum_turnover"] = fin.minimum_turnover
            if fin.earnest_money_deposit_emd:
                existing_fin["earnest_money_deposit_emd"] = fin.earnest_money_deposit_emd
            existing_details["financial_requirements"] = existing_fin

            # Reassign rather than mutate in place: SQLAlchemy's default JSON type
            # is not mutation-tracked, so an in-place edit would not be flushed.
            tender.details = existing_details
            db.commit()
            print(f"Successfully applied corrigendum overrides to Tender ID: {parent_tender_id}")

        return corrigendum_state

    def _persist(self, state: AgentState, db: Session) -> None:
        from ..database import log_tool_call

        for record in state.tool_calls:
            try:
                log_tool_call(db=db, tender_id=state.tender_id, record=record)
            except Exception as exc:
                db.rollback()
                print(f"Warning: failed to log tool call {record.agent_name}: {exc}")

        try:
            tender = db.query(Tender).filter(Tender.id == state.tender_id).first()
            if tender:
                if state.summary is not None:
                    tender.summary = state.summary.model_dump()
                if state.risk_analysis is not None:
                    tender.risks = state.risk_analysis.model_dump()
                if state.compliance_assessment is not None:
                    tender.compliance_status = state.compliance_assessment.model_dump()
                if state.bid_decision is not None:
                    details = dict(tender.details or {})
                    details["bid_decision"] = state.bid_decision.model_dump()
                    tender.details = details
                db.commit()
                print("Analysis results persisted to database.")
        except Exception as exc:
            db.rollback()
            print(f"Error persisting analysis results: {exc}")
