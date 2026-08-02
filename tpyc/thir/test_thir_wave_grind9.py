"""Wave 9 of the grind loop: comp tuple-unpack heads with record elements,
plus the storage-form tuple RETURN at an Own element slot.

Two pieces landed together (the same case exercised both):
`_comp_route`'s unpack arm admits record-element tuple iterables
(`allow_record`, the for-head unpack's knob) with the per-target ref
binding keyed on `const_loop_var` alone -- the AST's
`_emit_inline_tuple_unpack` spells `auto&` / `const auto&` for every
non-value target off that one flag, with no const-only admission. And
`_lower_call_arg`'s Own[ptr-repr tuple] slot arm passes a STORAGE-form
tuple return (`Own[tuple[..]]` / all-Own per-element synthesis) bare
instead of wrapping it in `tuple_to_storage` -- the AST's
`needs_tuple_storage_lift` verdict for a call source; only borrow-form
and MIXED-render returns owe the copy lift.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _thir_fallbacks(source):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_NODE = (
    "from tpy import Int32, Own\n"
    "from tplib import Rc\n"
    "class Node:\n"
    "    value: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.value = v\n"
)


class TestCompUnpackRecordTarget:
    SRC = (
        _NODE +
        "def make_pair(i: Int32, v: Int32) -> Own[tuple[Int32, Rc[Node]]]:\n"
        "    return (i, Rc.new(Node(v)))\n"
        "def main() -> None:\n"
        "    pairs: list[tuple[Int32, Rc[Node]]] = []\n"
        "    pairs.append(make_pair(1, 10))\n"
        "    clones: list[Rc[Node]] = [r.clone() for (_, r) in pairs]\n"
        "    for c in clones:\n"
        "        print(c.get().value)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The cloning body mutates the Rc handle, so the unpack head and
        # the record target both drop const.
        assert "auto& __tup_1 = *__beg_0;" in cpp
        assert "auto& r = std::get<1>(__tup_1);" in cpp
        # The Own[tuple] return already matches the owning slot: bare bind.
        assert "pairs.push_back(make_pair(1, 10));" in cpp
        assert "::tpy::tuple_to_storage" not in cpp

    def test_faces(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("comp.unpack", 0) >= 1
        assert w.get("arg.own_btuple_call_storage", 0) >= 1


class TestCompUnpackRecordTargetConst:
    # const_loop_var turns on `worth_const_ref` (the str element makes the
    # tuple expensive-copy) + no target mutation: the head AND the record
    # target both spell `const auto&` -- the ref binding is keyed on that
    # one flag, exactly like the AST's `_emit_inline_tuple_unpack`.
    SRC = (
        "from tpy import Int32\n"
        "from tplib import Rc\n"
        "class Node:\n"
        "    value: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.value = v\n"
        "def main() -> None:\n"
        "    pairs: list[tuple[str, Rc[Node]]] = []\n"
        "    names: list[str] = [k for (k, r) in pairs]\n"
        "    print(len(names))\n"
        "main()\n"
    )

    def test_const_source_keeps_const_binding(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "const auto& __tup_1 = *__beg_0;" in cpp
        assert "const auto& r = std::get<1>(__tup_1);" in cpp


class TestCompUnpackUnionElemRejects:
    # A pointer-variant UNION element target is outside the admitted set
    # (scalar / owned-str / F1 record): the body stays on the AST path.
    SRC = (
        "from tpy import Int32\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class B:\n"
        "    y: Int32\n"
        "    def __init__(self, y: Int32) -> None:\n"
        "        self.y = y\n"
        "def f(pairs: list[tuple[Int32, A | B]]) -> Int32:\n"
        "    return len([u for (_, u) in pairs])\n"
        "print(f([]))\n"
    )

    def test_union_elem_target_falls_back(self):
        fell = _thir_fallbacks(self.SRC)
        assert any(k.startswith("body:") for k in fell), fell


class TestBorrowTupleReturnStillLifts:
    # The wrap-side boundary of the storage-return split: a call returning
    # the BORROW-form `tuple[P | None, P | None]` (no Own) still takes the
    # non-move `tuple_to_storage` at the owning element slot.
    SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def make_pair(left: P, right: P) -> tuple[P | None, P | None]:\n"
        "    return (left, right)\n"
        "def f() -> None:\n"
        "    a = P(1)\n"
        "    b = P(2)\n"
        "    pairs: list[tuple[P | None, P | None]] = []\n"
        "    pairs.append(make_pair(a, b))\n"
        "    print(len(pairs))\n"
        "f()\n"
    )

    def test_borrow_return_keeps_the_copy_lift(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "::tpy::tuple_to_storage<" in cpp
        assert "tuple_to_storage_move" not in cpp

    def test_faces(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("arg.own_btuple_call", 0) >= 1
        assert w.get("arg.own_btuple_call_storage", 0) == 0



_RC_NODE = (
    "from tpy import Int32, Own\n"
    "from tplib import Rc\n"
    "class Node:\n"
    "    value: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.value = v\n"
)


class TestPerElementOwnReturnsStillReject:
    # `_storage_form_tuple_return`'s second branch (a bare tuple with Own
    # elements) and its mixed-own exclusion are currently UNREACHABLE
    # through the Own-slot arg gate: `_own_tuple_call_rvalue_arg` compares
    # the arg type against the slot with the per-element Own still on, so
    # these bodies fall back whole before the arg lowers. Pin today's
    # boundary so a future admission widening converts these consciously.

    ALL_OWN = (
        _RC_NODE +
        "def make_two(a: Int32, b: Int32) "
        "-> tuple[Own[Rc[Node]], Own[Rc[Node]]]:\n"
        "    return (Rc.new(Node(a)), Rc.new(Node(b)))\n"
        "def main() -> None:\n"
        "    pairs: list[tuple[Rc[Node], Rc[Node]]] = []\n"
        "    pairs.append(make_two(1, 2))\n"
        "    print(len(pairs))\n"
        "main()\n"
    )

    MIXED_OWN = (
        _RC_NODE +
        "def make_mixed(n: Node, v: Int32) -> tuple[Own[Rc[Node]], Node]:\n"
        "    return (Rc.new(Node(v)), n)\n"
        "def main() -> None:\n"
        "    n = Node(7)\n"
        "    pairs: list[tuple[Rc[Node], Node]] = []\n"
        "    pairs.append(make_mixed(n, 1))\n"
        "    print(len(pairs))\n"
        "main()\n"
    )

    def test_all_own_return_falls_back(self):
        fell = _thir_fallbacks(self.ALL_OWN)
        assert any(k.startswith("body:") for k in fell), fell

    def test_mixed_own_return_falls_back(self):
        fell = _thir_fallbacks(self.MIXED_OWN)
        assert any(k.startswith("body:") for k in fell), fell
