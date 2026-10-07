"""Drafting_Engine (FR-2, Req 2, 4): retrieve, build prompt, generate Draft.

Steps 6-8 (design): invoke the quality Korean text-gen model, normalize
`![img-N]` placeholders, store Markdown under generated-posts/, return it.
Retrieval + prompt build live in retrieval.py / prompt_builder.py; the stubs
here delegate so 9.8 can compose embed->retrieve->prompt->generate.
"""
import os
import re
import uuid

import bedrock
import index_store
import prompt_builder
import retrieval
import s3store
import style_profile

_MAX_IMAGES = 10  # 10-image cap (Req 3.7, 7.4)
_DRAFT_PREFIX = "generated-posts/"
_PROFILE_KEY = "style-profile/profile.json"  # Style_Profile location (Req 1.6, 2.5)

_TEXT_MODEL_ENV = "MABLOP_TEXT_MODEL"  # quality Korean text-gen (Design tiering, Req 2.7)
# Locked in decisions.md: Claude 3.5 Haiku (quality Korean text-gen, us-east-1).
_DEFAULT_TEXT_MODEL = "anthropic.claude-3-5-haiku-20241022-v1:0"

_TOP_K_ENV = "MABLOP_TOP_K"  # Top_K retrieval cap (Req 2.4, 7.2)
_DEFAULT_TOP_K = 8

# Matches a single ![img-N] placeholder (N = 1+ digits).
_PLACEHOLDER_RE = re.compile(r"!\[img-(\d+)\]")


def cosine_top_k(query, matrix, k: int) -> list:
    """Delegate to retrieval.cosine_top_k (2.3, 2.4, 7.2)."""
    return retrieval.cosine_top_k(query, matrix, k)


def build_prompt(profile: dict, chunks: list, inputs: dict) -> str:
    """Delegate to prompt_builder.build_prompt (2.6, 6.5, 7.1)."""
    inputs = inputs or {}
    return prompt_builder.build_prompt(
        profile,
        chunks,
        inputs.get("title"),
        inputs.get("outline"),
        inputs.get("notes"),
        inputs.get("captions"),
        inputs.get("token_cap"),
    )


def _text_model() -> str:
    """Quality Korean text-gen model id from env; placeholder default."""
    return os.environ.get(_TEXT_MODEL_ENV, _DEFAULT_TEXT_MODEL)


