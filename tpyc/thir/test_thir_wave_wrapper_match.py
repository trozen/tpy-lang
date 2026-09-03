"""Pins for the M4c wrapper-match slice: a value-repr non-generic
recursive-alias wrapper NAME subject dispatches through `.value`
(`switch (subj.value.index())`, `std::get` over `subj.value`), plus the
wrapper member-init decl row, the wrapper container-literal element row,
and the free-call wrapper arg rows (same-wrapper bare name / member-name
typed temp with the `_maybe_move` mirror). Boundaries: guarded matches,
field subjects, and non-ctor decl inits keep falling back; hoisted
pointer-form and borrow-returning-call subjects ROUTE (the
TestWrapperMatchSubjectSources widenings)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
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
        entry, options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp, compiler._thir_face_witnesses


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
        out, faces = _gen_thir(src)
        assert faces.get("match.union_wrapper_value", 0) >= 1
        assert ".value.index())" in out
        assert "std::get<0>(__match_subject_1.value)" in out
        _assert_byte_identical(src)

    def test_narrow_then_match_subject_routes(self):
        # After `if t is None: return`, the subject reads flow-DIVERGENT on
        # sema's books but the match consumes the WHOLE variant at full
        # arity -- the bare `auto& __match_subject_N = t;` bind routes.
        src = (
            "class Leaf:\n"
            "    value: int\n"
            "    def __init__(self, value: int) -> None:\n"
            "        self.value = value\n"
            "type Tree = None | Leaf | list[Tree]\n"
            "def describe(t: Tree) -> str:\n"
            "    if t is None:\n"
            "        return \"null\"\n"
            "    match t:\n"
            "        case Leaf():\n"
            "            return \"leaf\"\n"
            "        case _:\n"
            "            return \"branch\"\n"
            "def main() -> None:\n"
            "    a: Tree = None\n"
            "    print(describe(a))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("isnone.union_wrapper_monostate", 0) >= 1
        assert faces.get("match.union_wrapper_value", 0) >= 1
        assert "auto& __match_subject_1 = t;" in out
        _assert_byte_identical(src)

    def test_guarded_wrapper_match_routes(self):
        # The guarded tier dispatches a wrapper subject through `.value`
        # like the unguarded one (switch head + get positions).
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
        out, faces = _gen_thir(src)
        assert faces.get("match.guarded_union_wrapper", 0) >= 1
        assert ".value.index())" in out
        assert "std::get<0>(__match_subject_1.value)" in out
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
        _assert_rejects_at(_reject_tally(src), "body:stmt.match")


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
        out, faces = _gen_thir(src)
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
        fallback = _reject_tally(src)
        assert any("decl." in r or "slot" in r for r in fallback)

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
        out, faces = _gen_thir(src)
        assert faces.get("argtemp.ru_wrapper_member", 0) >= 1
        # The member name moves at its last use (_maybe_move mirror).
        assert "Tree __tmp_1 = std::move(b);" in out
        # The same-wrapper name passes bare -- no temp for `a`.
        assert "head(a)" in out
        _assert_byte_identical(src)


class TestWrapperMatchSubjectSources:
    """M4c subject widenings: a hoisted POINTER-local wrapper subject
    derefs into the alias (`auto& __match_subject_N = (*v);`), a
    borrow-returning CALL subject binds by reference
    (`auto& __match_subject_N = h.get();`); field / Own-call / guarded
    flavors keep deferring."""

    _PRE = (
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "class Holder:\n"
        "    t: Tree[Int32]\n"
        "    def __init__(self, xs: Tree[Int32]) -> None:\n"
        "        self.t = xs\n"
        "    def get(self) -> Tree[Int32]:\n"
        "        return self.t\n"
        "    def make(self) -> Own[Tree[Int32]]:\n"
        "        return [1, 2]\n")

    def test_pointer_local_and_borrow_call_subjects_route(self):
        # Per-function routing: the fixture's ctor MIL (the genrec field
        # init) and main's ctor arg are unrelated known gaps, so the
        # whole-program claim would fail on them.
        src = self._PRE + (
            "def hoisted(src: Tree[Int32], flag: bool) -> None:\n"
            "    if flag:\n"
            "        v = src\n"
            "    else:\n"
            "        v = src\n"
            "    match v:\n"
            "        case list() as b:\n"
            "            b.append(9)\n"
            "        case _:\n"
            "            pass\n"
            "def call_subject(h: Holder) -> None:\n"
            "    match h.get():\n"
            "        case list() as got:\n"
            "            got.append(3)\n"
            "        case _:\n"
            "            pass\n"
            "def main() -> None:\n"
            "    t: Tree[Int32] = [1]\n"
            "    hoisted(t, True)\n"
            "    h = Holder([2])\n"
            "    call_subject(h)\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.other_recursivealiasinstancetype")

    def test_field_and_own_call_defer_guarded_call_routes(self):
        # BOUNDARY: a FIELD wrapper subject (the `.value`-over-member
        # composition) and an Own-returning call subject (value flavor)
        # keep deferring; the guarded tier's borrow-call subject now rides
        # the wrapper `.value` respell like the unguarded one.
        src = self._PRE + (
            "def field_subject(h: Holder) -> None:\n"
            "    match h.t:\n"
            "        case list() as b:\n"
            "            b.append(4)\n"
            "        case _:\n"
            "            pass\n"
            "def own_call_subject(h: Holder) -> None:\n"
            "    match h.make():\n"
            "        case list() as b:\n"
            "            print(len(b))\n"
            "        case _:\n"
            "            pass\n"
            "def guarded_call(h: Holder, k: Int32) -> None:\n"
            "    match h.get():\n"
            "        case list() as b if k > 0:\n"
            "            print(len(b))\n"
            "        case _:\n"
            "            pass\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.match")

    def test_nongeneric_wrapper_call_subject_routes(self):
        # The _wrapper_borrow_return disjunct's own witness: a bare
        # `-> Expr` accessor on a NON-generic wrapper renders `Expr&`
        # through the wrapper convention, which call_returns_cpp_ref does
        # not see (ablation-verified load-bearing).
        src = (
            "type Expr = int | list[Expr]\n"
            "class Box:\n"
            "    e: Expr\n"
            "    def __init__(self) -> None:\n"
            "        self.e = 5\n"
            "    def get(self) -> Expr:\n"
            "        return self.e\n"
            "def ng_call_subject(b: Box) -> None:\n"
            "    match b.get():\n"
            "        case list() as xs:\n"
            "            xs.append(7)\n"
            "        case _:\n"
            "            pass\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.wrapper_borrow_source")
