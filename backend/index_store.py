"""Vector index build + store: .npy matrix, JSON row-map, manifest (Req 1.5; Design Embedding storage format)."""
import datetime
import hashlib
import io
import os

import numpy as np

import s3store
from retrieval import load_index as _load_npy

INDEX_KEY = "embeddings/index.npy"
MAP_KEY = "embeddings/index-map.json"
MANIFEST_KEY = "vector-index/manifest.json"

_EMBED_MODEL_ENV = "MABLOP_EMBED_MODEL"


def _npy_bytes(matrix: np.ndarray) -> bytes:
    """Serialize array to .npy bytes."""
    buf = io.BytesIO()
    np.save(buf, matrix)
    return buf.getvalue()


def build_and_store_index(vectors, chunk_meta):
    """Build [N, D] float32 index; store .npy, row-map JSON, manifest JSON (Req 1.5). Return manifest."""
    matrix = np.asarray(vectors, dtype=np.float32).reshape(len(vectors), -1)
    npy = _npy_bytes(matrix)

    row_map = {
        str(i): {
            "chunkId": m["chunkId"],
            "postId": m["postId"],
            "offset": m["offset"],
            "text": m["text"],
        }
        for i, m in enumerate(chunk_meta)
    }

    manifest = {
        "dim": int(matrix.shape[1]) if matrix.size else 0,
        "count": int(matrix.shape[0]),
        "model": os.environ.get(_EMBED_MODEL_ENV, ""),
        "builtAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checksum": hashlib.sha256(npy).hexdigest(),
    }

    s3store.put_bytes(INDEX_KEY, npy, content_type="application/octet-stream")
    s3store.put_json(MAP_KEY, row_map)
    s3store.put_json(MANIFEST_KEY, manifest)
    return manifest


def load_index():
    """Load stored index; return (matrix [N, D] float32, row-map dict)."""
    matrix = _load_npy(s3store.get_bytes(INDEX_KEY))
    row_map = s3store.get_json(MAP_KEY)
    return matrix, row_map
