"""`header_secret_ok`: the admin routes' secret check (HUXLEY_ADMIN_TOKEN)."""

from __future__ import annotations

from pydantic import SecretStr
from websockets.datastructures import Headers

from huxley.server.auth import header_secret_ok

SECRET = SecretStr("s3cret-admin-token-0123456789")
NAME = "X-Huxley-Admin"


def test_exactly_the_secret_passes() -> None:
    assert header_secret_ok(Headers({NAME: "s3cret-admin-token-0123456789"}), NAME, SECRET)
    assert header_secret_ok(Headers({NAME: " s3cret-admin-token-0123456789 "}), NAME, SECRET)


def test_anything_else_fails() -> None:
    for value in ("1", "", "s3cret-admin-token-012345678", "s3cret-admin-token-0123456789x", "ñ"):
        assert not header_secret_ok(Headers({NAME: value}), NAME, SECRET), value
    assert not header_secret_ok(Headers(), NAME, SECRET)


def test_two_headers_fail_even_if_one_is_right() -> None:
    headers = Headers()
    headers[NAME] = "1"
    headers[NAME] = "s3cret-admin-token-0123456789"
    assert not header_secret_ok(headers, NAME, SECRET)


def test_admin_token_problems() -> None:
    from huxley.server.auth import admin_token_problem

    assert admin_token_problem(None) == "missing"
    assert admin_token_problem(SecretStr("short")) == "short"
    assert admin_token_problem(SecretStr("ñ" * 40)) == "not_ascii"
    assert admin_token_problem(SecretStr(" " + "a" * 40)) == "spaces"
    assert admin_token_problem(SecretStr("a" * 20 + " " + "b" * 20)) == "spaces"
    assert admin_token_problem(SecretStr("a" * 48)) is None
