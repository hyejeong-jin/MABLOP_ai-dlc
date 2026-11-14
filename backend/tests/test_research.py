"""Unit and property coverage for research URL normalization and collection."""

from datetime import datetime, timezone

from hypothesis import given, settings, strategies as st
import pytest

import research


PROPERTY_SETTINGS = settings(max_examples=100, deadline=None)


_SAFE_HOSTS = st.sampled_from(["example.com", "docs.example.org", "news.example.net"])
_SAFE_PATHS = st.from_regex(r"/[a-z]{1,12}(?:/[a-z0-9_-]{1,12})?", fullmatch=True)


@st.composite
def normalized_url_variants(draw):
    scheme = draw(st.sampled_from(["http", "https", "HTTP", "HTTPS"]))
    host = draw(_SAFE_HOSTS)
    host_variant = draw(st.sampled_from([host, host.upper(), host.capitalize()]))
    default_port = "80" if scheme.lower() == "http" else "443"
    port = draw(st.sampled_from(["", f":{default_port}"]))
    path = draw(_SAFE_PATHS)
    query = draw(st.one_of(st.just(""), st.from_regex(r"\?[a-z]{1,8}=[a-z0-9]{1,8}", fullmatch=True)))
    fragment = draw(st.one_of(st.just(""), st.from_regex(r"#[a-z]{1,8}", fullmatch=True)))
    return f"{scheme}://{host_variant}{port}{path}{query}{fragment}"


@st.composite
def primary_links(draw, *, min_size=3, max_size=5):
    return draw(
        st.lists(
            normalized_url_variants(),
            min_size=min_size,
            max_size=max_size,
        )
    )


@st.composite
def collection_scenarios(draw):
    links = draw(primary_links())
    failures = draw(st.lists(st.booleans(), min_size=len(links), max_size=len(links)))
    return links, failures


def test_normalize_url_canonicalizes_host_fragment_and_default_port():
    assert research.normalize_url("HTTPS://Example.COM:443/articles?id=7#comments") == (
        "https://example.com/articles?id=7"
    )
    assert research.normalize_url("http://Example.COM:80") == "http://example.com"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "http://localhost/private",
        "http://127.0.0.1:80/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "https://example.com:8443/",
    ],
)
def test_normalize_url_rejects_unsafe_or_non_default_urls(url):
    with pytest.raises(research.URLValidationError):
        research.normalize_url(url)


def test_is_safe_url_is_a_non_throwing_predicate():
    assert research.is_safe_url("https://example.com/path#fragment") is True
    assert research.is_safe_url("http://10.0.0.1") is False


def test_primary_link_cardinality_rejects_before_fetching():
    with pytest.raises(research.ResearchCardinalityError):
        research.run_research(["https://example.com/one"] * 2, lambda url: "never")


def test_collect_primary_links_keeps_success_when_one_link_fails():
    requested = []

    def fetch(url):
        requested.append(url)
        if url.endswith("/bad"):
            raise OSError("upstream unavailable")
        return {"excerpt": "source text"}

    result = research.run_research(
        ["https://example.com/good", "https://example.com/bad", "https://example.com/other"],
        fetch,
    )

    assert result["status"] == "partial"
    assert len(requested) == 3
    assert [source["status"] for source in result["sources"]] == ["succeeded", "failed", "succeeded"]
    assert result["sources"][0]["excerpt"] == "source text"
    assert result["sources"][1]["errorCode"] == "OSError"
    assert result["summary"]["sourceIds"] == ["src_01", "src_03"]


def test_collect_primary_links_does_not_fetch_unsafe_url():
    fetched = []

    def fetch(url):
        fetched.append(url)
        return "unexpected"

    with pytest.raises(research.URLValidationError):
        research.collect_primary_links(
            ["https://example.com/a", "http://127.0.0.1/b", "https://example.com/c"],
            fetch,
        )
    assert fetched == []


def test_collect_primary_links_bounds_excerpt_and_preserves_normalized_url():
    sources = research.collect_primary_links(
        ["HTTPS://Example.COM:443/a#fragment", "https://example.com/b", "https://example.com/c"],
        lambda url: "0123456789",
        excerpt_limit=4,
    )
    assert sources[0]["normalizedUrl"] == "https://example.com/a"
    assert sources[0]["excerpt"] == "0123"
    assert all(source["source"] == source["sourceId"] for source in sources)


# Feature: voice-box-content-research-branding, Property 6: 검색 명시성과 횟수 상한

