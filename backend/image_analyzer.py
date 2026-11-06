"""Image_Analyzer (FR-3, Req 3): presigned URLs + vision captioning."""
import base64
import mimetypes
import os
import re
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
# Locked in decisions.md: Claude Sonnet 4.5 fallback (verified multimodal access, us-east-1).
_DEFAULT_VISION_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
_CAPTION_MAX_ENV = "MABLOP_CAPTION_MAX_CHARS"  # short-caption bound (Req 7.5)
_DEFAULT_CAPTION_MAX = 200


def _ext(filename: str, content_type: str) -> str:
    """Lowercase extension from filename, else from content-type, else 'bin'."""
    if filename and "." in filename:
        return filename.rsplit(".", 1)[1].lower()
    if content_type and "/" in content_type:
        return content_type.rsplit("/", 1)[1].lower()
    return "bin"


_FILENAME_MAX = 100
_UNSAFE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f\[\]()<>]")


def safe_filename(name) -> str | None:
    """Normalize a user-supplied filename for display in a draft marker; None when nothing usable is left.

    Keeps only the last path component (drops '/' and '\\' directories), removes control characters,
    markdown/HTML-significant brackets and parentheses, and '..' sequences, and bounds the length
    while keeping the extension.
    """
    if not isinstance(name, str):
        return None
    base = re.split(r"[\\/]", name)[-1]
    base = _UNSAFE_NAME_CHARS.sub("", base)
    while ".." in base:
        base = base.replace("..", ".")
    base = base.strip().strip(".").strip()
    if not base:
        return None
    if len(base) > _FILENAME_MAX:
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            base = stem[: _FILENAME_MAX - len(ext) - 1] + "." + ext
        else:
            base = base[:_FILENAME_MAX]
    return base


def issue_presigned_urls(files: list) -> dict:
    """Presigned PUTs scoped to users/{id}/images/, short expiry, type+size conditions (3.1, 3.2).

    The sanitized original filename is stored in images/<imgId>.json so draft markers can show it
    (the object key itself stays uuid-based); a failed metadata write never blocks the upload.
    """
    bucket = os.environ["MABLOP_BUCKET"]
    expiry = min(int(os.environ.get(_EXPIRY_ENV, _DEFAULT_EXPIRY)), _DEFAULT_EXPIRY)
    max_bytes = int(os.environ.get(_MAXBYTES_ENV, _DEFAULT_MAXBYTES))
    s3 = boto3.client("s3")

    uploads = []
    for f in (files or [])[:_MAX_IMAGES]:  # don't issue beyond 10
        content_type = f.get("contentType") or "application/octet-stream"
        key = s3store.user_key(f"images/{uuid.uuid4().hex}.{_ext(f.get('filename', ''), content_type)}")
        filename = safe_filename(f.get("filename"))
        if filename:
            try:
                s3store.put_json(f"{key.rsplit('.', 1)[0]}.json", {"key": key, "filename": filename})
            except Exception:  # noqa: BLE001 - marker falls back to the stored object name
                pass
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
    media_type = mimetypes.guess_type(key)[0] or "image/jpeg"
    return bedrock.vision_caption(b64, media_type, model_id)


def _bound(text: str) -> str:
    """Truncate caption to the configured max chars (7.5)."""
    return text[: max(0, int(os.environ.get(_CAPTION_MAX_ENV, _DEFAULT_CAPTION_MAX)))]


def analyze_images(images: list) -> dict:
    """Vision caption per image lacking description; skip + reuse user text (3.3-3.5)."""
    images = images or []
    # Only the acting user's own image keys are accepted (Req 8.7); nothing is read before this check.
    s3store.assert_own_image_keys([img.get("key") if isinstance(img, dict) else None for img in images])
    captions = []
    for img in images:
        key = img["key"]
        desc = img.get("description")
        if desc:  # user text verbatim, no model call (3.4, cost-0)
            caption, source = _bound(desc), "user"
        else:  # one vision call (3.3)
            caption, source = _bound(_vision_caption(key)), "vision"
        meta = {"key": key, "caption": caption, "source": source}
        if img.get("contentType"):
            meta["contentType"] = img["contentType"]
        meta_key = f"{key.rsplit('.', 1)[0]}.json"
        try:  # keep the original filename stored at issue time
            prior = s3store.get_json(meta_key)
        except Exception:  # noqa: BLE001 - no earlier metadata is normal
            prior = None
        if isinstance(prior, dict) and safe_filename(prior.get("filename")):
            meta["filename"] = safe_filename(prior["filename"])
        s3store.put_json(meta_key, meta)  # users/{id}/images/<imgId>.json (3.5)
        captions.append({"key": key, "caption": caption, "source": source})
    return {"captions": captions}
