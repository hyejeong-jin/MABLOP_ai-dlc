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
from errors import MablopError

_MOBILE_HOST = "m.blog.naver.com"

_MAX_POST_COUNT_ENV = "MABLOP_MAX_POST_COUNT"  # cap per run (Req 1.8)
_DEFAULT_MAX_POST_COUNT = 20

_RAW_PREFIX = "raw-posts/"  # extracted/pasted bodies (Req 1.7)
_STATUS_KEY = "metadata/learning-status.json"


def resolve_url(blog_url: str) -> str:
    """Normalize a Naver post URL to the mobile host. Pure, no network (1.1).

    Accepts desktop `blog.naver.com/{blogId}/{logNo}`, the `?blogId=&logNo=`
    query form, `PostView.naver?blogId=&logNo=`, and the mobile host. Returns
    `https://m.blog.naver.com/{blogId}/{logNo}`. Raises ValueError if the
    blogId/logNo pair cannot be extracted.
    """
    parts = urlsplit(blog_url.strip())
    if parts.scheme in {"http", "https"} and parts.netloc and "naver.com" not in parts.netloc.lower():
        return blog_url.strip()
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

    explicit_posts = payload.get("posts") or []
    if not explicit_posts and payload.get("blogUrl"):
        try:
            explicit_posts = crawler.discover_posts(
                payload["blogUrl"], _max_post_count()
            )
        except Exception:
            explicit_posts = []

    for i, raw in enumerate(explicit_posts):
        if isinstance(raw, str):
            url, given_id, fallback = raw, None, ""
        else:
            url, given_id = raw.get("url"), raw.get("postId")
            fallback = raw.get("fallbackText", "")
        post_id = given_id or _post_id_from_url(url) or f"p{i}"
        if url or fallback:
            seen.setdefault(post_id, {"postId": post_id, "url": url, "fallbackText": fallback})

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
    return pasted.get(post["postId"], "") or post.get("fallbackText", "")


