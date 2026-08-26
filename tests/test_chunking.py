"""
tests/test_chunking.py — Unit tests for PDFProcessor and chunking logic.
"""
from app.services.pdf_processor import PDFProcessor

def test_chunk_text_basic():
    sample_text = "Clause 1: Scope of work. " * 50
    chunks = PDFProcessor.chunk_text(sample_text, chunk_size=200, chunk_overlap=50)
    
    assert len(chunks) > 1
    assert "index" in chunks[0]
    assert "text" in chunks[0]
    assert len(chunks[0]["text"]) <= 250

def test_extract_text_empty_handling():
    sample_text = "   \n\n  "
    chunks = PDFProcessor.chunk_text(sample_text, chunk_size=100, chunk_overlap=20)
    assert len(chunks) == 0 or chunks[0]["text"].strip() == ""
