from hunter.normalize import (
    is_same_site,
    looks_non_html,
    matches_any,
    normalize,
)


class TestNormalize:
    def test_lowercases_scheme_and_host(self):
        assert normalize("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_strips_default_port(self):
        assert normalize("http://example.com:80/x") == "http://example.com/x"
        assert normalize("https://example.com:443/x") == "https://example.com/x"

    def test_keeps_non_default_port(self):
        assert normalize("http://example.com:8080/x") == "http://example.com:8080/x"

    def test_drops_fragment(self):
        assert normalize("https://example.com/a#section") == "https://example.com/a"

    def test_sorts_query_params(self):
        assert normalize("https://example.com/?b=2&a=1") == "https://example.com/?a=1&b=2"

    def test_resolves_relative_against_base(self):
        out = normalize("../foo", base="https://example.com/a/b/")
        assert out == "https://example.com/a/foo"

    def test_skips_mailto(self):
        assert normalize("mailto:x@y.com") is None

    def test_skips_javascript(self):
        assert normalize("javascript:void(0)") is None

    def test_skips_tel(self):
        assert normalize("tel:+12025551212") is None

    def test_blank_returns_none(self):
        assert normalize("") is None
        assert normalize("   ") is None

    def test_preserves_trailing_slash_difference(self):
        assert normalize("https://example.com/foo") != normalize("https://example.com/foo/")

    def test_protocol_relative(self):
        out = normalize("//cdn.example.com/x", base="https://example.com/")
        assert out == "https://cdn.example.com/x"

    def test_dot_segments_resolved(self):
        out = normalize("https://example.com/a/./b/../c")
        assert out == "https://example.com/a/c"


class TestSameSite:
    def test_host_scope_exact(self):
        assert is_same_site("https://example.com/x", "example.com", "host")
        assert not is_same_site("https://other.com/x", "example.com", "host")
        assert not is_same_site("https://www.example.com/x", "example.com", "host")

    def test_domain_scope_includes_subdomains(self):
        assert is_same_site("https://www.example.com/x", "example.com", "domain")
        assert is_same_site("https://api.example.com/x", "example.com", "domain")
        assert not is_same_site("https://other.com/x", "example.com", "domain")


class TestLooksNonHtml:
    def test_image_extension(self):
        assert looks_non_html("https://example.com/foo.png")
        assert looks_non_html("https://example.com/foo.JPG")

    def test_pdf(self):
        assert looks_non_html("https://example.com/doc.pdf")

    def test_html_paths_ignored(self):
        assert not looks_non_html("https://example.com/post-1")
        assert not looks_non_html("https://example.com/blog/")

    def test_fragment_doesnt_break(self):
        # normalize() drops fragments before this is called, but the function itself
        # should still behave reasonably on a path without one.
        assert not looks_non_html("https://example.com/post.html.foo")


class TestGlob:
    def test_matches_include(self):
        assert matches_any("/blog/x", ["/blog/*"])
        assert not matches_any("/admin/x", ["/blog/*"])

    def test_matches_any_pattern(self):
        assert matches_any("/admin/x", ["/blog/*", "/admin/*"])
