from hunter.parser import extract_links


class TestExtractLinks:
    def test_resolves_relative_against_base_url(self):
        html = '<a href="/foo">Foo</a><a href="bar">Bar</a>'
        links = extract_links(html, "https://example.com/page/")
        urls = sorted(l.url for l in links)
        assert urls == ["https://example.com/foo", "https://example.com/page/bar"]

    def test_honors_base_href(self):
        html = '<head><base href="https://other.com/x/"></head><body><a href="y">Y</a></body>'
        links = extract_links(html, "https://example.com/")
        assert any(l.url == "https://other.com/x/y" for l in links)

    def test_dedupes_within_a_page(self):
        html = '<a href="/x">A</a><a href="/x">B</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 1
        # First text wins (deterministic order: parser visits in document order)
        assert links[0].text == "A"

    def test_skips_unsupported_schemes(self):
        html = '<a href="mailto:x@y.com">M</a><a href="javascript:foo()">J</a><a href="/ok">OK</a>'
        links = extract_links(html, "https://example.com/")
        urls = [l.url for l in links]
        assert urls == ["https://example.com/ok"]

    def test_extracts_link_text(self):
        html = '<a href="/x">Read <b>more</b></a>'
        links = extract_links(html, "https://example.com/")
        assert links[0].text == "Read more"

    def test_uses_title_when_text_missing(self):
        html = '<a href="/x" title="Image link"><img src="/i.png"/></a>'
        links = extract_links(html, "https://example.com/")
        assert links[0].text == "Image link"

    def test_handles_empty_html(self):
        assert extract_links("", "https://example.com/") == []

    def test_truncates_huge_link_text(self):
        long_text = "x" * 1000
        html = f'<a href="/x">{long_text}</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links[0].text) <= 200
