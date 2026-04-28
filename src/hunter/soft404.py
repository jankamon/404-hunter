"""Soft-404 detection: pages that return 200 but actually mean 'not found'."""
from __future__ import annotations

import hashlib
import re
import secrets
from urllib.parse import urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from .fetcher import Fetcher


# Strong phrases — match anywhere (title, h1, body). High signal, low false-positive rate.
STRONG_PATTERNS = [
    # English
    re.compile(r"page (was )?not found", re.I),
    re.compile(r"page (?:doesn'?t|does not) exist", re.I),
    re.compile(r"couldn'?t find (?:the |that )?page", re.I),
    re.compile(r"this page can'?t be found", re.I),
    # Polish
    re.compile(r"\bnie (?:znaleziono|odnaleziono) strony\b", re.I),
    re.compile(r"\bstrona nie (?:istnieje|została (?:znaleziona|odnaleziona))\b", re.I),
    re.compile(r"\b(?:ta |podana )?strona nie (?:znaleziona|odnaleziona)\b", re.I),
    re.compile(r"\bnie ma takiej strony\b", re.I),
    re.compile(r"\bszukana strona nie (?:istnieje|została)", re.I),
    re.compile(r"\bpodanej strony nie (?:znaleziono|odnaleziono)\b", re.I),
    re.compile(r"\bstrona o podanym adresie\b", re.I),
    re.compile(r"\bstrona, kt[óo]r[aą] (?:wyszukujesz|szukasz)\b", re.I),
    re.compile(r"\bb[łl][ąa]d 404\b", re.I),
    # German
    re.compile(r"\bseite nicht gefunden\b", re.I),
    # Spanish
    re.compile(r"\bp[áa]gina no encontrada\b", re.I),
]

# Weak signals — only count if found in <title> or <h1>. The bare word "404" or
# "not found" appears constantly in legitimate body text (sitemaps, FAQs, blog posts).
WEAK_PATTERNS = [
    re.compile(r"\b404\b"),
    re.compile(r"\bnot found\b", re.I),
    re.compile(r"\bnie znaleziono\b", re.I),
    re.compile(r"\bnie odnaleziono\b", re.I),
    re.compile(r"\bnie istnieje\b", re.I),
]


class Soft404Detector:
    """Detects soft-404s by combining content heuristics with a probe fingerprint."""

    def __init__(self) -> None:
        # host -> hash of the response body for a known-bogus URL on that host
        self._probe_hashes: dict[str, str] = {}

    async def calibrate(self, fetcher: Fetcher, seed_url: str) -> None:
        """Fetch a deliberately-bogus URL on the seed's host and remember its body hash."""
        parts = urlsplit(seed_url)
        bogus_path = f"/__hunter_probe_{secrets.token_hex(8)}__"
        bogus = urlunsplit((parts.scheme, parts.netloc, bogus_path, "", ""))
        result = await fetcher.get(bogus)
        if result.status_code == 200 and result.body:
            host = (parts.hostname or "").lower()
            self._probe_hashes[host] = _fingerprint(result.body)

    def is_soft_404(self, url: str, body: bytes, status_code: int) -> tuple[bool, str]:
        """Return (is_soft_404, reason). Only inspects 200-OK HTML."""
        if status_code != 200 or not body:
            return False, ""

        host = (urlsplit(url).hostname or "").lower()
        probe = self._probe_hashes.get(host)
        if probe and _fingerprint(body) == probe:
            return True, "probe-match"

        if _heuristic_match(body):
            return True, "heuristic-match"

        return False, ""


def _fingerprint(body: bytes) -> str:
    """Stable-ish hash of a page body. Strips noisy whitespace before hashing."""
    text = body[:200_000].decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _heuristic_match(body: bytes) -> bool:
    try:
        tree = HTMLParser(body[:200_000].decode("utf-8", errors="replace"))
    except Exception:
        return False

    title = ""
    title_node = tree.css_first("title")
    if title_node is not None:
        title = title_node.text(strip=True) or ""

    h1 = ""
    h1_node = tree.css_first("h1")
    if h1_node is not None:
        h1 = h1_node.text(strip=True) or ""

    text_sample = ""
    body_node = tree.body
    if body_node is not None:
        text_sample = (body_node.text(deep=True, separator=" ", strip=True) or "")[:2000]

    head_haystack = f"{title} {h1}".strip()
    full_haystack = f"{title} {h1} {text_sample}".strip()

    if not full_haystack:
        return False

    for pattern in STRONG_PATTERNS:
        if pattern.search(full_haystack):
            return True

    if head_haystack:
        for pattern in WEAK_PATTERNS:
            if pattern.search(head_haystack):
                return True

    return False
