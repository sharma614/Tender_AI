import concurrent.futures
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

    @staticmethod
    def _route_after_extraction(state: AgentState) -> str:
        return "compliance" if state.extracted_facts is not None else END

    def _build_graph(self):
        graph = StateGraph(AgentState)

        graph.add_node("extraction", self._node_extraction)
        graph.add_node("summarizer", self._node_summarizer)
        graph.add_node("risk", self._node_risk)
        graph.add_node("compliance", self._node_compliance)

        graph.add_edge(START, "extraction")
        graph.add_edge(START, "summarizer")
        graph.add_edge(START, "risk")

        graph.add_conditional_edges(
            "extraction",
            self._route_after_extraction,
            {"compliance": "compliance", END: END},
        )
        graph.add_edge("summarizer", END)
        graph.add_edge("risk", END)
        graph.add_edge("compliance", END)

        return graph.compile()

    # ------------------------------------------------------------------

    def run_analysis(
        self,
        tender_id: int,
        raw_text: str,
        db: Session,
        company_profile: CompanyProfile = None,
        mode: Literal["full", "rag", "rag-cite"] = "full",
        use_parallel: bool = True,
    ) -> AgentState:
        """
        Execute agent tasks.

        When `use_parallel=True`, runs Extraction, Summarizer, and Risk agents
        concurrently via ThreadPoolExecutor(max_workers=3) for ~3x latency reduction.
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

        print(f"--- Starting Agent Analysis (mode={mode}, parallel={use_parallel}) for Tender ID: {tender_id} ---")

        if use_parallel:
            # Concurrently execute 3 independent agent nodes
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                fut_ext = executor.submit(self._node_extraction, initial)
                fut_sum = executor.submit(self._node_summarizer, initial)
                fut_rsk = executor.submit(self._node_risk, initial)

                res_ext = fut_ext.result()
                res_sum = fut_sum.result()
                res_rsk = fut_rsk.result()

            # Combine node state updates
            tool_calls = (
                res_ext.get("tool_calls", [])
                + res_sum.get("tool_calls", [])
                + res_rsk.get("tool_calls", [])
            )
            errors = (
                res_ext.get("errors", [])
                + res_sum.get("errors", [])
                + res_rsk.get("errors", [])
            )

            state = AgentState(
                tender_id=tender_id,
                raw_text=raw_text,
                company_profile=initial.company_profile,
                mode=mode,
                extracted_facts=res_ext.get("extracted_facts"),
                summary=res_sum.get("summary"),
                risk_analysis=res_rsk.get("risk_analysis"),
                tool_calls=tool_calls,
                errors=errors,
            )

            # Compliance agent depends on extracted_facts
            if state.extracted_facts:
                res_comp = self._node_compliance(state)
                state.compliance_assessment = res_comp.get("compliance_assessment")
                if "tool_calls" in res_comp:
                    state.tool_calls.extend(res_comp["tool_calls"])
                if "errors" in res_comp:
                    state.errors.extend(res_comp["errors"])

        else:
            # Sequential LangGraph execution
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
            use_parallel=True,
        )

        tender = db.query(Tender).filter(Tender.id == parent_tender_id).first()
        if tender and corrigendum_state.extracted_facts:
            corr_facts = corrigendum_state.extracted_facts
            existing_details = tender.details or {}

            # Override extracted fields if corrigendum specifies them
            if corr_facts.submission_deadline and corr_facts.submission_deadline.lower() != "not specified":
                existing_details["submission_deadline"] = corr_facts.submission_deadline
                tender.submission_deadline = corr_facts.submission_deadline

            fin = corr_facts.financial_requirements
            if fin.minimum_turnover:
                existing_details["financial_requirements"]["minimum_turnover"] = fin.minimum_turnover
            if fin.earnest_money_deposit_emd:
                existing_details["financial_requirements"]["earnest_money_deposit_emd"] = fin.earnest_money_deposit_emd

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
                db.commit()
                print("Analysis results persisted to database.")
        except Exception as exc:
            db.rollback()
            print(f"Error persisting analysis results: {exc}")
