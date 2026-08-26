import os
from sentence_transformers import SentenceTransformer
from typing import List

class TextEmbedder:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TextEmbedder, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        model_name = os.environ.get("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
        print(f"Loading SentenceTransformer model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        self._initialized = True
        print("Model loaded successfully.")

    def embed_text(self, text: str) -> List[float]:
        """
        Generates a dense vector embedding for a single text snippet.
        """
        if not text.strip():
            return [0.0] * 384
        embedding = self.model.encode(text)
        return embedding.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generates dense vector embeddings for a list of text snippets in batch.
        """
        if not texts:
            return []
        embeddings = self.model.encode(texts)
        return embeddings.tolist()
