"""The `@cpp_template` brace grammar's placeholder scan, the one every
question about which operands a template spells asks.

Light on purpose: the parser imports it, so it imports nothing from the
compiler.

Grammar follows Python str.format: `{{` / `}}` are literal braces, `{N}` is
the N-th runtime operand, `{self}` the receiver, and any other field (`{cpp}`,
a type parameter such as `{T}`) a type placeholder sema substitutes before
expansion.
"""

from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TemplatePlaceholders:
    """The placeholder occurrences of one template body."""
    # `{N}` keyed on int(N), so `{0}` and `{00}` count as the same operand.
    positional: 'Counter[int]' = field(default_factory=Counter)
    self_uses: int = 0
    # The type placeholders (`{cpp}`, `{T}`), by name.
    named: 'Counter[str]' = field(default_factory=Counter)
    # The first brace-grammar violation, or None when the body is well formed.
    malformed: str | None = None


def scan_template_placeholders(tmpl: str) -> TemplatePlaceholders:
    positional: Counter[int] = Counter()
    named: Counter[str] = Counter()
    self_uses = 0
    i, n = 0, len(tmpl)
    while i < n:
        c = tmpl[i]
        if c == "{":
            if i + 1 < n and tmpl[i + 1] == "{":
                i += 2
                continue
            close = tmpl.find("}", i + 1)
            if close == -1:
                return TemplatePlaceholders(
                    positional, self_uses, named,
                    "unmatched '{' (use '{{' for a literal brace)")
            name = tmpl[i + 1:close]
            if name == "self":
                self_uses += 1
            # str.isdigit also admits digits int() refuses (`{\u00b2}`).
            elif name.isascii() and name.isdigit():
                positional[int(name)] += 1
            elif name.isidentifier():
                named[name] += 1
            else:
                return TemplatePlaceholders(
                    positional, self_uses, named,
                    f"invalid placeholder '{{{name}}}' (use '{{{{' and "
                    f"'}}}}' for literal braces)")
            i = close + 1
        elif c == "}":
            if i + 1 < n and tmpl[i + 1] == "}":
                i += 2
                continue
            return TemplatePlaceholders(
                positional, self_uses, named,
                "unmatched '}' (use '}}' for a literal brace)")
        else:
            i += 1
    return TemplatePlaceholders(positional, self_uses, named, None)
