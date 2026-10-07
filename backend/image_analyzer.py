"""Image_Analyzer (FR-3, Req 3): presigned URLs + vision captioning."""
import base64
import os
import uuid

import boto3

import bedrock
import s3store

_MAX_IMAGES = 10  # 10-image cap (Req 7.4)
_EXPIRY_ENV = "MABLOP_PRESIGN_EXPIRY"  # seconds, default 120 (Req 3.2 short expiry)
_MAXBYTES_ENV = "MABLOP_IMAGE_MAX_BYTES"  # default 2 MiB
_DEFAULT_EXPIRY = 120
_DEFAULT_MAXBYTES = 2 * 1024 * 1024

_VISION_MODEL_ENV = "MABLOP_VISION_MODEL"  # low/mid multimodal (Design tiering, Req 3.3)
# Locked in decisions.md: Claude 3 Haiku (multimodal, cheapest vision-capable, us-east-1).
_DEFAULT_VISION_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
_CAPTION_MAX_ENV = "MABLOP_CAPTION_MAX_CHARS"  # short-caption bound (Req 7.5)
_DEFAULT_CAPTION_MAX = 200


def _ext(filename: str, content_type: str) -> str:
    """Lowercase extension from filename, else from content-type, else 'bin'."""
    if filename and "." in filename:
        return filename.rsplit(".", 1)[1].lower()
    if content_type and "/" in content_type:
        return content_type.rsplit("/", 1)[1].lower()
    return "bin"


def issue_presigned_urls(files: list) -> dict:
    """Presigned PUTs scoped to images/, short expiry, type+size conditions (3.1, 3.2)."""
    bucket = os.environ["MABLOP_BUCKET"]
    expiry = min(int(os.environ.get(_EXPIRY_ENV, _DEFAULT_EXPIRY)), _DEFAULT_EXPIRY)
    max_bytes = int(os.environ.get(_MAXBYTES_ENV, _DEFAULT_MAXBYTES))
    s3 = boto3.client("s3")

    uploads = []
    for f in (files or [])[:_MAX_IMAGES]:  # don't issue beyond 10
        content_type = f.get("contentType") or "application/octet-stream"
        key = f"images/{uuid.uuid4().hex}.{_ext(f.get('filename', ''), content_type)}"
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expiry,
        )
        uploads.append({
            "key": key,
            "url": url,
            "method": "PUT",
            "expiresInSeconds": expiry,
            "conditions": {"contentType": content_type, "maxSize": max_bytes},
        })
    return {"uploads": uploads}


def _vision_caption(key: str) -> str:
    """One vision call: fetch bytes from S3, base64, caption (3.3)."""
    model_id = os.environ.get(_VISION_MODEL_ENV, _DEFAULT_VISION_MODEL)
    b64 = base64.b64encode(s3store.get_bytes(key)).decode("ascii")
    out = bedrock.invoke(model_id, {"image": b64, "task": "caption"})
    return str(out["caption"])


def _bound(text: str) -> str:
    """Truncate caption to the configured max chars (7.5)."""
    return text[: max(0, int(os.environ.get(_CAPTION_MAX_ENV, _DEFAULT_CAPTION_MAX)))]


def analyze_images(images: list) -> dict:
    """Vision caption per image lacking description; skip + reuse user text (3.3-3.5)."""
    captions = []
    for img in images or []:
        key = img["key"]
        desc = img.get("description")
        if desc:  # user text verbatim, no model call (3.4, cost-0)
            caption, source = _bound(desc), "user"
        else:  # one vision call (3.3)
            caption, source = _bound(_vision_caption(key)), "vision"
        meta = {"key": key, "caption": caption, "source": source}
        if img.get("contentType"):
            meta["contentType"] = img["contentType"]
        s3store.put_json(f"{key.rsplit('.', 1)[0]}.json", meta)  # images/<imgId>.json (3.5)
        captions.append({"key": key, "caption": caption, "source": source})
    return {"captions": captions}
