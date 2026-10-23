"""A small, provider-neutral Boolean search expression. Stdlib only.

Grammar (operators must be UPPER CASE so ordinary words such as "or" in prose stay search terms):

    expr    := or_expr
    or_expr := and_expr ("OR" and_expr)*
    and_expr:= unary (["AND"] unary)*          adjacent terms are ANDed
    unary   := "NOT" unary | primary
    primary := "(" expr ")" | "phrase in quotes" | word

A word may end in `*` (truncation). Field tags (`TITLE:x`) and other provider-specific syntax are rejected rather
than passed through, because the same expression is rendered for several providers and must mean the same thing
in each. Every rendering reports what it could not express (`limitations`), so a lossy translation is visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_TERMS = 60
MAX_DEPTH = 8
MAX_LENGTH = 2000
OPERATORS = ("AND", "OR", "NOT")


class QueryError(ValueError):
    """The expression cannot be parsed. Message is safe to show to the user."""


@dataclass(frozen=True)
class Term:
    text: str
    phrase: bool = False
    truncated: bool = False  # trailing * in the source expression


@dataclass(frozen=True)
class And:
    children: tuple[Node, ...]


@dataclass(frozen=True)
class Or:
    children: tuple[Node, ...]


@dataclass(frozen=True)
class Not:
    child: Node


Node = Term | And | Or | Not

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"([^"]*)"|([^\s()"]+))')
_WORD_OK = re.compile(r"^[\w\-'.+/&]+\*?$", re.UNICODE)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            if text[pos:].strip().startswith('"'):
                raise QueryError("A quotation mark is not closed.")
            raise QueryError(f"Cannot read the expression near {text[pos : pos + 20]!r}.")
        pos = m.end()
        if m.group(1):
            tokens.append(("(", "("))
        elif m.group(2):
            tokens.append((")", ")"))
        elif m.group(3) is not None:
            phrase = " ".join(m.group(3).split())
            if not phrase:
                raise QueryError("An empty quoted phrase is not allowed.")
            tokens.append(("PHRASE", phrase))
        else:
            word = m.group(4)
            if word in OPERATORS:
                tokens.append((word, word))
            else:
                if ":" in word:
                    raise QueryError(
                        f"Field tags such as {word!r} are not supported; the same expression is sent to several "
                        "providers. Use plain words and quoted phrases."
                    )
                if not _WORD_OK.match(word):
                    raise QueryError(f"Unsupported characters in {word!r}.")
                tokens.append(("WORD", word))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.t, self.i, self.terms = tokens, 0, 0

    def peek(self) -> str | None:
        return self.t[self.i][0] if self.i < len(self.t) else None

    def take(self) -> tuple[str, str]:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expr(self, depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise QueryError(f"Parentheses are nested too deeply (limit {MAX_DEPTH}).")
        parts = [self.and_expr(depth)]
        while self.peek() == "OR":
            self.take()
            parts.append(self.and_expr(depth))
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def and_expr(self, depth: int) -> Node:
        parts = [self.unary(depth)]
        while self.peek() in ("AND", "NOT", "WORD", "PHRASE", "("):
            if self.peek() == "AND":
                self.take()
            parts.append(self.unary(depth))
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def unary(self, depth: int) -> Node:
        if self.peek() == "NOT":
            self.take()
            return Not(self.unary(depth))
        return self.primary(depth)

    def primary(self, depth: int) -> Node:
        kind = self.peek()
        if kind is None:
            raise QueryError("The expression ends where a search term was expected.")
        if kind == "(":
            self.take()
            node = self.expr(depth + 1)
            if self.peek() != ")":
                raise QueryError("A parenthesis is not closed.")
            self.take()
            return node
        if kind in ("WORD", "PHRASE"):
            _, value = self.take()
            self.terms += 1
            if self.terms > MAX_TERMS:
                raise QueryError(f"Too many search terms (limit {MAX_TERMS}).")
            if kind == "PHRASE":
                return Term(value, phrase=True)
            truncated = value.endswith("*")
            return Term(value.rstrip("*"), truncated=truncated)
        raise QueryError(f"Unexpected {kind!r}: an operator needs a search term on both sides.")


def _flatten(node: Node) -> Node:
    """Merge nested And(And(..)) / Or(Or(..)) so renderings stay readable."""
    if isinstance(node, And | Or):
        cls = type(node)
        kids: list[Node] = []
        for c in (_flatten(k) for k in node.children):
            kids.extend(c.children if isinstance(c, cls) else (c,))
        return cls(tuple(kids))
    if isinstance(node, Not):
        return Not(_flatten(node.child))
    return node


def _has_positive(node: Node) -> bool:
    if isinstance(node, Term):
        return True
    if isinstance(node, Not):
        return False
    if isinstance(node, And):
        return any(_has_positive(c) for c in node.children)
    return all(_has_positive(c) for c in node.children)


def parse(text: str) -> Node:
    if not text or not text.strip():
        raise QueryError("The search expression is empty.")
    if len(text) > MAX_LENGTH:
        raise QueryError(f"The search expression is too long (limit {MAX_LENGTH} characters).")
    p = _Parser(_tokenize(text))
    node = p.expr(0)
    if p.i != len(p.t):
        raise QueryError("There is an unmatched closing parenthesis.")
    node = _flatten(node)
    if not _has_positive(node):
        if positive_terms(node):
            raise QueryError(
                "An OR alternative only excludes terms (for example 'a OR NOT b'). Join exclusions with AND: 'a NOT b'."
            )
        raise QueryError("The expression only excludes terms. Add at least one term to search for.")
    return node


# ---------- rendering ----------


@dataclass
class Rendered:
    text: str
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Dialect:
    """How a provider writes terms and operators. `and_not` is the binary exclusion operator."""

    and_op: str = " AND "
    or_op: str = " OR "
    and_not: str = " NOT "
    term_prefix: str = ""
    supports_truncation: bool = False
    name: str = "generic"


def _render(node: Node, d: Dialect, notes: set[str], top: bool) -> str:
    if isinstance(node, Term):
        text = node.text.replace('"', "")
        if node.phrase:
            return f'{d.term_prefix}"{text}"'
        if node.truncated:
            if d.supports_truncation:
                return f"{d.term_prefix}{text}*"
            notes.add(f"{d.name} does not support truncation (*); truncated words were searched as whole words.")
        return f"{d.term_prefix}{text}"
    if isinstance(node, Or):
        parts = [r for r in (_render(c, d, notes, False) for c in node.children) if r]
        inner = d.or_op.join(parts)
        return inner if top or len(parts) == 1 else f"({inner})"
    if isinstance(node, And):
        pos = [c for c in node.children if not isinstance(c, Not)]
        neg = [c.child for c in node.children if isinstance(c, Not)]
        inner = d.and_op.join(_render(c, d, notes, len(pos) == 1 and not neg and top) for c in pos)
        for n in neg:
            inner += d.and_not + _wrap(_render(n, d, notes, False))
        return inner if top else f"({inner})"
    # A NOT that is not inside an AND (e.g. "a OR NOT b") has no binary form in these dialects.
    notes.add(f"{d.name}: an exclusion outside an AND group was dropped ('NOT' needs something to exclude from).")
    return ""


def _wrap(s: str) -> str:
    return s if s.startswith("(") or " " not in s or s.startswith('"') else f"({s})"


def render(node: Node, dialect: Dialect) -> Rendered:
    notes: set[str] = set()
    text = _render(node, dialect, notes, True).strip()
    return Rendered(text=text, limitations=sorted(notes))


def positive_terms(node: Node) -> list[Term]:
    """Every term that is not excluded, in order, de-duplicated (for keyword-only providers)."""
    out: list[Term] = []
    seen: set[str] = set()

    def walk(n: Node) -> None:
        if isinstance(n, Term):
            k = n.text.lower()
            if k not in seen:
                seen.add(k)
                out.append(n)
        elif isinstance(n, And | Or):
            for c in n.children:
                walk(c)

    walk(node)
    return out


def has_operator(node: Node, kind: type) -> bool:
    if isinstance(node, kind):
        return True
    if isinstance(node, And | Or):
        return any(has_operator(c, kind) for c in node.children)
    if isinstance(node, Not):
        return kind is Not or has_operator(node.child, kind)
    return False


def to_text(node: Node) -> str:
    """Canonical text of an expression in this module's own syntax (round-trips through parse)."""
    return render(node, Dialect(supports_truncation=True, name="canonical")).text
