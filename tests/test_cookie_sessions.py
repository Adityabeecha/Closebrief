"""S8: the browser's session lives in an HttpOnly cookie that page scripts can't
read. A token the page already holds (a Supabase sign-in, or one an older
version stored) is traded for the cookie once; cookie-signed writes must carry
the client header (CSRF guard). Bearer tokens keep working for API clients.
S15: the privacy and terms pages are public."""

import time

import jwt
from test_auth import SECRET, _bearer, make_client  # noqa: F401  (make_client is a pytest fixture)

# Postgres stores user ids as UUIDs.
UID = "00000000-0000-4000-8000-0000000000c5"
HDR = {"X-Closebrief-Client": "1"}


def _signed_in(client):
    r = client.post("/auth/session", headers=_bearer(sub=UID, email="c@co.com"))
    assert r.status_code == 200, r.text
    return r


def test_trading_a_token_sets_an_httponly_cookie_and_never_returns_it(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    r = _signed_in(client)
    assert r.json() == {"email": "c@co.com", "role": r.json()["role"]}
    cookies = r.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("cb_session="))
    hint = next(c for c in cookies if c.startswith("cb_signed_in="))
    assert "httponly" in session.lower() and "samesite=lax" in session.lower() and "path=/" in session.lower()
    assert "httponly" not in hint.lower()          # the home page may read the hint, not the session
    assert client.get("/auth/session").json() == {"signed_in": True, "email": "c@co.com", "role": r.json()["role"]}
    assert client.get("/me").json()["email"] == "c@co.com"


def test_cookie_signed_writes_need_the_client_header(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    _signed_in(client)
    # Another site can make the browser send the cookie, but not our header.
    r = client.post("/context", json={"type": "event_note", "title": "t", "body": "b"})
    assert r.status_code == 403 and "client header" in r.json()["detail"].lower()
    ok = client.post("/context", json={"type": "event_note", "title": "t", "body": "b"}, headers=HDR)
    assert ok.status_code in (200, 201), ok.text
    assert client.get("/me").status_code == 200    # reads don't need it


def test_bad_or_expired_cookies_are_refused(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    expired = jwt.encode({"sub": UID, "email": "c@co.com", "iss": "closebrief", "aud": "authenticated",
                          "iat": int(time.time()) - 7200, "exp": int(time.time()) - 60}, SECRET, algorithm="HS256")
    client.cookies.set("cb_session", expired)
    assert client.get("/me").status_code == 401
    assert client.get("/auth/session").json() == {"signed_in": False}
    # A Supabase token isn't a Closebrief session, even if someone puts it in the cookie.
    client.cookies.set("cb_session", jwt.encode({"sub": UID, "aud": "authenticated", "exp": int(time.time()) + 60},
                                                SECRET, algorithm="HS256"))
    assert client.get("/me").status_code == 401


def test_session_trade_needs_a_valid_token(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    assert client.post("/auth/session").status_code == 401
    assert client.post("/auth/session", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.post("/auth/session", headers=_bearer(sub=UID, exp_delta=-60)).status_code == 401


def test_sign_out_clears_the_cookies(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    _signed_in(client)
    r = client.post("/auth/logout", headers=HDR)
    assert r.status_code == 204
    cleared = r.headers.get_list("set-cookie")
    assert any(c.startswith("cb_session=") and "max-age=0" in c.lower() for c in cleared)
    assert any(c.startswith("cb_signed_in=") and "max-age=0" in c.lower() for c in cleared)
    assert client.get("/auth/session").json() == {"signed_in": False}


def test_bearer_tokens_still_work_for_api_clients(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    assert client.get("/me", headers=_bearer(sub=UID, email="api@co.com")).json()["email"] == "api@co.com"


def test_privacy_and_terms_are_public(make_client):  # noqa: F811
    client = make_client(auth_on=True)
    for path, words in (("/privacy", "What's collected"), ("/terms", "Check the numbers")):
        r = client.get(path)
        assert r.status_code == 200 and words in r.text, path
    home = client.get("/").text
    assert 'href="/privacy"' in home and 'href="/terms"' in home
