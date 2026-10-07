"""Style_Profile generation via the cheap Bedrock model (Req 1.6; Design Style_Learner step 6).

Reuses bedrock.invoke (cheap tier) + s3store.put_json. The model id comes from env
MABLOP_STYLE_MODEL; the default is the locked cheap-style selection (decisions.md D14).
"""
import json
import os
import re
from datetime import datetime, timezone

import bedrock
import s3store

_STYLE_MODEL_ENV = "MABLOP_STYLE_MODEL"  # cheap style/proof model (Design tiering D10, Req 1.6)
# Locked in decisions.md: Claude 3 Haiku (cheapest Claude, structured JSON, us-east-1).
_DEFAULT_STYLE_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"

_PROFILE_KEY = "style-profile/profile.json"

# Schema keys we expect back from the model (builtAt/sourcePostCount filled by us).
_MODEL_KEYS = (
    "tone",
    "frequentExpressions",
    "paragraphStructure",
    "sentenceLength",
    "emojiUsage",
    "sampleChunkIds",
)


def generate_profile(post_bodies: list[str]) -> dict:
    """Analyze post bodies via the cheap model; return a Style_Profile dict (1.6)."""
    model_id = os.environ.get(_STYLE_MODEL_ENV, _DEFAULT_STYLE_MODEL)
    prompt = _build_prompt(post_bodies)
    out = bedrock.invoke(model_id, {"inputText": prompt})
    parsed = _parse_profile(_response_text(out))
    # We own builtAt + sourcePostCount; never trust the model for them.
    parsed["builtAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parsed["sourcePostCount"] = len(post_bodies)
    return parsed


def store_profile(profile: dict) -> str:
    """Write the profile to style-profile/profile.json (1.6, 4.4). Return the key."""
    return s3store.put_json(_PROFILE_KEY, profile)


def _build_prompt(post_bodies: list[str]) -> str:
    """Prompt asking the model for a Style_Profile JSON object."""
    joined = "\n\n---\n\n".join(post_bodies)
    return (
        "Analyze the author's Korean writing style from the posts below and respond "
        "with ONLY a JSON object (no prose) with these keys: "
        '"tone" (string), "frequentExpressions" (string array), '
        '"paragraphStructure" (object: avgSentencesPerParagraph int, usesHeadings bool, '
        'listUsage string), "sentenceLength" (string), "emojiUsage" (string), '
        '"sampleChunkIds" (string array).\n\n'
        f"POSTS:\n{joined}"
    )


def _response_text(out: dict) -> str:
    """Best-effort pull of the generated text from a Bedrock response body."""
    for key in ("outputText", "completion", "text"):
        if isinstance(out.get(key), str):
            return out[key]
    # Fall back to the whole response serialized -- _parse_profile extracts the JSON.
    return json.dumps(out)


def _parse_profile(text: str) -> dict:
    """Extract the first JSON object from model text (handles prose-wrapped JSON)."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        obj = _extract_json_object(text)
    if not isinstance(obj, dict):
        raise ValueError("model did not return a JSON object")
    return {k: obj.get(k) for k in _MODEL_KEYS}


def _extract_json_object(text: str) -> dict:
    """Find and parse the first balanced {...} block in text."""
    for match in re.finditer(r"\{", text):
        depth = 0
        for i in range(match.start(), len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[match.start():i + 1])
                    except ValueError:
                        break
    raise ValueError("no JSON object found in model response")
