"""Tiny helpers usable by both pytest and the stdlib shim runner (no third-party imports)."""

from contextlib import contextmanager


@contextmanager
def raises(exc_type, contains: str | None = None):
    try:
        yield
    except exc_type as e:  # noqa: PERF203
        if contains is not None and contains not in str(e):
            raise AssertionError(f"expected {contains!r} in {e!r}") from e
        return
    raise AssertionError(f"expected {exc_type.__name__} was not raised")
