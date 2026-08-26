import fitz  # PyMuPDF
from typing import List, Dict

class PDFProcessor:
    @staticmethod
    def extract_text_from_bytes(file_bytes: bytes) -> str:
        """
        Extracts all raw text from a PDF file provided as bytes.
        """
        text = ""
        # Open the PDF document from memory
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text()
                if page_text:
                    text += f"--- Page {page_num + 1} ---\n{page_text}\n"
        return text

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[Dict[str, any]]:
        """
        Splits a string of text into smaller overlapping chunks.
        Returns a list of dicts: [{"text": str, "index": int}]
        """
        chunks = []
        if not text:
            return chunks

        start = 0
        chunk_idx = 0
        text_len = len(text)

        while start < text_len:
            # Determine the end bound of the chunk
            end = min(start + chunk_size, text_len)
            chunk_content = text[start:end].strip()
            
            if chunk_content:
                chunks.append({
                    "text": chunk_content,
                    "index": chunk_idx
                })
                chunk_idx += 1
            
            # Slide the window by chunk_size - overlap
            start += (chunk_size - chunk_overlap)
            
            # Guard against infinite loops if chunk_size is too small or overlap is too big
            if chunk_size <= chunk_overlap:
                break
                
        return chunks
