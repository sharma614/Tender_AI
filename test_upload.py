import sys
import os

def run_checks():
    print("--- Running TenderAI backend verification checks ---")

    # Check imports
    try:
        import fitz
        print("[OK] PyMuPDF imported successfully.")
    except ImportError:
        print("[ERROR] Failed to import PyMuPDF (fitz). Please run: pip install pymupdf")
        sys.exit(1)

    try:
        from sentence_transformers import SentenceTransformer
        print("[OK] sentence-transformers imported successfully.")
    except ImportError:
        print("[ERROR] Failed to import sentence-transformers. Please run: pip install sentence-transformers")
        sys.exit(1)

    try:
        from google import genai
        print("[OK] google-genai imported successfully.")
    except ImportError:
        print("[ERROR] Failed to import google-genai. Please run: pip install google-genai")
        sys.exit(1)

    try:
        from pgvector.sqlalchemy import Vector
        print("[OK] pgvector imported successfully.")
    except ImportError:
        print("[ERROR] Failed to import pgvector-sqlalchemy. Please run: pip install pgvector")
        sys.exit(1)

    # Test PDFProcessor chunking
    from app.services.pdf_processor import PDFProcessor
    sample_text = "Tender Title: Tender for Office Supplies. Eligibility: Bidders must have 3 years of experience. Deadline: October 12, 2026. EMD: $5,000."
    chunks = PDFProcessor.chunk_text(sample_text, chunk_size=50, chunk_overlap=10)
    if len(chunks) > 0:
        print(f"[OK] PDFProcessor chunking works. Created {len(chunks)} chunks from sample text.")
    else:
        print("[ERROR] PDFProcessor chunking failed.")
        sys.exit(1)

    print("\n--- Setup is complete and verified! ---")
    print("To start your PostgreSQL + pgvector database:")
    print("  docker-compose up -d")
    print("")
    print("To start the FastAPI server locally:")
    print("  pip install -r requirements.txt")
    print("  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000")
    print("")

if __name__ == "__main__":
    run_checks()
