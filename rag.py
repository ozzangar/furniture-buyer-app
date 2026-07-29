"""Vector RAG over the product catalogue (Level 4).

Design (from the earlier analysis):
- Embed a SEMANTIC chunk per product: name + category + colours. Category carries
  most of the meaning (names are invented words); price/dimensions are NOT embedded
  because embeddings can't do numeric comparison.
- Keep price + dimensions + item_id as METADATA attached to each chunk, so "small",
  "cheap", "under $500" get handled by the model reasoning over real numbers after retrieval.
- In-memory cosine similarity over ~762 vectors — no vector DB needed at this size.
- Embedding model: chromadb's built-in ONNX MiniLM (local, no torch, no API key,
  ThreatLocker-safe). Same model used for documents AND queries (required for RAG).

Lazy-built on first query and cached in-process.
"""
from __future__ import annotations

import threading

import shop_api

_lock = threading.Lock()
_state: dict = {"built": False, "ef": None, "vectors": None, "products": None}


def _embedder():
    from chromadb.utils import embedding_functions
    return embedding_functions.DefaultEmbeddingFunction()


def _chunk_text(p: dict) -> str:
    """The text we embed for a product — meaning-bearing fields only."""
    parts = [p.get("product_name") or ""]
    if p.get("category"):
        parts.append(f"Category: {p['category']}")
    cols = p.get("colours")
    if cols:
        parts.append("Colour: " + ", ".join(cols))
    return ". ".join(parts)


def _build():
    """Fetch the catalogue, embed every product once. Idempotent + thread-safe."""
    import numpy as np
    with _lock:
        if _state["built"]:
            return
        products = shop_api.list_products(limit=1000)   # all 762, cached upstream
        ef = _embedder()
        texts = [_chunk_text(p) for p in products]
        vecs = np.array(ef(texts), dtype="float32")
        # pre-normalise so cosine similarity is a plain dot product
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        _state.update(built=True, ef=ef, vectors=vecs / norms, products=products)


def search(query: str, k: int = 6) -> list[dict]:
    """Return the top-k products most similar in MEANING to the query.

    Each result includes price + dimensions so the caller (the agent/Claude) can
    reason about 'small', 'cheap', etc. over real numbers — embeddings can't.
    """
    import numpy as np
    if not _state["built"]:
        _build()
    ef, vectors, products = _state["ef"], _state["vectors"], _state["products"]
    qv = np.array(ef([query])[0], dtype="float32")
    qn = np.linalg.norm(qv) or 1.0
    scores = vectors @ (qv / qn)                       # cosine sim (both normalised)
    top = np.argsort(-scores)[:max(1, k)]
    out = []
    for i in top:
        p = products[int(i)]
        out.append({
            "item_id": p["item_id"],
            "product_name": p["product_name"],
            "category": p.get("category"),
            "price": p.get("price"),
            "colours": p.get("colours"),
            "width": p.get("width"), "height": p.get("height"), "depth": p.get("depth"),
            "similarity": round(float(scores[int(i)]), 3),
        })
    return out
