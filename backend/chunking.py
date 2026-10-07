"""Chunking of post bodies into Post_Chunks (Req 1.4; Design Style_Learner step 4).

Character-based, stdlib-only, deterministic. No tokenizer/NLP library (YAGNI).
"""

DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 80


def chunk_body(
    body: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split body into non-empty Post_Chunks, each <= chunk_size chars.

    Fixed target size with small overlap. Consecutive chunks share `overlap`
    chars so adjacent context is preserved; stripping that overlap and
    concatenating reconstructs the full body with no content dropped
    (Property 1).

    Empty/whitespace-only body -> []. Body <= chunk_size -> single chunk.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    if not body:
        return []

    if len(body) <= chunk_size:
        return [body]

    stride = chunk_size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(body):
        chunk = body[start : start + chunk_size]
        chunks.append(chunk)
        if start + chunk_size >= len(body):
            break
        start += stride
    return chunks


def reconstruct(chunks: list[str], overlap: int = DEFAULT_OVERLAP) -> str:
    """Inverse of chunk_body: drop the overlap prefix of each chunk after the
    first and concatenate, yielding the original body."""
    if not chunks:
        return ""
    out = [chunks[0]]
    for prev, cur in zip(chunks, chunks[1:]):
        shared = min(overlap, len(prev))
        out.append(cur[shared:])
    return "".join(out)
