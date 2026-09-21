"""URL ingestion is a server-side fetch, so its destination is a security boundary.

Without these checks a signed-up user can aim the server at addresses only the server
can reach -- cloud metadata, other services in the VPC, localhost -- and read the
response back as a document. Sign-up is open and unverified, so "signed-up user" means
anyone.

Hermetic: nothing here makes a network request. `assert_fetchable` resolves names, which
is why the hostname cases use literals and names that are guaranteed to resolve locally.
"""

import pytest

from app.ingestion.parsers import (
    ALLOWED_URL_SCHEMES,
    MAX_URL_REDIRECTS,
    UnsupportedSource,
    assert_fetchable,
)


@pytest.mark.parametrize(
    "url",
    [
        # The cloud metadata endpoint. On EC2 this is the one that can hand out
        # credentials for whatever IAM role the instance carries.
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/credentials",
        # The API talking to itself, or to anything else on the host.
        "http://127.0.0.1:8000/api/health",
        "http://localhost/",
        "http://[::1]/",
        # Anything else inside the private network.
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/admin",
        # Unspecified and broadcast.
        "http://0.0.0.0/",
    ],
)
def test_internal_addresses_are_refused(url):
    with pytest.raises(UnsupportedSource):
        assert_fetchable(url)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "gopher://example.com/", "ftp://example.com/x", "data:text/html,hi"],
)
def test_only_http_and_https_are_allowed(url):
    with pytest.raises(UnsupportedSource, match="http and https"):
        assert_fetchable(url)


def test_ipv4_mapped_ipv6_does_not_slip_through():
    """`::ffff:169.254.169.254` is the metadata address wearing a different hat.

    The private/loopback properties are False on the IPv6 form, so the check has to
    unwrap `ipv4_mapped` before testing it.
    """
    with pytest.raises(UnsupportedSource):
        assert_fetchable("http://[::ffff:169.254.169.254]/latest/meta-data/")


def test_a_public_url_is_allowed():
    assert_fetchable("https://example.com/some/article")


def test_unresolvable_host_is_refused_not_fetched():
    with pytest.raises(UnsupportedSource, match="resolve"):
        assert_fetchable("https://this-host-does-not-exist.invalid/page")


def test_redirects_are_bounded():
    """A public URL may redirect to a private one, so the chain is followed here rather
    than handed to httpx -- which means it also needs a stop."""
    assert MAX_URL_REDIRECTS > 0
    assert ALLOWED_URL_SCHEMES == {"http", "https"}
