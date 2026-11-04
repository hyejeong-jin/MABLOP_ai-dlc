"""HTTP crawl + HTML->text body extraction (Req 1.2, 1.7). Stdlib only."""

import html
import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit
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


def discover_naver_posts(blog_url: str, max_posts: int = 20) -> list[dict]:
    """Discover public Naver posts from the official public RSS feed.

    RSS is a public feed. This does not bypass login, robots, or access
    controls. Each item returns a post URL and a short public description that
    can be used only as a crawl-failure fallback.
    """
    path = urlsplit(blog_url.strip()).path.strip("/")
    blog_id = path.split("/", 1)[0] if path else ""
    if not blog_id:
        return []
    rss = crawl(f"https://rss.blog.naver.com/{blog_id}.xml")
    root = ElementTree.fromstring(rss)
    posts = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        description = html.unescape(item.findtext("description") or "").strip()
        posts.append({
            "url": link,
            "title": (item.findtext("title") or "").strip(),
            "fallbackText": extract_body(description) if description else "",
        })
        if len(posts) >= max_posts:
            break
    return posts

