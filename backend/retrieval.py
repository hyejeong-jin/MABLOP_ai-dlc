"""RAG retrieval: numpy cosine Top_K over the stored embedding index (2.2-2.4, 7.2)."""
import io

import numpy as np


def load_index(npy_bytes: bytes) -> np.ndarray:
    """Load the [N, D] float32 index from .npy bytes."""
    return np.load(io.BytesIO(npy_bytes))


def _unit(a: np.ndarray, axis=None, keepdims=False) -> np.ndarray:
    """Normalize to unit length. Clamp (not add) the norm so zero vectors stay
    finite without perturbing non-zero ones -- keeps cosine exactly scale-invariant
    even for tiny-magnitude vectors (an additive epsilon would not)."""
    norm = np.linalg.norm(a, axis=axis, keepdims=keepdims)
    norm = np.where(norm == 0.0, 1.0, norm)  # avoid div-by-zero only when truly zero
    return a / norm


def cosine_top_k(query: np.ndarray, matrix: np.ndarray, k: int) -> list:
    """Top_K cosine search, score-desc; returns [(row_index, score)] (2.3, 2.4, 7.2)."""
    q = _unit(query)
    m = _unit(matrix, axis=1, keepdims=True)
    scores = m @ q                       # [N]
    k = min(k, scores.shape[0])          # Top_K cap (2.4, 7.2)
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx])]  # sort the k by score desc
    return [(int(i), float(scores[i])) for i in idx]
