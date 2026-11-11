"""U6: the home page shares well (preview image, canonical link, structured data)
and links only to pages visitors can open."""

import json
import re

from test_auth import make_client  # noqa: F401  (make_client is a pytest fixture)


def test_share_image_is_public_and_a_1200x630_png(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    r = client.get("/og.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    body = r.content
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(body[16:20], "big"), int.from_bytes(body[20:24], "big")
    assert (width, height) == (1200, 630)


def test_home_page_carries_share_tags_and_valid_structured_data(make_client):  # noqa: F811
    html = make_client(auth_on=True).get("/").text
    for tag in ('property="og:image" content="https://closebrief.onrender.com/og.png"',
                'name="twitter:card" content="summary_large_image"',
                'rel="canonical" href="https://closebrief.onrender.com/"'):
        assert tag in html, tag
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
    assert ld["@type"] == "SoftwareApplication" and ld["name"] == "Closebrief"
    assert ld["offers"]["price"] == "0"


def test_home_page_does_not_link_to_api_docs():
    # /docs is switched off in production, so a footer link to it was a 404.
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "ui" / "web" / "home.html").read_text(encoding="utf-8")
    assert 'href="/docs"' not in html