def test_search_provider_is_not_called_without_explicit_enabled_flag():
    calls = []

    def provider(query):
        calls.append(query)
        return [{"url": "https://search.example/result"}]

    result = research.run_research(
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        lambda url: "primary",
        search={"queries": ["ignored"]},
        search_provider=provider,
        max_search_calls=2,
    )

    assert calls == []
    assert result["searchCalls"] == 0
    assert result["searchSources"] == []
    assert result["controls"] == ["search_not_requested"]


def test_search_provider_is_called_only_when_enabled_and_is_capped():
    calls = []

    def provider(query):
        calls.append(query)
        return {"results": [{"url": "HTTPS://Results.Example:443/article#top", "title": "Result"}]}

    result = research.run_research(
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        lambda url: "primary",
        search={"enabled": True, "queries": ["one", "two", "three"]},
        search_provider=provider,
        max_search_calls=2,
    )

    assert calls == ["one", "two"]
    assert result["searchCalls"] == 2
    assert result["controls"] == ["max_search_calls_reached"]
    assert len(result["searchSources"]) == 2
    assert all(source["sourceType"] == "web-search" for source in result["searchSources"])
    assert [source["query"] for source in result["searchSources"]] == ["one", "two"]
    assert result["searchSources"][0]["collectedAt"]
    assert result["searchSources"][0]["url"] == "https://results.example/article"


def test_search_provider_failures_are_recorded_without_network_access():
    calls = []

    def provider(query):
        calls.append(query)
        raise OSError("provider unavailable")

    result = research.run_research(
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        lambda url: "primary",
        search={"enabled": True, "queries": ["one"]},
        search_provider=provider,
        max_search_calls=1,
    )

    assert calls == ["one"]
    assert result["searchCalls"] == 1
    assert result["searchSources"][0]["sourceType"] == "web-search"
    assert result["searchSources"][0]["status"] == "failed"
    assert result["searchSources"][0]["errorCode"] == "OSError"


def test_cost_budget_stops_stages_before_fetching_when_requests_are_exhausted():
    fetched = []

    result = research.run_research(
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        lambda url: fetched.append(url) or "source",
        budget={"maxRequests": 0, "maxInputChars": 100},
    )

    assert fetched == []
    assert result["status"] == "budget-exhausted"
    assert "maxRequests_reached" in result["controls"]
    assert all(source["status"] == "skipped" for source in result["sources"])


def test_cost_budget_limits_input_chars_before_each_collection_stage():
    fetched = []
    result = research.run_research(
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        lambda url: fetched.append(url) or "0123456789",
        excerpt_limit=10,
        budget={"maxRequests": 3, "maxInputChars": 10},
    )

    assert fetched == ["https://example.com/a"]
    assert result["usage"]["inputChars"] == 10
    assert result["status"] == "partial"
    assert "maxInputChars_reached" in result["controls"]


def test_time_guard_stops_before_the_next_stage():
    now = [0.0]
    fetched = []

    def monotonic():
        return now[0]

    def fetch(url):
        fetched.append(url)
        now[0] = 2.0
        return "source"

    result = research.run_research(
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        fetch,
        budget={"maxRequests": 3, "maxInputChars": 100},
        max_runtime_s=1.0,
        monotonic=monotonic,
    )

    assert fetched == ["https://example.com/a"]
    assert result["status"] == "partial"
    assert "time_guard_reached" in result["controls"]


# Feature: voice-box-content-research-branding, Property 4: 링크 집합 cardinality와 source 기록


@PROPERTY_SETTINGS
@given(links=st.lists(normalized_url_variants(), min_size=0, max_size=7))
@pytest.mark.property
def test_property_primary_links_only_accept_three_to_five_and_record_each_source(links):
    """**Validates: Requirements 4.1, 4.2, 4.3**"""
    fetched = []
    collected_at = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def fetch(url):
        fetched.append(url)
        return {"excerpt": "content"}

    if not 3 <= len(links) <= 5:
        with pytest.raises(research.ResearchCardinalityError):
            research.run_research(links, fetch, clock=lambda: collected_at)
        assert fetched == []
        return

    result = research.run_research(links, fetch, clock=lambda: collected_at)

    assert len(result["sources"]) == len(links)
    assert len(fetched) == len(links)
    for index, source in enumerate(result["sources"], start=1):
        assert source["sourceId"] == f"src_{index:02d}"
        assert source["source"] == source["sourceId"]
        assert source["status"] == "succeeded"
        assert source["normalizedUrl"] == source["url"] == fetched[index - 1]
        assert source["collectedAt"] == collected_at.isoformat()
    assert result["summary"]["sourceIds"] == [source["sourceId"] for source in result["sources"]]


