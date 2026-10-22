"""Requires argon2-cffi (so NOT runnable under the stdlib shim)."""

from app.core import passwords


def test_hash_is_argon2id_and_verifies():
    h = passwords.hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "correct horse battery staple") is True
    assert passwords.verify_password(h, "wrong") is False


def test_unknown_user_path_still_returns_false_and_never_true():
    assert passwords.verify_password(None, "anything") is False
    assert passwords.verify_password("not-a-hash", "anything") is False


def test_hashes_are_salted():
    assert passwords.hash_password("same-password-1") != passwords.hash_password("same-password-1")


def test_needs_rehash_false_for_fresh_hash():
    assert passwords.needs_rehash(passwords.hash_password("another-long-password")) is False
