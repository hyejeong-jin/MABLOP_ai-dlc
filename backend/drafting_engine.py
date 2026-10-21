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
# Locked in decisions.md: Claude Sonnet 4.5 (quality Korean text-gen, us-east-1).
_DEFAULT_TEXT_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

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
    """Quality Korean text-gen model id from env; locked default in decisions.md D14."""
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


def _caption_terms(caption: str) -> list[str]:
    """Extract useful Korean/Latin terms for contextual image placement."""
    return [term for term in re.findall(r"[0-9A-Za-z\uAC00-\uD7A3]{2,}", str(caption or ""))]


def _insert_by_caption(text: str, number: int, caption: str, image_names=None) -> str:
    """Insert one filename-aware image marker near matching context."""
    marker = _image_marker(number, image_names)
    terms = _caption_terms(caption)
    lines = text.splitlines()
    # Prefer headings: they identify the image's section without disrupting prose.
    for heading_only in (True, False):
        for i, line in enumerate(lines):
            if heading_only and not line.lstrip().startswith("#"):
                continue
            if any(term.casefold() in line.casefold() for term in terms):
                lines.insert(i + 1, marker)
                return "\n".join(lines)
    # Do not append uncontextualized markers at the document end.
    return text


def _image_marker(number: int, image_names=None) -> str:
    """Render a stable marker with the original attachment filename when known."""
    name = image_names.get(number) if isinstance(image_names, dict) else None
    return f"![img-{number}]({name})" if name else f"![img-{number}]"


def normalize_placeholders(
    markdown: str,
    image_count: int,
    captions: dict | list | None = None,
    image_names: dict | None = None,
) -> str:
    """Keep valid markers and place missing images near matching caption context.

    When captions are supplied (the real generate-draft path), missing markers
    are inserted near matching headings/lines and never dumped at the end.
    Legacy direct calls without captions retain the old append fallback for
    compatibility with the pure placeholder tests.
    """
    k = max(0, min(int(image_count), _MAX_IMAGES))
    text = markdown or ""
    seen = set()

    def _repl(m):
        n = int(m.group(1))
        if n < 1 or n > k or n in seen:
            return ""
        seen.add(n)
        return _image_marker(n, image_names)

    text = _PLACEHOLDER_RE.sub(_repl, text)
    missing = [n for n in range(1, k + 1) if n not in seen]

    if captions is None:
        if missing:
            tail = "\n\n" + "\n\n".join(_image_marker(n, image_names) for n in missing)
            text = (text.rstrip("\n") + tail) if text.strip() else tail.lstrip("\n")
        return text

    for number in missing:
        caption = captions.get(number, "") if isinstance(captions, dict) else (
            captions[number - 1] if number - 1 < len(captions) else ""
        )
        placed = _insert_by_caption(text, number, caption, image_names)
        if placed != text:
            text = placed
    return text


def generate_from_prompt(
    prompt: str,
    image_count: int,
    draft_id: str = None,
    captions=None,
    image_names: dict | None = None,
) -> dict:
    """Generate+normalize+store a Draft from an assembled prompt (2.7,2.8,2.9,3.6,3.7,4.1,4.2).

    Invokes the quality Korean text model, parses the Markdown, normalizes
    `![img-N]` placeholders to each supplied index 1..K (K<=10) exactly once,
    stores it under generated-posts/<draftId>.md, and returns the draft.
    """
    raw = bedrock.text_generate(prompt, _text_model())
    markdown = normalize_placeholders(
        raw, image_count, captions=captions, image_names=image_names
    )

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
    image_names = {
        index: key.rsplit("/", 1)[-1]
        for index, key in enumerate(image_keys, start=1)
    }
    chunks = _retrieve_chunks(_input_text(title, outline, notes))
    profile = _load_profile()

    prompt = prompt_builder.build_prompt(
        profile, chunks, title, outline, notes, captions,
        prompt_builder.token_cap_from_env(),
    )

    result = generate_from_prompt(
        prompt,
        image_count=len(image_keys),
        captions=captions,
        image_names=image_names,
    )
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