def _store_raw(post_id: str, body: str) -> None:
    """Persist the raw body under raw-posts/<postId>.txt (1.7)."""
    s3store.put_bytes(s3store.user_key(f"{_RAW_PREFIX}{post_id}.txt"), body.encode("utf-8"),
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

    pasted = _pasted_index(payload)
    # Normal UI flow: pasted rows are new raw posts. Store them first, then
    # rebuild from every raw post so previous learning is never overwritten.
    if pasted and not payload.get("posts") and not payload.get("resumeRunId"):
        for post_id, body in pasted.items():
            _store_raw(post_id, body)
        return rebuild_from_stored()

    posts = _discover_posts(payload)
    if not posts:
        raise MablopError(
            "bad_request",
            "\ud559\uc2b5\ud560 \ud3ec\uc2a4\ud2b8\uac00 \uc5c6\uc2b5\ub2c8\ub2e4. \ub124\uc774\ubc84 \uc790\ub3d9 \uc218\uc9d1\uc774 \uc2e4\ud328\ud558\uba74 \ubd99\uc5ec\ub123\uae30 \ud589\uc744 \ucd94\uac00\ud558\uace0 postId\uc640 \ubcf8\ubb38\uc744 \uc785\ub825\ud558\uc138\uc694.",
        )
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
        return s3store.get_bytes(s3store.user_key(f"{_RAW_PREFIX}{post_id}.txt")).decode("utf-8")
    except Exception:  # noqa: BLE001 - missing raw body is tolerable
        return ""


def _learning_status(state: dict) -> dict:
    """Build the current workspace learning summary from checkpoint state."""
    counts = {}
    for chunk in state.get("chunks", []):
        post_id = chunk.get("postId")
        if post_id:
            counts[post_id] = counts.get(post_id, 0) + 1
    return {
        "runId": state["runId"],
        "updatedAt": state.get("updatedAt"),
        "processedPosts": len(state.get("processedPosts", [])),
        "chunks": len(state.get("chunks", [])),
        "posts": [
            {"postId": post_id, "chunkCount": counts.get(post_id, 0)}
            for post_id in state.get("processedPosts", [])
        ],
    }


def rebuild_from_stored() -> dict:
    """Rebuild the RAG index/profile from all raw-posts already in S3."""
    prefix = s3store.user_key(_RAW_PREFIX)
    keys = [obj["key"] for obj in s3store.list_keys(prefix) if obj["key"].endswith(".txt")]
    if not keys:
        raise MablopError("bad_request", "저장된 raw-posts가 없습니다. 먼저 스타일 학습을 진행하세요.")

    state = checkpoint.new_state("rebuild-" + uuid.uuid4().hex)
    for key in sorted(keys):
        post_id = key[len(prefix):-4]
        body = s3store.get_bytes(key).decode("utf-8")
        chunk_meta, vectors = _embed_post(post_id, body)
        checkpoint.record_post(state, post_id, chunk_meta, vectors)
    return _complete(state, [])



def get_learning_post(body: dict) -> dict:
    """Return one stored learned post body for the acting user."""
    post_id = body.get("postId") if isinstance(body, dict) else None
    if not isinstance(post_id, str) or not post_id or "/" in post_id or ".." in post_id or "\\" in post_id:
        raise MablopError("bad_request", "올바르지 않은 포스트 번호입니다.")
    key = s3store.user_key(f"{_RAW_PREFIX}{post_id}.txt")
    try:
        text = s3store.get_bytes(key).decode("utf-8")
    except Exception as exc:
        raise MablopError("not_found", "학습 포스트를 찾을 수 없습니다.") from exc
    return {"postId": post_id, "text": text}


def rename_learning_post(body: dict) -> dict:
    """Rename a learned post id/title and keep the stored raw text and index metadata aligned."""
    old_id = body.get("postId") if isinstance(body, dict) else None
    new_id = body.get("newPostId") if isinstance(body, dict) else None
    valid = lambda value: isinstance(value, str) and value.strip() and "/" not in value and ".." not in value and "\\" not in value
    if not valid(old_id) or not valid(new_id):
        raise MablopError("bad_request", "포스트 제목은 안전한 문자로 입력해주세요.")
    old_key = s3store.user_key(f"{_RAW_PREFIX}{old_id}.txt")
    new_key = s3store.user_key(f"{_RAW_PREFIX}{new_id}.txt")
    if old_id != new_id and s3store.key_exists(new_key):
        raise MablopError("bad_request", "이미 같은 제목의 포스트가 있습니다.")
    try:
        text = s3store.get_bytes(old_key)
    except Exception as exc:
        raise MablopError("not_found", "학습 포스트를 찾을 수 없습니다.") from exc
    s3store.put_bytes(new_key, text, content_type="text/plain; charset=utf-8")
    if old_key != new_key:
        s3store.delete(old_key)
    found = s3store.read_json_optional(s3store.user_key(_STATUS_KEY))
    if found is not None:
        status = dict(found[0])
        status["posts"] = [{**p, "postId": new_id if p.get("postId") == old_id else p.get("postId"), "title": new_id if p.get("postId") == old_id else p.get("title", p.get("postId"))} for p in status.get("posts", [])]
        s3store.put_json(s3store.user_key(_STATUS_KEY), status)
    index = s3store.read_json_optional(s3store.user_key("embeddings/index-map.json"))
    if index is not None:
        mapping = {}
        for row, value in index[0].items():
            entry = dict(value)
            if entry.get("postId") == old_id:
                entry["postId"] = new_id
                entry["chunkId"] = entry.get("chunkId", "").replace(f"{old_id}-", f"{new_id}-", 1)
            mapping[row] = entry
        s3store.put_json(s3store.user_key("embeddings/index-map.json"), mapping)
    return {"postId": new_id, "renamed": True}
def preview_learning_sources(payload: dict) -> dict:
    """Preview public RSS-discovered posts without writing data or calling Bedrock."""
    payload = payload or {}
    posts = _discover_posts(payload)
    return {
        "count": len(posts),
        "posts": [
            {
                "postId": post["postId"],
                "url": post.get("url"),
                "title": post.get("title", ""),
                "hasRssFallback": bool(post.get("fallbackText")),
            }
            for post in posts
        ],
    }


def get_learning_status() -> dict:
    """Return the acting user's last completed learning summary (empty when absent)."""
    found = s3store.read_json_optional(s3store.user_key(_STATUS_KEY))
    if found is None:
        return {"processedPosts": 0, "chunks": 0, "posts": []}
    return found[0]


def _complete(state: dict, failures: list[str]) -> dict:
    """All posts done: build+store profile, finalize index, clear checkpoint (1.5,1.6,1.10).

    Profile is built from every processed post's stored raw body (not just this
    invocation's), so a resumed run profiles all posts (1.6).
    """
    bodies = [b for b in (_load_raw(pid) for pid in state["processedPosts"]) if b]
    profile = style_profile.generate_profile(bodies)
    profile_key = style_profile.store_profile(profile)
    checkpoint.finalize(state)
    learning_status = _learning_status(state)
    learning_status["updatedAt"] = profile.get("builtAt")
    s3store.put_json(s3store.user_key(_STATUS_KEY), learning_status)
    return {
        "runId": state["runId"],
        "status": "completed",
        "processedPosts": len(state["processedPosts"]),
        "chunks": len(state["chunks"]),
        "styleProfileKey": profile_key,
        "indexKey": s3store.user_key(index_store.INDEX_KEY),
        "checkpoint": None,
        "failedPosts": failures,
        "learningStatus": learning_status,
    }
