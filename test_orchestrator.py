import os
import sys

print("--- Running TenderAI Orchestrator & Tool Call Logging Verification Checks ---")

# Add the root directory to path to resolve imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app.schemas import AgentState, CompanyProfile, TenderDetails, TenderSummary, RiskAnalysis, ComplianceAssessment
    print("[OK] Schemas loaded correctly.")
except Exception as e:
    print(f"[ERROR] Failed to load schemas: {e}")
    sys.exit(1)

try:
    from app.models import ToolCall, Tender, TenderChunk
    print("[OK] Database models loaded correctly.")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"[ERROR] Failed to load models: {e}")
    sys.exit(1)

try:
    from app.agents.orchestrator import AgentOrchestrator
    print("[OK] AgentOrchestrator loaded correctly.")
except Exception as e:
    print(f"[ERROR] Failed to load orchestrator: {e}")
    sys.exit(1)

# Verify ToolCall DB Insertion logic using SQLite for testing
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, log_tool_call

print("Running mock DB session tool call logging test...")
engine = create_engine("sqlite:///:memory:")
Session = sessionmaker(bind=engine)
session = Session()

# Create tables in SQLite
try:
    # SQLite doesn't have vector, so we only create tenders and tool_calls tables
    Base.metadata.create_all(bind=engine, tables=[Tender.__table__, ToolCall.__table__])
    print("[OK] Mock SQLite schema created successfully.")
except Exception as e:
    print(f"[ERROR] Mock SQLite schema creation failed: {e}")
    sys.exit(1)

# Insert mock Tender
try:
    mock_tender = Tender(title="Test Tender", raw_text="This is a test tender document.")
    session.add(mock_tender)
    session.commit()
    print(f"[OK] Mock Tender inserted with ID: {mock_tender.id}")
except Exception as e:
    session.rollback()
    print(f"[ERROR] Failed to insert mock Tender: {e}")
    sys.exit(1)

# Insert mock ToolCall
try:
    log_tool_call(
        db=session,
        tender_id=mock_tender.id,
        agent_name="MockAgent",
        tool_name="mock_tool",
        input_arguments={"param1": "value1"},
        output_response={"status": "mock_success"}
    )
    # Check if successfully inserted
    tool_call_record = session.query(ToolCall).filter(ToolCall.agent_name == "MockAgent").first()
    if tool_call_record:
         print(f"[OK] ToolCall log successfully verified in DB: {tool_call_record.tool_name} for agent {tool_call_record.agent_name}")
    else:
         raise ValueError("ToolCall record not found in database.")
except Exception as e:
    session.rollback()
    print(f"[ERROR] Failed to verify ToolCall insertion: {e}")
    sys.exit(1)

print("\n--- All Orchestration & Logging Checks Passed Successfully! ---")
