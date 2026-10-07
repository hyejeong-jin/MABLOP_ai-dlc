"""Thin shared Bedrock client (Req 1.4; Design Bedrock model tiering; browser->Lambda->Bedrock Req 6.2).

Backend is the ONLY Bedrock caller. Embedding only for now (YAGNI); text-gen + vision
land here later (tasks 6.6/9.3/9.6) via the generic `invoke` helper.

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
