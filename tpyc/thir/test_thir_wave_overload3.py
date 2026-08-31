"""The short-stub LIVE-default arity rows: omitted impl params emit as
prologue locals (`::tpy::BigInt factor = ::tpy::BigInt(1);` --
THIROverloadDefault via the shared default renderer), the literal-eq
fold mirrors `_resolve_literal_eq_statically` for the facts a literal
default injects, and param equality lost its conservative db_compare
row (a fact-less compare is a plain runtime compare on both paths).
Membership and bool-truthiness folds stay fenced (db_literal_fold)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _fn, _assert_byte_identical,
)


class TestOverloadDefaultLocals:
    def test_method_short_stub_emits_default_local(self):
        src = ("from typing import overload\n"
               "class Calculator:\n"
               "    offset: int\n"
               "    def __init__(self, offset: int) -> None:\n"
               "        self.offset = offset\n"
               "    @overload\n"
               "    def scale(self, x: int) -> int: ...\n"
               "    @overload\n"
               "    def scale(self, x: int, factor: int) -> int: ...\n"
               "    def scale(self, x: int, factor: int = 1) -> int:\n"
               "        return (x * factor) + self.offset\n"
               "def main() -> None:\n"
               "    c = Calculator(10)\n"
               "    print(c.scale(5))\n"
               "    print(c.scale(5, 3))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "scale") is not None
        cpp = _assert_byte_identical(src)
        assert "::tpy::BigInt factor = ::tpy::BigInt(1);" in cpp[0]

    def test_literal_default_folds_the_equality(self):
        # The short stub's count fact folds `if count == 0:` to the
        # then-branch; the default local still emits (literal narrowing
        # keeps its local, unlike the NoneType skip).
        src = ("from typing import overload\n"
               "@overload\n"
               "def repeat(s: str) -> str: ...\n"
               "@overload\n"
               "def repeat(s: str, count: int) -> str: ...\n"
               "def repeat(s: str, count: int = 0) -> str:\n"
               "    if count == 0:\n"
               "        return s\n"
               "    result = \"\"\n"
               "    i = 0\n"
               "    while i < count:\n"
               "        result = result + s\n"
               "        i = i + 1\n"
               "    return result\n"
               "def main() -> None:\n"
               "    print(repeat(\"ab\"))\n"
               "    print(repeat(\"ab\", 3))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "repeat") is not None
        cpp = _assert_byte_identical(src)
        assert "::tpy::BigInt count = ::tpy::BigInt(0);" in cpp[1]
        # the folded short stub returns immediately; the long stub keeps
        # the runtime compare
        assert "if ((count == 0))" in cpp[1]

    def test_membership_on_fact_param_folds(self):
        # `count in (0, 1)` folds through the mirrored `check_literal_in`
        # for the SHORT stub (default 0 injects Literal[0], a subset of the
        # tuple -> True splices the then-body); the long stub keeps the
        # runtime membership compare.
        src = ("from typing import overload\n"
               "@overload\n"
               "def pick(s: str) -> str: ...\n"
               "@overload\n"
               "def pick(s: str, count: int) -> str: ...\n"
               "def pick(s: str, count: int = 0) -> str:\n"
               "    if count in (0, 1):\n"
               "        return s\n"
               "    return s + s\n"
               "def main() -> None:\n"
               "    print(pick(\"ab\"))\n"
               "    print(pick(\"ab\", 5))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "pick") is not None
        _assert_byte_identical(src)
