"""Style_Learner (FR-1, Req 1): crawl Naver blog, chunk, embed, build index + profile."""

import os
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import bedrock
import chunking
import checkpoint
import crawler
import index_store
import s3store
import style_profile

_MOBILE_HOST = "m.blog.naver.com"

_MAX_POST_COUNT_ENV = "MABLOP_MAX_POST_COUNT"  # cap per run (Req 1.8)
_DEFAULT_MAX_POST_COUNT = 20

_RAW_PREFIX = "raw-posts/"  # extracted/pasted bodies (Req 1.7)


def resolve_url(blog_url: str) -> str:
    """Normalize a Naver post URL to the mobile host. Pure, no network (1.1).

    Accepts desktop `blog.naver.com/{blogId}/{logNo}`, the `?blogId=&logNo=`
    query form, `PostView.naver?blogId=&logNo=`, and the mobile host. Returns
    `https://m.blog.naver.com/{blogId}/{logNo}`. Raises ValueError if the
    blogId/logNo pair cannot be extracted.
    """
    blog_id, log_no = _extract_ids(blog_url)
    if not blog_id or not log_no:
        raise ValueError(f"cannot resolve Naver blogId/logNo from: {blog_url!r}")
    return f"https://{_MOBILE_HOST}/{blog_id}/{log_no}"


def _extract_ids(blog_url: str) -> tuple[str, str]:
    """Pull (blogId, logNo) from any supported Naver URL shape."""
    parts = urlsplit(blog_url.strip())
    query = parse_qs(parts.query)
    blog_id = _first(query.get("blogId"))
    log_no = _first(query.get("logNo"))

    # Path form: /{blogId}/{logNo} (desktop or mobile host). PostView.naver
    # carries ids only in the query, so its path segments are ignored here.
    segs = [s for s in parts.path.split("/") if s]
    if "PostView.naver" not in parts.path and len(segs) >= 2:
        blog_id = blog_id or segs[0]
        log_no = log_no or segs[1]
    return blog_id, log_no


def _first(values: list[str] | None) -> str:
    """First query value, or empty string."""
    return values[0] if values else ""


def _max_post_count() -> int:
    """MAX_POST_COUNT from env; default; fall back on bad/non-positive (1.8)."""
    try:
        val = int(os.environ.get(_MAX_POST_COUNT_ENV, _DEFAULT_MAX_POST_COUNT))
    except ValueError:
        return _DEFAULT_MAX_POST_COUNT
    return val if val > 0 else _DEFAULT_MAX_POST_COUNT


def _discover_posts(payload: dict) -> list[dict]:
    """Resolve the posts to process into [{postId, url?}], capped to MAX_POST_COUNT (1.8).

    MVP keeps discovery minimal (no heavy HTML crawling): the payload supplies
    explicit single-post URLs/ids, and/or pastedTexts carry postIds. Each entry
    gets a stable postId so crawl and paste fallback align on the same key.
    """
    seen: dict[str, dict] = {}

    for i, raw in enumerate(payload.get("posts") or []):
        if isinstance(raw, str):
            url, given_id = raw, None
        else:
            url, given_id = raw.get("url"), raw.get("postId")
        post_id = given_id or _post_id_from_url(url) or f"p{i}"
        if url:
            seen.setdefault(post_id, {"postId": post_id, "url": url})

    # pastedTexts can introduce posts that have no crawlable URL at all.
    for pasted in payload.get("pastedTexts") or []:
        post_id = pasted.get("postId")
        if post_id:
            seen.setdefault(post_id, {"postId": post_id, "url": None})

    return list(seen.values())[: _max_post_count()]


def _post_id_from_url(url: str | None) -> str:
    """Derive a stable postId (the logNo) from a resolvable Naver URL, else ''."""
    if not url:
        return ""
    try:
        _blog_id, log_no = _extract_ids(url)
    except Exception:  # noqa: BLE001 - unresolvable url -> fall back to index id
        return ""
    return log_no


def _pasted_index(payload: dict) -> dict[str, str]:
    """Map postId -> pasted body text (paste fallback, 1.3)."""
    out = {}
    for pasted in payload.get("pastedTexts") or []:
        post_id, text = pasted.get("postId"), pasted.get("text")
        if post_id and text:
            out[post_id] = text
    return out


def _fetch_body(url: str | None) -> str:
    """Resolve + crawl + extract one post body. Raises crawler.CrawlError on failure (1.2)."""
    if not url:
        raise crawler.CrawlError("no url for post")
    html = crawler.crawl(resolve_url(url))
    return crawler.extract_body(html)


