import pytest

from app.core.errors import ValidationFailed
from app.services.conversation_service import (
    AUTO_TITLE_MAX,
    DEFAULT_TITLE,
    MAX_MESSAGE_CHARS,
    clean_content,
    clean_title,
    derive_title,
)


def test_short_first_message_becomes_title_verbatim():
    assert derive_title("Does exercise reduce depression?") == "Does exercise reduce depression?"


def test_title_collapses_whitespace_and_newlines():
    assert derive_title("  Does   exercise\n\nreduce\tdepression?  ") == "Does exercise reduce depression?"


def test_long_message_is_cut_at_word_boundary_with_ellipsis():
    text = "What is the effect of high intensity interval training on depressive symptoms in older adults"
    t = derive_title(text)
    assert len(t) <= AUTO_TITLE_MAX and t.endswith("…")
    assert text.startswith(t[:-1])  # a prefix of the original, never invented words
    assert not t[:-1].endswith(" ")


def test_unbroken_long_word_is_still_bounded():
    t = derive_title("x" * 500)
    assert len(t) == AUTO_TITLE_MAX and t.endswith("…")


def test_blank_message_falls_back_to_default_title():
    assert derive_title("   \n ") == DEFAULT_TITLE


def test_clean_title_rejects_blank_and_overlong():
    with pytest.raises(ValidationFailed):
        clean_title("   ")
    with pytest.raises(ValidationFailed):
        clean_title("a" * 201)
    assert clean_title("  Search   plan ") == "Search plan"


def test_clean_content_keeps_inner_formatting_and_bounds_length():
    assert clean_content("\n  line 1\n\n  line 2  \n") == "line 1\n\n  line 2"
    with pytest.raises(ValidationFailed):
        clean_content(" \n\t ")
    with pytest.raises(ValidationFailed):
        clean_content("a" * (MAX_MESSAGE_CHARS + 1))
    assert len(clean_content("a" * MAX_MESSAGE_CHARS)) == MAX_MESSAGE_CHARS
