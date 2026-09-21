"""URL ingestion tests.

The point of using trafilatura over a plain HTML-to-text pass is that nav, cookie
banners and footers never become chunks -- boilerplate that gets embedded is boilerplate
that gets retrieved. These assert that, without hitting the network.
"""

from contextlib import contextmanager

import httpx
import pytest

from app.ingestion.parsers import UnsupportedSource, parse_url

PAGE = """
<!doctype html>
<html><head><title>Bearing maintenance</title></head>
<body>
  <nav><a href="/">Home</a> <a href="/shop">Shop</a> <a href="/cart">Cart</a></nav>
  <div class="cookie-banner">We use cookies. Accept all?</div>
  <article>
    <h1>Bearing maintenance intervals</h1>
    <p>Precision bearings should be regreased every 4000 operating hours. Running a
    bearing beyond 2400 rpm voids the manufacturer warranty and accelerates wear on
    the inner race.</p>
    <p>Inspect the seal for discolouration at every service interval, and replace the
    bearing outright if the race shows pitting.</p>
  </article>
  <footer>Copyright 2026 Northwind Components. All rights reserved.</footer>
</body></html>
"""


@pytest.fixture
def served(monkeypatch):
    """Serve a canned page instead of reaching the network.

    Patches `stream`, not `get`: the fetcher reads the body in chunks so it can abort
    past MAX_URL_BYTES, and it follows redirects itself so each hop can be re-checked
    against the private-address rules.
    """

    def serve(html: str, status: int = 200):
        @contextmanager
        def fake_stream(self, method, url, **kwargs):
            yield httpx.Response(status, text=html, request=httpx.Request(method, url))

        monkeypatch.setattr(httpx.Client, "stream", fake_stream)

    return serve


def test_main_content_is_extracted(served):
    served(PAGE)
    docs = parse_url("https://example.com/bearings")

    assert len(docs) == 1
    text = docs[0].page_content
    assert "regreased every 4000 operating hours" in text
    assert "2400 rpm" in text


def test_navigation_and_boilerplate_are_dropped(served):
    """Boilerplate that gets embedded is boilerplate that gets retrieved."""
    served(PAGE)
    text = parse_url("https://example.com/bearings")[0].page_content

    for junk in ("Shop", "Cart", "We use cookies", "All rights reserved"):
        assert junk not in text, f"{junk!r} should not survive extraction"


def test_source_url_is_kept_for_citations(served):
    served(PAGE)
    docs = parse_url("https://example.com/bearings")
    assert docs[0].metadata["url"] == "https://example.com/bearings"


def test_page_with_no_article_content_is_rejected(served):
    """Better a clear failure at upload than an empty document in the library."""
    served("<html><body><nav>Home</nav></body></html>")
    with pytest.raises(UnsupportedSource):
        parse_url("https://example.com/empty")


def test_http_error_propagates(served):
    served("not found", status=404)
    with pytest.raises(httpx.HTTPStatusError):
        parse_url("https://example.com/missing")
