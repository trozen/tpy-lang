"""Pins for the M4c wrapper-match slice: a value-repr non-generic
recursive-alias wrapper NAME subject dispatches through `.value`
(`switch (subj.value.index())`, `std::get` over `subj.value`), plus the
wrapper member-init decl row, the wrapper container-literal element row,
and the free-call wrapper arg rows (same-wrapper bare name / member-name
typed temp with the `_maybe_move` mirror). Boundaries: guarded matches,
field subjects, non-ctor decl inits, and hoisted pointer-form subjects
keep falling back (the last is corpus-pinned by
union/match_hoisted_wrapper_local)."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _compile,
    _entry,
    _fn,
    _lower_ctx_witnessed,
)

_TREE = (
    "class Leaf:\n"
    "    value: int\n"
    "    def __init__(self, value: int) -> None:\n"
    "        self.value = value\n"
    "type Tree = Leaf | list[Tree]\n"
)


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp, compiler._thir_face_witnesses, compiler._thir_fallback


class TestWrapperMatch:
    def test_wrapper_subject_routes_value_indirection(self):
        src = _TREE + (
            "def describe(t: Tree) -> str:\n"
            "    match t:\n"
            "        case Leaf(value=v):\n"
            "            return \"leaf=\" + str(v)\n"
            "        case _:\n"
            "            return \"branch\"\n"
            "def main() -> None:\n"
            "    a: Tree = Leaf(42)\n"
            "    print(describe(a))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert faces.get("match.union_wrapper_value", 0) >= 1
        assert not fallback
        assert ".value.index())" in out
        assert "std::get<0>(__match_subject_1.value)" in out
        _assert_byte_identical(src)

    def test_guarded_wrapper_match_falls_back(self):
        src = _TREE + (
            "def describe(t: Tree, big: bool) -> str:\n"
            "    match t:\n"
            "        case Leaf(value=v) if big:\n"
            "            return \"big\"\n"
            "        case Leaf(value=v):\n"
            "            return \"leaf\"\n"
            "        case _:\n"
            "            return \"branch\"\n"
            "def main() -> None:\n"
            "    a: Tree = Leaf(1)\n"
            "    print(describe(a, True))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not faces.get("match.union_wrapper_value")
        assert any("stmt.match" in r for r in fallback)
        _assert_byte_identical(src)

    def test_wrapper_field_subject_falls_back(self):
        src = _TREE + (
            "class Holder:\n"
            "    t: Tree\n"
            "    def __init__(self) -> None:\n"
            "        self.t = Leaf(1)\n"
            "def probe(h: Holder) -> str:\n"
            "    match h.t:\n"
            "        case Leaf():\n"
            "            return \"leaf\"\n"
            "        case _:\n"
            "            return \"branch\"\n"
            "def main() -> None:\n"
            "    print(probe(Holder()))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not faces.get("match.union_wrapper_value")
        _assert_byte_identical(src)


class TestWrapperDeclAndArgs:
    def test_member_ctor_decl_routes(self):
        # The match lives in a helper on the PARAM: a freshly-initialized
        # local is Leaf-NARROWED, making a following wildcard a sema error.
        src = _TREE + (
            "def show(t: Tree) -> None:\n"
            "    match t:\n"
            "        case Leaf(value=v):\n"
            "            print(v)\n"
            "        case _:\n"
            "            print(\"branch\")\n"
            "def main() -> None:\n"
            "    a: Tree = Leaf(42)\n"
            "    b: list[Tree] = [Leaf(1), Leaf(2)]\n"
            "    show(a)\n"
            "    show(b)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "Tree a = Leaf(::tpy::BigInt(42));" in out
        _assert_byte_identical(src)

    def test_name_init_decl_falls_back(self):
        # A non-ctor init (a member NAME) is outside the decl row's slice.
        src = _TREE + (
            "def show(t: Tree) -> None:\n"
            "    match t:\n"
            "        case Leaf(value=v):\n"
            "            print(v)\n"
            "        case _:\n"
            "            print(\"branch\")\n"
            "def main() -> None:\n"
            "    l = Leaf(7)\n"
            "    a: Tree = l\n"
            "    show(a)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert any("decl." in r or "slot" in r for r in fallback)
        _assert_byte_identical(src)

    def test_wrapper_args_bare_and_member_temp(self):
        src = _TREE + (
            "def head(t: Tree) -> int:\n"
            "    match t:\n"
            "        case Leaf(value=v):\n"
            "            return v\n"
            "        case _:\n"
            "            return -1\n"
            "def main() -> None:\n"
            "    a: Tree = Leaf(42)\n"
            "    b: list[Tree] = [Leaf(1)]\n"
            "    print(head(a))\n"
            "    print(head(b))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert faces.get("argtemp.ru_wrapper_member", 0) >= 1
        # The member name moves at its last use (_maybe_move mirror).
        assert "Tree __tmp_1 = std::move(b);" in out
        # The same-wrapper name passes bare -- no temp for `a`.
        assert "head(a)" in out
        _assert_byte_identical(src)
