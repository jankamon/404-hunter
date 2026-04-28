"""End-to-end-ish crawler tests using pytest-httpx for HTTP mocking."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from hunter.crawler import Crawler, CrawlConfig, derive_seed_host
from hunter.fetcher import Fetcher, HostRateLimiter, make_client
from hunter.soft404 import Soft404Detector
from hunter.state import Store


def page(html: str) -> dict:
    return {
        "status_code": 200,
        "headers": {"content-type": "text/html; charset=utf-8"},
        "content": html.encode("utf-8"),
    }


async def _make_crawler(tmp_path: Path, **overrides):
    seed = overrides.pop("seed", "https://example.com/")
    store = Store(tmp_path / "state.db")
    limiter = HostRateLimiter(min_delay=overrides.pop("delay", 0.0))
    client = make_client(
        timeout=5.0,
        user_agent="test/1.0",
        auth=None,
        extra_headers=None,
        verify=True,
        concurrency=overrides.pop("concurrency", 5),
    )
    fetcher = Fetcher(client=client, rate_limiter=limiter)
    cfg = CrawlConfig(
        seed_url=seed,
        seed_host=derive_seed_host(seed),
        scope="host",
        concurrency=overrides.pop("concurrency_cfg", 5),
        max_pages=overrides.pop("max_pages", 100),
        check_external=overrides.pop("check_external", True),
        soft_404=overrides.pop("soft_404", True),
        include=overrides.pop("include", None),
        exclude=overrides.pop("exclude", None),
        verbose=False,
    )
    crawler = Crawler(
        config=cfg,
        fetcher=fetcher,
        store=store,
        soft404_detector=Soft404Detector(),
    )
    return crawler, store, client


@pytest.mark.asyncio
async def test_plain_404(httpx_mock, tmp_path):
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=404,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="/missing">go</a>'),
    )
    httpx_mock.add_response(url="https://example.com/missing", status_code=404)

    crawler, store, client = await _make_crawler(tmp_path)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    # Re-open to read findings
    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    assert len(findings) == 1
    f = findings[0]
    assert f.status == "404"
    assert f.url == "https://example.com/missing"
    assert f.source_page == "https://example.com/"
    assert f.link_text == "go"


@pytest.mark.asyncio
async def test_redirect_to_404(httpx_mock, tmp_path):
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=404,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="/old">go</a>'),
    )
    httpx_mock.add_response(
        url="https://example.com/old",
        status_code=301,
        headers={"location": "/new"},
    )
    httpx_mock.add_response(url="https://example.com/new", status_code=404)

    crawler, store, client = await _make_crawler(tmp_path)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    assert len(findings) == 1
    assert findings[0].status == "404"
    assert "https://example.com/new" in findings[0].redirect_chain or findings[0].final_url.endswith("/new")


@pytest.mark.asyncio
async def test_soft_404_heuristic(httpx_mock, tmp_path):
    import re

    # Probe returns 200 with a unique body so it doesn't accidentally match.
    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=200,
        headers={"content-type": "text/html"},
        content=b"<html><body>unique probe body xyzzy</body></html>",
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="/old">old</a>'),
    )
    httpx_mock.add_response(
        url="https://example.com/old",
        status_code=200,
        headers={"content-type": "text/html"},
        content=b"<html><head><title>Page Not Found</title></head><body>Sorry.</body></html>",
    )

    crawler, store, client = await _make_crawler(tmp_path)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    assert len(findings) == 1
    assert findings[0].status == "soft-404"
    assert findings[0].error == "heuristic-match"


@pytest.mark.asyncio
async def test_soft_404_polish(httpx_mock, tmp_path):
    """Polish 'page not found' phrases should be detected."""
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=200,
        headers={"content-type": "text/html"},
        content=b"<html><body>unique probe body xyzzy</body></html>",
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="/old">stara</a>'),
    )
    httpx_mock.add_response(
        url="https://example.com/old",
        status_code=200,
        headers={"content-type": "text/html"},
        content=(
            "<html><head><title>Sklep</title></head>"
            "<body><h1>Witaj</h1>"
            "<p>Niestety, podanej strony nie znaleziono.</p>"
            "</body></html>"
        ).encode("utf-8"),
    )

    crawler, store, client = await _make_crawler(tmp_path)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    assert len(findings) == 1
    assert findings[0].status == "soft-404"
    assert findings[0].url == "https://example.com/old"


@pytest.mark.asyncio
async def test_external_head_check(httpx_mock, tmp_path):
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=404,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="https://outside.test/dead">x</a>'),
    )
    # External link returns 404 to a HEAD
    httpx_mock.add_response(
        url="https://outside.test/dead",
        method="HEAD",
        status_code=404,
    )

    crawler, store, client = await _make_crawler(tmp_path)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    assert len(findings) == 1
    assert findings[0].url == "https://outside.test/dead"
    assert findings[0].status == "404"


@pytest.mark.asyncio
async def test_external_403_is_blocked_not_403(httpx_mock, tmp_path):
    """External 403 (likely WAF) gets bucketed as 'blocked', not as a hard failure."""
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=404,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="https://outside.test/protected">x</a>'),
    )
    httpx_mock.add_response(
        url="https://outside.test/protected",
        method="HEAD",
        status_code=403,
    )

    crawler, store, client = await _make_crawler(tmp_path)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    assert len(findings) == 1
    assert findings[0].url == "https://outside.test/protected"
    assert findings[0].status == "blocked"
    assert "403" in findings[0].error


@pytest.mark.asyncio
async def test_internal_403_stays_403(httpx_mock, tmp_path):
    """Internal 403 is a real signal (perm/config issue), not anti-bot — keep numeric label."""
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=404,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="/forbidden">x</a>'),
    )
    httpx_mock.add_response(url="https://example.com/forbidden", status_code=403)

    crawler, store, client = await _make_crawler(tmp_path)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    assert len(findings) == 1
    assert findings[0].url == "https://example.com/forbidden"
    assert findings[0].status == "403"


@pytest.mark.asyncio
async def test_max_pages_halts(httpx_mock, tmp_path):
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=404,
        is_reusable=True,
    )
    # Each page links to many siblings — without a cap this would explode.
    body = "".join(f'<a href="/p{i}">x</a>' for i in range(50))
    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/(p\d+)?"),
        status_code=200,
        headers={"content-type": "text/html"},
        content=body.encode(),
        is_reusable=True,
    )

    crawler, store, client = await _make_crawler(tmp_path, max_pages=5)
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    seen = store2.stats()["seen"]
    store2.close()

    # Allow a small overshoot from inflight workers, but it must be bounded.
    assert seen <= 5 + 5  # cap + concurrency-worth of in-flight tolerance


@pytest.mark.asyncio
async def test_rate_limiter_enforces_min_gap(tmp_path):
    """Direct test of the rate limiter — independent of HTTP mocks."""
    limiter = HostRateLimiter(min_delay=0.2)
    timestamps = []

    async def hit():
        await limiter.wait("example.com")
        timestamps.append(time.monotonic())

    start = time.monotonic()
    await asyncio.gather(*[hit() for _ in range(4)])
    elapsed = time.monotonic() - start
    # 4 requests at 0.2s gap = at least 0.6s for the 2nd, 3rd, 4th to wait
    assert elapsed >= 0.55, f"rate limiter let through too fast: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_exclude_skips_path(httpx_mock, tmp_path):
    import re

    httpx_mock.add_response(
        url=re.compile(r"https://example\.com/__hunter_probe_[0-9a-f]+__"),
        status_code=404,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://example.com/",
        **page('<a href="/admin/secret">x</a><a href="/blog/post">y</a>'),
    )
    httpx_mock.add_response(
        url="https://example.com/blog/post",
        status_code=404,
    )
    # /admin/secret should never be requested

    crawler, store, client = await _make_crawler(tmp_path, exclude=["/admin/*"])
    try:
        await crawler.run(resume=False)
    finally:
        await client.aclose()
        store.close()

    store2 = Store(tmp_path / "state.db")
    findings = store2.all_findings()
    store2.close()

    urls = [f.url for f in findings]
    assert "https://example.com/blog/post" in urls
    assert "https://example.com/admin/secret" not in urls
