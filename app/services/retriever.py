"""
app/services/retriever.py — Vector similarity search over stored tender chunks.

This is the retrieval half of the RAG pipeline. Prior to this module, chunks were
embedded into `tender_chunks` by app/tasks.py and never read back — there was no
cosine search anywhere in the codebase, so "Recall@K" could not be measured.
"""
from typing import List, Optional
from sqlalchemy.orm import Session

from ..database import DATABASE_URL
from ..models import TenderChunk
from ..schemas import RetrievedChunk
from .embedder import TextEmbedder


def retrieve(
    db: Session,
    tender_id: int,
    query: str,
    k: int = 5,
    query_vector: Optional[List[float]] = None,
) -> List[RetrievedChunk]:
    """
    Return the `k` chunks of `tender_id` closest to `query` by cosine distance.
    """
    if k <= 0:
        return []

    vector = query_vector if query_vector is not None else TextEmbedder().embed_text(query)

    if DATABASE_URL.startswith("sqlite"):
        import numpy as np
        chunks = db.query(TenderChunk).filter(TenderChunk.tender_id == tender_id).all()
        qvec = np.array(vector)
        scored = []
        for chunk in chunks:
            if not chunk.embedding:
                continue
            cvec = np.array(chunk.embedding)
            norm_q = np.linalg.norm(qvec)
            norm_c = np.linalg.norm(cvec)
            if norm_q == 0 or norm_c == 0:
                dist = 1.0
            else:
                dist = float(1.0 - (np.dot(qvec, cvec) / (norm_q * norm_c)))
            scored.append((chunk, dist))
        scored.sort(key=lambda x: x[1])
        return [
            RetrievedChunk(
                chunk_id=c.id,
                chunk_index=c.chunk_index,
                text=c.text_content,
                distance=d,
                page_number=c.page_number,
                char_start=c.char_start,
            )
            for c, d in scored[:k]
        ]
    else:
        distance = TenderChunk.embedding.cosine_distance(vector).label("distance")
        rows = (
            db.query(TenderChunk, distance)
            .filter(TenderChunk.tender_id == tender_id)
            .filter(TenderChunk.embedding.isnot(None))
            .order_by(distance.asc())
            .limit(k)
            .all()
        )

        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                text=chunk.text_content,
                distance=float(dist),
                page_number=chunk.page_number,
                char_start=chunk.char_start,
            )
            for chunk, dist in rows
        ]
