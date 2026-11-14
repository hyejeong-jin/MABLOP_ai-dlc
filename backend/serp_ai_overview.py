"""Google AI Overview collection through SerpApi (server-side only).

The API key is read once per warm container from SSM (env MABLOP_SERP_KEY_SSM_PATH).
It is sent only as a query parameter to serpapi.com. It is never logged, returned to
clients, or placed in an error message. Each call makes at most MAX_CALLS requests.
"""
import json
import os
import urllib.parse
import urllib.request

SERP_ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_KEY_PATH = "/mablop/serp-api-key"
MAX_OVERVIEW_CHARS = 4000
MAX_CALLS = 2
_key_cache: str | None = None


def _api_key() -> str:
    """SerpApi key from SSM SecureString (cached); empty string when unavailable."""
    global _key_cache
    if _key_cache:
        return _key_cache
    path = os.environ.get("MABLOP_SERP_KEY_SSM_PATH", DEFAULT_KEY_PATH)
    if not path:
        return ""
    try:
        import boto3

        region = os.environ.get("MABLOP_BEDROCK_REGION", "us-east-1")
        value = boto3.client("ssm", region_name=region).get_parameter(
            Name=path, WithDecryption=True
        )["Parameter"]["Value"]
    except Exception:  # noqa: BLE001 - the caller reports the control; the reason stays private
        return ""
    value = str(value or "").strip()
    _key_cache = value or None
    return value


def _get_json(params: dict, timeout: float = 10.0) -> dict:
    """GET the SerpApi endpoint. Errors never include the URL, which carries the key."""
    url = SERP_ENDPOINT + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"serpapi request failed: {type(exc).__name__}") from None
    if not isinstance(payload, dict):
        raise RuntimeError("serpapi response is not an object")
    return payload


def _flatten(blocks, out: list) -> None:
    """Turn SerpApi text_blocks into plain lines: headings as '## ', list items as '- '."""
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("list"), list):
            for item in block["list"]:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("text_blocks"), list):
                    _flatten(item["text_blocks"], out)
                    continue
                title = str(item.get("title") or "").strip()
                snippet = str(item.get("snippet") or "").strip()
                if title and snippet:
                    out.append(f"- {title}: {snippet}")
                elif title or snippet:
                    out.append(f"- {title or snippet}")
        else:
            snippet = str(block.get("snippet") or "").strip()
            if snippet:
                prefix = "## " if block.get("type") == "heading" else ""
                out.append(prefix + snippet)


def fetch_ai_overview(keyword: str) -> tuple[dict | None, str | None]:
    """Return (overview, None) on success or (None, control) when nothing usable came back.

    overview = {"text": str}. Controls: serp_provider_unavailable (no key),
    ai_overview_unavailable (Google showed no overview for this query).
    Raises RuntimeError when a SerpApi request fails.
    """
    key = _api_key()
    if not key:
        return None, "serp_provider_unavailable"
    data = _get_json({"engine": "google", "q": keyword, "hl": "ko", "gl": "kr", "api_key": key})
    calls = 1
    overview = data.get("ai_overview")
    if isinstance(overview, dict) and not overview.get("text_blocks") and overview.get("page_token") and calls < MAX_CALLS:
        follow = _get_json({"engine": "google_ai_overview", "page_token": overview["page_token"], "api_key": key})
        calls += 1
        overview = follow.get("ai_overview") if isinstance(follow.get("ai_overview"), dict) else follow
    if not isinstance(overview, dict) or not isinstance(overview.get("text_blocks"), list):
        return None, "ai_overview_unavailable"
    lines: list = []
    _flatten(overview["text_blocks"], lines)
    text = "\n".join(lines).strip()[:MAX_OVERVIEW_CHARS]
    if not text:
        return None, "ai_overview_unavailable"
    return {"text": text}, None