def _extract_text(resp) -> str:
    """Pull generated Markdown from a Bedrock text-gen response (shape-tolerant)."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        # Common shapes: {"outputText"}, Messages API {"content":[{"text"}]},
        # completion/generation keys, or {"results":[{"outputText"}]}.
        for key in ("outputText", "completion", "generation", "text"):
            val = resp.get(key)
            if isinstance(val, str):
                return val
        content = resp.get("content")
        if isinstance(content, list):
            return "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        results = resp.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            return results[0].get("outputText", "") or ""
    return ""


def normalize_placeholders(markdown: str, image_count: int) -> str:
    """Ensure ![img-1..K] each appear exactly once; drop out-of-range; K<=10 (3.6,3.7,4.1,7.4)."""
    k = max(0, min(int(image_count), _MAX_IMAGES))
    text = markdown or ""

    seen = set()

    def _repl(m):
        n = int(m.group(1))
        if n < 1 or n > k or n in seen:  # out-of-range or duplicate -> remove
            return ""
        seen.add(n)
        return m.group(0)

    text = _PLACEHOLDER_RE.sub(_repl, text)

    # Append any supplied index the model omitted, in order, so each 1..K appears once.
    missing = [n for n in range(1, k + 1) if n not in seen]
    if missing:
        tail = "\n\n" + "\n\n".join("![img-{}]".format(n) for n in missing)
        text = (text.rstrip("\n") + tail) if text.strip() else tail.lstrip("\n")

    return text


def generate_from_prompt(prompt: str, image_count: int, draft_id: str = None) -> dict:
    """Generate+normalize+store a Draft from an assembled prompt (2.7,2.8,2.9,3.6,3.7,4.1,4.2).

    Invokes the quality Korean text model, parses the Markdown, normalizes
    `![img-N]` placeholders to each supplied index 1..K (K<=10) exactly once,
    stores it under generated-posts/<draftId>.md, and returns the draft.
    """
    resp = bedrock.invoke(_text_model(), {"prompt": prompt})
    raw = _extract_text(resp)
    markdown = normalize_placeholders(raw, image_count)

    draft_id = draft_id or "draft-" + uuid.uuid4().hex
    draft_key = _DRAFT_PREFIX + draft_id + ".md"
    s3store.put_bytes(draft_key, markdown.encode("utf-8"), content_type="text/markdown")

    return {"draftId": draft_id, "markdown": markdown, "draftKey": draft_key}


def _top_k() -> int:
    """Top_K from env; default _DEFAULT_TOP_K; fall back on bad/non-positive."""
    try:
        val = int(os.environ.get(_TOP_K_ENV, _DEFAULT_TOP_K))
    except ValueError:
        return _DEFAULT_TOP_K
    return val if val > 0 else _DEFAULT_TOP_K


def _caption_key(image_key: str) -> str:
    """images/<imgId>.<ext> -> images/<imgId>.json (matches Image_Analyzer)."""
    return image_key.rsplit(".", 1)[0] + ".json"


def _load_captions(image_keys: list) -> dict:
    """Map {1-based index: caption} from stored images/<imgId>.json; skip missing."""
    captions = {}
    for i, key in enumerate(image_keys, start=1):
        try:
            meta = s3store.get_json(_caption_key(key))
        except Exception:  # noqa: BLE001 - missing caption json is tolerable
            continue
        cap = meta.get("caption") if isinstance(meta, dict) else None
        if cap:
            captions[i] = cap
    return captions


def _load_profile() -> dict:
    """Load style-profile/profile.json; tolerate missing -> {} (2.5)."""
    try:
        prof = s3store.get_json(_PROFILE_KEY)
    except Exception:  # noqa: BLE001 - no profile yet is tolerable
        return {}
    return prof if isinstance(prof, dict) else {}


def _retrieve_chunks(query_text: str) -> list:
    """Embed query, cosine Top_K over the stored index, map rows -> chunk texts (2.2-2.4)."""
    matrix, row_map = index_store.load_index()
    query = bedrock.embed(query_text)
    import numpy as np

    hits = retrieval.cosine_top_k(np.asarray(query, dtype=np.float32), matrix, _top_k())
    chunks = []
    for row, _score in hits:
        entry = row_map.get(str(row))
        if entry and entry.get("text"):
            chunks.append(entry["text"])
    return chunks


def _input_text(title, outline, notes) -> str:
    """Assemble embed input from the author's drafting inputs (2.2)."""
    return "\n".join(p for p in (title, outline, notes) if p)


def generate_draft(payload: dict) -> dict:
    """Full generate-draft flow (2.1-2.9, 3.6-3.7, 4.1-4.2; Design Drafting Flow).

    embed input -> cosine Top_K -> load profile -> build prompt -> generate ->
    placeholder-validate -> store -> synchronous return. Composes existing
    modules; adds no retrieval/prompt logic of its own.
    """
    payload = payload or {}
    title = payload.get("title")
    outline = payload.get("outline")
    notes = payload.get("notes")
    image_keys = (payload.get("imageKeys") or [])[:_MAX_IMAGES]

    captions = _load_captions(image_keys)
    chunks = _retrieve_chunks(_input_text(title, outline, notes))
    profile = _load_profile()

    prompt = prompt_builder.build_prompt(
        profile, chunks, title, outline, notes, captions,
        prompt_builder.token_cap_from_env(),
    )

    result = generate_from_prompt(prompt, image_count=len(image_keys))
    result["topK"] = len(chunks)
    return result


def get_result(payload: dict) -> dict:
    """get-result for kind 'draft': return stored Markdown from generated-posts/<id>.md (4.1, 4.2)."""
    draft_id = (payload or {}).get("id")
    if not draft_id:
        raise ValueError("get-result requires an 'id'")
    key = _DRAFT_PREFIX + draft_id + ".md"
    markdown = s3store.get_bytes(key).decode("utf-8")
    return {"status": "ready", "markdown": markdown}
