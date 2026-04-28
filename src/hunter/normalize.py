"""URL canonicalization and scope checks."""
from __future__ import annotations

import fnmatch
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urldefrag,
    urljoin,
    urlsplit,
    urlunsplit,
)

SKIP_SCHEMES = frozenset({"mailto", "tel", "javascript", "data", "ftp", "sms", "file"})
DEFAULT_PORTS = {"http": 80, "https": 443}

NON_HTML_EXTENSIONS = frozenset(
    {
        ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
        ".mp3", ".mp4", ".wav", ".mov", ".avi", ".mkv", ".webm", ".ogg",
        ".css", ".js", ".map",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
    }
)


def normalize(url: str, base: str | None = None) -> str | None:
    """Return a canonical absolute URL, or None if it should be skipped.

    Rules:
      - resolve against base if relative
      - lowercase scheme and host
      - drop default port
      - drop fragment
      - sort query params (case-sensitive values preserved)
      - skip non-http(s) schemes (mailto, javascript, ...)
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None

    if base:
        url = urljoin(base, url)

    url, _ = urldefrag(url)
    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    if scheme in SKIP_SCHEMES:
        return None
    if scheme not in {"http", "https"}:
        return None
    if not parts.netloc:
        return None

    host = parts.hostname or ""
    host = host.lower()
    port = parts.port
    if port and port == DEFAULT_PORTS.get(scheme):
        port = None

    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += ":" + parts.password
        userinfo += "@"

    netloc = userinfo + host + (f":{port}" if port else "")

    path = parts.path or "/"
    path = _normalize_path(path)

    query = ""
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        pairs.sort()
        query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs)

    return urlunsplit((scheme, netloc, path, query, ""))


def _normalize_path(path: str) -> str:
    """Resolve `.` and `..` per RFC 3986 and re-encode unreserved chars consistently.

    Trailing slashes are preserved (they're meaningful on most servers).
    """
    raw = path.split("/")
    has_trailing_slash = path.endswith("/") and len(raw) > 1
    segments: list[str] = []
    for seg in raw:
        if seg == "" or seg == ".":
            continue
        if seg == "..":
            if segments and segments[-1] != "..":
                segments.pop()
            else:
                segments.append(seg)
        else:
            segments.append(quote(unquote(seg), safe="-._~!$&'()*+,;=:@"))
    out = "/" + "/".join(segments)
    if has_trailing_slash and out != "/":
        out += "/"
    return out


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_same_site(url: str, seed_host: str, scope: str = "host") -> bool:
    """Return True if `url` should be considered part of the same site as `seed_host`."""
    h = host_of(url)
    if not h:
        return False
    if scope == "host":
        return h == seed_host
    if scope == "domain":
        return h == seed_host or h.endswith("." + _registrable(seed_host))
    return False


def _registrable(host: str) -> str:
    """Naive registrable-domain extractor (last two labels). Good enough for `--scope domain`
    on most TLDs; users with multi-part TLDs (.co.uk) should use --scope host."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def looks_non_html(url: str) -> bool:
    """Cheap pre-filter: does the URL path end with a known non-HTML extension?"""
    path = urlsplit(url).path.lower()
    if "." not in path.rsplit("/", 1)[-1]:
        return False
    for ext in NON_HTML_EXTENSIONS:
        if path.endswith(ext):
            return True
    return False


def matches_any(path: str, patterns: list[str]) -> bool:
    """Return True if `path` matches any glob pattern."""
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"
