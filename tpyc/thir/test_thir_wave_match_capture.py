"""Class-pattern KEYWORD captures of non-scalar fields, plus the value-tuple
match branch-decl hoist.

A keyword capture joins the arm walk as an ordinary declared local, so the
admitted field families are the ones the name-read classification already
covers. Records, list/dict/set and value tuples bind the plain `auto&` alias
into the subject's field, which is the same render a scalar capture takes.

Two shapes stay out. An Optional field capture binds a hoisted pointer, and a
capture the match itself hoisted into a POINTER local binds by ADDRESS
(`q = &(__match_subject_1.inner);`) -- both their own rungs. The pointer-hoist
check has to run at lowering: the hoist registers `lc.pointers` only after the
arm gate has already seen a pointer snapshot.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (_lower_ctx, _fn, _assert_byte_identical, _compile,
                       _entry)
from ..codegen_cpp import CodeGenOptions


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


def _fallback(src: str) -> dict:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_HOLDER = (
    "from tpy import Int32\n"
    "class Inner:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
    "class Holder:\n"
    "    inner: Inner\n"
    "    items: list[Int32]\n"
    "    pair: tuple[Int32, Int32]\n"
    "    opt: Inner | None\n"
    "    kind: Int32\n"
    "    def __init__(self, k: Int32) -> None:\n"
    "        self.inner = Inner(1)\n"
    "        self.items = [1, 2]\n"
    "        self.pair = (3, 4)\n"
    "        self.opt = None\n"
    "        self.kind = k\n"
)


class TestKeywordCaptureFamilies:
    def test_record_field_capture_aliases(self):
        src = _HOLDER + ("def f(h: Holder) -> Int32:\n"
                         "    match h:\n"
                         "        case Holder(inner=q):\n"
                         "            q.n = 42\n"
                         "            return q.n\n"
                         "    return -1\n")
        body = _body(_lower_ctx(src), "f")
        assert "auto& q = __match_subject_1.inner;" in body
        _assert_byte_identical(src)

    def test_container_field_capture_aliases(self):
        src = _HOLDER + ("def f(h: Holder) -> Int32:\n"
                         "    match h:\n"
                         "        case Holder(items=xs):\n"
                         "            xs.append(9)\n"
                         "            return len(xs)\n"
                         "    return -1\n")
        assert "auto& xs = __match_subject_1.items;" in _body(_lower_ctx(src), "f")
        _assert_byte_identical(src)

    def test_value_tuple_field_capture(self):
        src = _HOLDER + ("def f(h: Holder) -> Int32:\n"
                         "    match h:\n"
                         "        case Holder(pair=p):\n"
                         "            return p[0] + p[1]\n"
                         "    return -1\n")
        assert "auto& p = __match_subject_1.pair;" in _body(_lower_ctx(src), "f")
        _assert_byte_identical(src)

    def test_optional_field_capture_still_defers(self):
        src = _HOLDER + ("def f(h: Holder) -> Int32:\n"
                         "    match h:\n"
                         "        case Holder(opt=o):\n"
                         "            if o is not None:\n"
                         "                return o.n\n"
                         "            return 0\n"
                         "    return -1\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_pointer_hoisted_capture_still_defers(self):
        # `q` is read after the match, so the hoist takes the `Inner* q;`
        # pointer form and the bind is an address-of.
        src = _HOLDER + ("def f(h: Holder) -> Int32:\n"
                         "    match h:\n"
                         "        case Holder(kind=0, inner=q):\n"
                         "            q.n = 42\n"
                         "        case _:\n"
                         "            return -1\n"
                         "    return q.n\n")
        assert "body:match.field_bind_ptr_hoist" in _fallback(src)


class TestMatchValueTupleHoist:
    def test_value_tuple_branch_decl_hoists(self):
        src = _HOLDER + ("def f(h: Holder) -> Int32:\n"
                         "    match h:\n"
                         "        case Holder(kind=0):\n"
                         "            t = (7, 8)\n"
                         "        case _:\n"
                         "            t = (1, 2)\n"
                         "    return t[0] + t[1]\n")
        body = _body(_lower_ctx(src), "f")
        assert "std::tuple<int32_t, int32_t> t;" in body
        _assert_byte_identical(src)

    def test_container_branch_decl_still_defers(self):
        # The list hoist takes the optional-storage / rebind-slot forms,
        # neither of which this row builds.
        src = _HOLDER + ("def f(h: Holder) -> Int32:\n"
                         "    match h:\n"
                         "        case Holder(kind=0):\n"
                         "            xs = [1, 2]\n"
                         "        case _:\n"
                         "            xs = [3]\n"
                         "    return len(xs)\n")
        assert _fn(_lower_ctx(src), "f") is None
