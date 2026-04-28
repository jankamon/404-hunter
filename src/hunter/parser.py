"""HTML link extraction."""
from __future__ import annotations

from dataclasses import dataclass

from selectolax.parser import HTMLParser

from .normalize import normalize


@dataclass(frozen=True)
class Link:
    url: str  # normalized absolute URL
    text: str  # link text, trimmed and truncated


def extract_links(html: str, base_url: str, *, max_body_bytes: int = 5_000_000) -> list[Link]:
    """Parse `html` and return all valid normalized <a href> links.

    `base_url` is the URL the body was fetched from; if a `<base href>` is present
    inside the document, it overrides this for relative resolution.
    """
    if not html:
        return []
    if len(html) > max_body_bytes:
        html = html[:max_body_bytes]

    tree = HTMLParser(html)

    base = base_url
    base_node = tree.css_first("base[href]")
    if base_node is not None:
        href = base_node.attributes.get("href")
        if href:
            resolved = normalize(href, base_url)
            if resolved:
                base = resolved

    out: list[Link] = []
    seen: set[str] = set()
    for node in tree.css("a[href]"):
        href = node.attributes.get("href")
        if not href:
            continue
        url = normalize(href, base)
        if not url or url in seen:
            continue
        seen.add(url)

        text = (node.text(deep=True, separator=" ", strip=True) or "")[:200]
        if not text:
            title = node.attributes.get("title")
            if title:
                text = title[:200]
        out.append(Link(url=url, text=text))

    return out
