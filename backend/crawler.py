"""HTTP crawl + HTML->text body extraction (Req 1.2, 1.7). Stdlib only."""

import html
import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from xml.etree import ElementTree
from urllib.error import URLError
from urllib.request import Request

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TIMEOUT = 10  # seconds
# Naver mobile post body container id.
_NAVER_BODY_ID = "postViewArea"
# Tags whose text content is never body text.
_SKIP_TAGS = {"script", "style", "head", "noscript", "template"}
_WS = re.compile(r"\s+")


class CrawlError(Exception):
    """Per-post crawl failure. Pipeline catches -> paste fallback (1.3)."""


def crawl(url: str) -> str:
    """HTTP GET, return HTML text. Raise CrawlError on failure (1.2)."""
    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except (URLError, OSError, ValueError) as exc:
        raise CrawlError(f"crawl failed for {url!r}: {exc}") from exc
    return raw.decode(charset, errors="replace")


def extract_body(html: str) -> str:
    """HTML -> plain post-body text, no markup, whitespace collapsed (1.2)."""
    parser = _BodyExtractor()
    parser.feed(html)
    parser.close()
    text = parser.text()
    return _WS.sub(" ", text).strip()


class _BodyExtractor(HTMLParser):
    """Strip tags/scripts/styles. Prefer the Naver body container if present."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._all: list[str] = []
        # Naver target capture: collect text only inside the body container.
        self._target: list[str] = []
        self._target_depth = 0  # >0 while inside the container
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._target_depth == 0 and _is_naver_body(attrs):
            self._target_depth = self._depth

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        # Close the container when its own open tag's depth is unwound.
        if self._target_depth and self._depth <= self._target_depth:
            self._target_depth = 0
        self._depth -= 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._all.append(data)
        if self._target_depth:
            self._target.append(data)

    def text(self) -> str:
        """Container text if we found one, else whole-document fallback."""
        return "".join(self._target) if self._target else "".join(self._all)


def _is_naver_body(attrs) -> bool:
    """True if a tag's id marks the Naver mobile post body."""
    for name, value in attrs:
        if name == "id" and value and _NAVER_BODY_ID in value:
            return True
    return False


def _feed_candidates(blog_url: str) -> list[str]:
    """Return public feed candidates for Naver, Tistory, Velog, and common RSS sites."""
    parsed = urlsplit(blog_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return []
    base = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc.lower().split(":", 1)[0]
    path = parsed.path.rstrip("/")
    candidates = []
    if host.endswith("naver.com"):
        blog_id = next((part for part in path.split("/") if part), "")
        if blog_id:
            candidates.append(f"https://rss.blog.naver.com/{blog_id}.xml")
    elif host.endswith("tistory.com"):
        candidates.extend([urljoin(base + path + "/", "rss"), urljoin(base + path + "/", "feed")])
    elif host in {"velog.io", "www.velog.io"}:
        user = next((part[1:] for part in path.split("/") if part.startswith("@")), "")
        if user:
            candidates.extend([f"https://v2.velog.io/rss/@{user}", f"https://velog.io/@{user}/rss"])
    candidates.extend([urljoin(base + path + "/", "feed"), urljoin(base + path + "/", "rss.xml"), urljoin(base + path + "/", "atom.xml")])
    return list(dict.fromkeys(candidates))


def _discover_from_feed(feed: str, max_posts: int = 20) -> list[dict]:
    """Parse RSS 2.0 or Atom and return public post links with description fallback."""
    root = ElementTree.fromstring(feed)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{*}entry")
    posts = []
    for item in items:
        link = (item.findtext("link") or "").strip()
        if not link:
            link_node = item.find("{*}link")
            link = (link_node.attrib.get("href", "") if link_node is not None else "").strip()
        if not link:
            continue
        description = item.findtext("description") or item.findtext("{*}summary") or item.findtext("{*}content") or ""
        title = item.findtext("title") or item.findtext("{*}title") or ""
        posts.append({"url": link, "title": html.unescape(title).strip(), "fallbackText": extract_body(html.unescape(description)) if description else ""})
        if len(posts) >= max_posts:
            break
    return posts


def discover_posts(blog_url: str, max_posts: int = 20) -> list[dict]:
    """Discover public posts from common public feeds; no login or access-control bypass."""
    for feed_url in _feed_candidates(blog_url):
        try:
            return _discover_from_feed(crawl(feed_url), max_posts)
        except (CrawlError, ElementTree.ParseError, ValueError):
            continue
    return []


def discover_naver_posts(blog_url: str, max_posts: int = 20) -> list[dict]:
    """Backward-compatible Naver discovery wrapper."""
    return discover_posts(blog_url, max_posts)