# Feature: voice-box-content-research-branding, Property 4: URL normalize


@PROPERTY_SETTINGS
@given(url=normalized_url_variants())
@pytest.mark.property
def test_property_url_normalization_is_canonical_and_fragment_free(url):
    """**Validates: Requirements 4.2, 7.2**"""
    normalized = research.normalize_url(url)
    parsed = research.validate_url(url)

    assert normalized == normalized.lower()
    assert "#" not in normalized
    assert research.normalize_url(normalized) == normalized
    assert parsed.fragment == ""
    assert parsed.scheme in {"http", "https"}
    assert parsed.hostname == parsed.hostname.lower()


# Feature: voice-box-content-research-branding, Property 5: 부분 수집 보존


@PROPERTY_SETTINGS
@given(scenario=collection_scenarios())
@pytest.mark.property
def test_property_partial_collection_preserves_successes_and_failure_reasons(scenario):
    """**Validates: Requirements 4.5, 5.5, 7.5**"""
    links, failures = scenario
    requested = []

    def fetch(url):
        index = len(requested)
        requested.append(url)
        if failures[index]:
            raise OSError(f"failed-{index}")
        return {"excerpt": f"success-{index}"}

    result = research.run_research(links, fetch)
    sources = result["primarySources"]
    successful = [source for index, source in enumerate(sources) if not failures[index]]
    failed = [source for index, source in enumerate(sources) if failures[index]]

    assert len(requested) == len(links)
    assert all(source["status"] == "succeeded" for source in successful)
    assert [source["excerpt"] for source in successful] == [
        f"success-{index}" for index, failed_at in enumerate(failures) if not failed_at
    ]
    assert all(source["status"] == "failed" for source in failed)
    assert all(source["errorCode"] == "OSError" for source in failed)
    assert all(source["errorMessage"].startswith("failed-") for source in failed)
    assert result["summary"]["sourceIds"] == [source["sourceId"] for source in successful]
    if successful and failed:
        assert result["status"] == "partial"
        assert result["summary"]["partial"] is True
    elif successful:
        assert result["status"] == "completed"
    else:
        assert result["status"] == "failed"


# ---- Task 5.3: cache key, TTL and fingerprint checks ----

from datetime import timedelta

_LINKS = ["https://A.example/x#f", "https://b.example/y", "https://c.example:443/z"]


def test_cache_key_ignores_order_spelling_and_depends_on_settings_and_budget():
    base = research.build_cache_key(_LINKS, {"length": "short"}, {"maxRequests": 5})
    shuffled = research.build_cache_key(
        ["https://c.example/z", "https://b.example/y", "https://a.example/x"], {"length": "short"}, {"maxRequests": 5}
    )
    assert base == shuffled and base.startswith("sha256:")
    assert base != research.build_cache_key(_LINKS, {"length": "long"}, {"maxRequests": 5})
    assert base != research.build_cache_key(_LINKS, {"length": "short"}, {"maxRequests": 6})
    with pytest.raises(research.URLValidationError):
        research.build_cache_key(["http://localhost/x"], {}, {})


def test_cache_entry_expiry_and_fingerprint_change():
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    entry = research.build_cache_entry("sha256:" + "a" * 64, "run_x", {"https://a.example/x": "f1"}, ttl_s=60, now=now)
    assert entry["expiresAt"] == "2025-01-01T00:01:00.000Z"
    assert research.cache_entry_valid(entry, now=now + timedelta(seconds=59))
    assert research.cache_entry_valid(entry, now=now, current_fingerprints={"https://a.example/x": "f1"})
    assert not research.cache_entry_valid(entry, now=now + timedelta(seconds=60))
    assert not research.cache_entry_valid(entry, now=now, current_fingerprints={"https://a.example/x": "f2"})
    assert not research.cache_entry_valid(None, now=now)


def test_lookup_cache_and_usage_merge_keep_existing_fields():
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    entry = research.build_cache_entry("sha256:" + "b" * 64, "run_y", ttl_s=60)
    assert research.lookup_cache("k", lambda key: None) is None
    assert research.lookup_cache("k", lambda key: entry)["runId"] == "run_y"
    usage = research.merge_research_usage({"requests": 3, "custom": 7}, cache_hit=True)
    assert usage["custom"] == 7 and usage["requests"] == 3 and usage["cacheHits"] == 1 and usage["cacheMisses"] == 0
    assert research.source_fingerprint(" a  b ") == research.source_fingerprint("a b")
