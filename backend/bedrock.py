"""Thin shared Bedrock client (Req 1.4; Design Bedrock model tiering; browser->Lambda->Bedrock Req 6.2).

Backend is the ONLY Bedrock caller. Titan embeddings and Claude Messages calls share this thin client.

Model IDs: configurable via env. Defaults are the locked us-east-1 selections
recorded in docs/aidlc/inception/decisions.md (D14). Override per-env via MABLOP_* env.
"""
import json
import os

import boto3

_REGION_ENV = "MABLOP_BEDROCK_REGION"  # default us-east-1 (Req 8.1)
_DEFAULT_REGION = "us-east-1"

_EMBED_MODEL_ENV = "MABLOP_EMBED_MODEL"  # cheap multilingual embedding (Design tiering, Req 1.4)
# Locked in decisions.md: Titan Text Embeddings V2 (multilingual, cheap, us-east-1).
_DEFAULT_EMBED_MODEL = "amazon.titan-embed-text-v2:0"


def get_client():
    """bedrock-runtime client; region from env, default us-east-1."""
    region = os.environ.get(_REGION_ENV, _DEFAULT_REGION)
    return boto3.client("bedrock-runtime", region_name=region)


def invoke(model_id: str, body: dict) -> dict:
    """Generic invoke_model: send JSON body, return parsed JSON response. Shared by all tiers."""
    resp = get_client().invoke_model(
        modelId=model_id,
        body=json.dumps(body).encode("utf-8"),
        accept="application/json",
        contentType="application/json",
    )
    return json.loads(resp["body"].read())


def embed(text: str) -> list[float]:
    """Embed one text via the env-configured cheap multilingual model; return the float vector."""
    model_id = os.environ.get(_EMBED_MODEL_ENV, _DEFAULT_EMBED_MODEL)
    out = invoke(model_id, {"inputText": text})
    return [float(x) for x in out["embedding"]]

_CLAUDE_API_VERSION = "bedrock-2023-05-31"
_DEFAULT_TEXT_MAX_TOKENS = 4096
_DEFAULT_VISION_MAX_TOKENS = 256


def _message_text(out: dict) -> str:
    """Extract Claude Messages text; keep legacy mock shapes compatible."""
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        content = out.get("content")
        if isinstance(content, list):
            return "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        for key in ("outputText", "completion", "generation", "text", "caption"):
            if isinstance(out.get(key), str):
                return out[key]
    return ""


def text_generate(prompt: str, model_id: str, max_tokens: int = _DEFAULT_TEXT_MAX_TOKENS) -> str:
    """Generate text with the Claude Messages InvokeModel schema."""
    body = {
        "anthropic_version": _CLAUDE_API_VERSION,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    return _message_text(invoke(model_id, body))


def vision_caption(
    image_base64: str,
    media_type: str,
    model_id: str,
    max_tokens: int = _DEFAULT_VISION_MAX_TOKENS,
) -> str:
    """Caption one image with Claude's Messages multimodal content schema."""
    body = {
        "anthropic_version": _CLAUDE_API_VERSION,
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": image_base64,
                }},
                {"type": "text", "text": "Describe this image briefly in Korean, within 200 characters."},
            ],
        }],
    }
    return _message_text(invoke(model_id, body))