def _get_body(post: dict, pasted: dict[str, str]) -> str:
    """Crawled body, else pasted fallback; '' if neither available (1.2, 1.3, 1.7)."""
    try:
        body = _fetch_body(post.get("url"))
        if body:
            return body
    except crawler.CrawlError:
        pass
    return pasted.get(post["postId"], "")


def _store_raw(post_id: str, body: str) -> None:
    """Persist the raw body under raw-posts/<postId>.txt (1.7)."""
    s3store.put_bytes(f"{_RAW_PREFIX}{post_id}.txt", body.encode("utf-8"),
                      content_type="text/plain; charset=utf-8")


def _embed_post(post_id: str, body: str) -> tuple[list[dict], list[list[float]]]:
    """Chunk + embed one body; return (chunk_meta, vectors) aligned by index (1.4)."""
    chunk_meta: list[dict] = []
    vectors: list[list[float]] = []
    offset = 0
    for i, text in enumerate(chunking.chunk_body(body)):
        chunk_meta.append({
            "chunkId": f"{post_id}-c{i}",
            "postId": post_id,
            "offset": offset,
            "text": text,
        })
        vectors.append(bedrock.embed(text))
        offset += len(text)
    return chunk_meta, vectors


def learn_style(payload: dict) -> dict:
    """Run full pipeline: resolve->crawl/paste->chunk->embed->index/profile (1.1-1.10).

    Processes up to MAX_POST_COUNT posts, checkpointing to S3 so a run resumes
    inside the 15-min Lambda cap. Returns the learn-style contract: status
    completed with counts+keys, or in-progress with a checkpoint to resume.
    """
    payload = payload or {}
    run_id = payload.get("resumeRunId") or "run-" + uuid.uuid4().hex
    state = checkpoint.resume_or_new(run_id)

    posts = _discover_posts(payload)
    pasted = _pasted_index(payload)
    start = time.monotonic()
    failures: list[str] = []

    for post in posts:
        post_id = post["postId"]
        if checkpoint.is_processed(state, post_id):
            continue

        body = _get_body(post, pasted)
        if not body:
            failures.append(post_id)  # no crawl + no paste -> skip (1.3)
            continue

        _store_raw(post_id, body)
        chunk_meta, vectors = _embed_post(post_id, body)
        checkpoint.record_post(state, post_id, chunk_meta, vectors)

        # Pause between posts if the time budget is spent (1.9).
        if checkpoint.should_pause(time.monotonic() - start):
            if _remaining(state, posts):
                checkpoint.save(state)
                return _in_progress(state, failures)

    return _complete(state, failures)


def _remaining(state: dict, posts: list[dict]) -> bool:
    """True if any discovered post is not yet processed."""
    return any(not checkpoint.is_processed(state, p["postId"]) for p in posts)


def _in_progress(state: dict, failures: list[str]) -> dict:
    """learn-style response for a paused run (resume via runId)."""
    return {
        "runId": state["runId"],
        "status": "in-progress",
        "processedPosts": len(state["processedPosts"]),
        "chunks": len(state["chunks"]),
        "styleProfileKey": None,
        "indexKey": None,
        "checkpoint": checkpoint.checkpoint_key(state["runId"]),
        "failedPosts": failures,
    }


def _load_raw(post_id: str) -> str:
    """Read a stored raw body back; '' if missing (profile source on resume)."""
    try:
        return s3store.get_bytes(f"{_RAW_PREFIX}{post_id}.txt").decode("utf-8")
    except Exception:  # noqa: BLE001 - missing raw body is tolerable
        return ""


def _complete(state: dict, failures: list[str]) -> dict:
    """All posts done: build+store profile, finalize index, clear checkpoint (1.5,1.6,1.10).

    Profile is built from every processed post's stored raw body (not just this
    invocation's), so a resumed run profiles all posts (1.6).
    """
    bodies = [b for b in (_load_raw(pid) for pid in state["processedPosts"]) if b]
    profile = style_profile.generate_profile(bodies)
    profile_key = style_profile.store_profile(profile)
    checkpoint.finalize(state)
    return {
        "runId": state["runId"],
        "status": "completed",
        "processedPosts": len(state["processedPosts"]),
        "chunks": len(state["chunks"]),
        "styleProfileKey": profile_key,
        "indexKey": index_store.INDEX_KEY,
        "checkpoint": None,
        "failedPosts": failures,
    }
