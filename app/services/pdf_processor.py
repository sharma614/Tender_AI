import re
import logging
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz  # PyMuPDF ≥1.24

logger = logging.getLogger(__name__)
_PAGE_MARKER = re.compile(r"--- Page (\d+) ---")

# Regex to recognize section headers (e.g. SECTION 1, CLAUSE 4.2, 1.1 Eligibility, ANNEXURE A, PART III)
_HEADING_PATTERN = re.compile(
    r"(?:\n|^)\s*("
    r"(?:SECTION|CLAUSE|ANNEXURE|PART|SCHEDULE)\s+[A-Z0-9\.]+|"
    r"\d+\.\d+\s+[A-Z][A-Za-z0-9\s]+"
    r")",
    re.IGNORECASE
)


class PDFProcessor:
    @staticmethod
    def extract_text_from_bytes(file_bytes: bytes, enable_ocr: bool = True) -> str:
        """
        Extracts raw text from a PDF file.

        If a page produces fewer than 50 characters (e.g. scanned image PDF),
        attempts Tesseract OCR via PyMuPDF pixmap rendering as a fallback.
        """
        text = ""
        has_ocr = False
        try:
            import pytesseract
            from PIL import Image
            has_ocr = True
        except ImportError:
            logger.info("pytesseract or PIL not installed; OCR fallback disabled.")

        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text()

                # Scanned PDF fallback
                if enable_ocr and has_ocr and (not page_text or len(page_text.strip()) < 50):
                    try:
                        pix = page.get_pixmap(dpi=150)
                        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        ocr_text = pytesseract.image_to_string(img)
                        if ocr_text and len(ocr_text.strip()) > len(page_text.strip()):
                            page_text = ocr_text + "\n[OCR Extracted]"
                    except Exception as e:
                        logger.warning(f"OCR fallback failed for page {page_num + 1}: {e}")

                if page_text:
                    # Strip PyMuPDF software header artifacts
                    page_text = re.sub(r"(?m)^\s*% Written by MuPDF[^\n]*\n?", "", page_text)
                    text += f"--- Page {page_num + 1} ---\n{page_text}\n"

        return text

    @staticmethod
    def page_index(text: str) -> List[Dict[str, int]]:
        """
        Build an ordered index of [{"char_start", "page"}] from the inline page
        markers emitted by `extract_text_from_bytes`.
        """
        return [
            {"char_start": m.start(), "page": int(m.group(1))}
            for m in _PAGE_MARKER.finditer(text)
        ]

    @staticmethod
    def page_for_offset(page_idx: List[Dict[str, int]], offset: int) -> Optional[int]:
        """
        Resolve a character offset to a page number: the page of the last marker
        at or before `offset`.
        """
        page = None
        for entry in page_idx:
            if entry["char_start"] <= offset:
                page = entry["page"]
            else:
                break
        return page

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[Dict[str, Any]]:
        """
        Character-window chunking (baseline).
        """
        chunks: List[Dict[str, Any]] = []
        if not text:
            return chunks

        page_idx = PDFProcessor.page_index(text)

        start = 0
        chunk_idx = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + chunk_size, text_len)
            window = text[start:end]
            chunk_content = window.strip()

            if chunk_content:
                content_start = start + (len(window) - len(window.lstrip()))
                chunks.append({
                    "text": chunk_content,
                    "index": chunk_idx,
                    "char_start": content_start,
                    "page_number": PDFProcessor.page_for_offset(page_idx, content_start),
                    "section_title": "General",
                })
                chunk_idx += 1

            start += (chunk_size - chunk_overlap)
            if chunk_size <= chunk_overlap:
                break

        return chunks

    @staticmethod
    def chunk_text_structured(text: str, min_chunk_size: int = 200, max_chunk_size: int = 1500) -> List[Dict[str, Any]]:
        """
        Structure-aware chunking.

        Splits text on clause/section headings rather than cutting clauses in half.
        Preserves section titles and inline page attribution in chunk metadata.
        """
        chunks: List[Dict[str, Any]] = []
        if not text:
            return chunks

        page_idx = PDFProcessor.page_index(text)
        matches = list(_HEADING_PATTERN.finditer(text))

        if not matches:
            # Fallback to standard windowed chunking if no structural headings found
            return PDFProcessor.chunk_text(text, chunk_size=1000, chunk_overlap=200)

        sections: List[Tuple[str, int, int]] = []

        # Text before the first heading is still document content — a preamble,
        # NIT header or covering letter — and often carries the tender title and
        # dates. Emit it as its own section rather than dropping it, otherwise
        # any gold span living above the first heading becomes unretrievable.
        if matches[0].start() > 0 and text[: matches[0].start()].strip():
            sections.append(("Preamble", 0, matches[0].start()))

        for i, match in enumerate(matches):
            section_title = match.group(1).strip()
            start_pos = match.start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append((section_title, start_pos, end_pos))

        chunk_idx = 0
        for section_title, s_start, s_end in sections:
            sec_text = text[s_start:s_end].strip()
            if not sec_text:
                continue

            # If section text fits within max_chunk_size, keep as a single structure-aligned chunk
            if len(sec_text) <= max_chunk_size:
                chunks.append({
                    "text": sec_text,
                    "index": chunk_idx,
                    "char_start": s_start,
                    "page_number": PDFProcessor.page_for_offset(page_idx, s_start),
                    "section_title": section_title,
                })
                chunk_idx += 1
            else:
                # Sub-chunk long sections using character windowing while maintaining section context
                sub_chunks = PDFProcessor.chunk_text(sec_text, chunk_size=1000, chunk_overlap=150)
                for sc in sub_chunks:
                    sc["index"] = chunk_idx
                    sc["char_start"] += s_start
                    sc["page_number"] = PDFProcessor.page_for_offset(page_idx, sc["char_start"])
                    sc["section_title"] = section_title
                    chunks.append(sc)
                    chunk_idx += 1

        return chunks
