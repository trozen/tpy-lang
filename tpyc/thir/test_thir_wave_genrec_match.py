"""Genrec track cell A: generic recursive alias instances
(`RecursiveAliasInstanceType`) admitted into the match union-switch tiers
through the `_wrapper_union_like` accessor. The whole match flow is
duck-keyed (`needs_wrapper()` / `wrapper_info()`), so the rekey is the
classifier only; members index via the type's own `alternatives()` in
template ordering. Boundaries: the wrapper slice stays NAME-only,
and a PLAIN union subject keeps the bare-variant render (no `.value`)."""

from ..codegen_cpp import CodeGenOptions
from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)

_TREE = (
    "from tpy import Int32\n"
    "type Tree[T] = T | list[Tree[T]]\n"
)


class TestGenrecMatchRoutes:
    SRC = (
        _TREE
        + "class IntBox:\n"
        + "    v: Int32\n"
        + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
        + "class StrBox:\n"
        + "    s: str\n"
        + "    def __init__(self, s: str) -> None:\n        self.s = s\n"
        + "type Plain = IntBox | StrBox\n"
        # sum_leaf stays UNCALLED: passing a value into its `Tree[int]`
        # slot needs the cell-B arg rows (member-valued args at genrec
        # slots reject until then); a concrete uncalled fn still lowers
        # and emits, which is all the routing claim needs. The open-T
        # match (a generic fn) gets its witness with cell B, where its
        # instantiating call can route.
        + "def sum_leaf(t: Tree[int]) -> Int32:\n"
        + "    match t:\n"
        + "        case int() as v:\n            return v\n"
        + "        case _:\n            return -1\n"
        + "def plain_match(p: Plain) -> Int32:\n"
        + "    match p:\n"
        + "        case IntBox():\n            return p.v\n"
        + "        case StrBox():\n            return len(p.s)\n"
        + "    return 0\n"
        + "def main() -> None:\n"
        + "    print(plain_match(IntBox(9)))\n"
        + "    print(plain_match(StrBox('abcd')))\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        # The genrec subject dispatches through the wrapper's `.value`
        # variant (concrete AND open-T tiers)...
        assert ".value.index()" in cpp
        # ...while the plain union subject keeps the bare variant switch
        # (its own function's subject, no `.value` hop).
        assert "switch (__match_subject_1.index())" in cpp
        assert "switch (__match_subject_1.value.index())" in cpp


class TestGenrecMatchBoundaries:
    """The wrapper slice is NAME-only. A GUARDED genrec subject routes since
    the guarded-union dispatch hooks landed, so only the FIELD subject is
    still a boundary here."""

    GUARDED_SRC = (
        _TREE
        + "def guarded_genrec(t: Tree[int], n: Int32) -> Int32:\n"
        + "    match t:\n"
        + "        case int() as v if n > 0:\n            return v\n"
        + "        case _:\n            return -1\n"
    )

    FIELD_SRC = (
        _TREE
        + "class Holder:\n"
        + "    t: 'Tree[int]'\n"
        + "    def __init__(self, t: 'Tree[int]') -> None:\n"
        + "        self.t = t\n"
        + "def field_subject(h: Holder) -> Int32:\n"
        + "    match h.t:\n"
        + "        case int() as v:\n            return v\n"
        + "        case _:\n            return 0\n"
    )

    def test_guarded_genrec_subject_routes(self):
        _hpp, cpp = _assert_routes_byte_identical(self.GUARDED_SRC,
                                                  comments=False)
        # Still the wrapper `.value` hop -- the guard rides on top of the
        # same variant dispatch, it does not pick a different tier.
        assert "switch (__match_subject_1.value.index())" in cpp

    def test_field_genrec_subject_stays_ast(self):
        _assert_byte_identical(self.FIELD_SRC)
        compiler, modules = _compile(self.FIELD_SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=True,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        # EXACT dict: the match body is the claim; the ctor MIL reject is the
        # fixture's own unrelated residue and is spelled out so a change to
        # either side fails here.
        assert dict(compiler._thir_fallback) == {
            "body:stmt.match": 1,
            "ctor:ctor.mil_field.recursivealiasinstance.name": 1,
        }, compiler._thir_fallback
