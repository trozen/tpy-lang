"""The ifexpr Form-threading wave: per-arm form normalization for ternaries.

A pointer-repr Optional[F1-record] ternary admits with each arm normalized
to the `T*` the result renders as (nullptr / bare pointer / optional_to_ptr
lift / address-of -- _gen_if_expr's `_ptr_optional_branch` mirror), and a
plain F1-record ternary of lvalue NAME arms renders bare (a C++ lvalue).
The whole ternary carries Form.BORROW, consumed by the OPTIONAL_TO_PTR decl
row, the ptr-opt return row, and the Own-slot copy temp. Two rider rows
landed with the same wave: the bare BORROW-returning ptr-opt free-call decl
bind and the whole ptr-opt name print (`::tpy::print_optional`).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _reject_tally,
    _compile,
    _entry,
)


def _gen(source):
    """`(compiler, (hpp, cpp))` from one emit -- the compiler is kept for the
    face-witness reads."""
    compiler, modules = _compile(source)
    return compiler, compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False))


_PRE = (
    "class Box:\n"
    "    val: int\n"
    "    def __init__(self, v: int) -> None:\n"
    "        self.val = v\n"
    "class Holder:\n"
    "    opt: Box | None\n"
    "    def __init__(self, b: Box | None) -> None:\n"
    "        self.opt = b\n"
)


class TestPtrOptTernaryMixedFormDecl:
    # The borrow-arm/storage-arm ternary at an OPTIONAL_TO_PTR decl: the
    # param arm passes bare, the field arm lifts, the binding binds the
    # lowered ternary bare.
    SRC = (
        _PRE +
        "def bump(p: Box | None, h: Holder, c: bool) -> None:\n"
        "    t = p if c else h.opt\n"
        "    if t is not None:\n"
        "        t.val += 100\n"
        "def main() -> None:\n"
        "    h = Holder(Box(7))\n"
        "    b = Box(3)\n"
        "    bump(b, h, True)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)
        assert ("Box* t = ((c) ? (p) : (::tpy::optional_to_ptr(h.opt)));"
                in thir[1])
        w = compiler._thir_face_witnesses
        assert w.get("ifexpr.ptr_opt", 0) >= 1
        assert w.get("decl.opt_ternary", 0) >= 1


class TestPtrOptTernaryReturns:
    # The two return shapes: a plain-record arm takes the address-of and a
    # None arm renders nullptr; two already-pointer Optional params pass
    # bare.
    SRC = (
        _PRE +
        "def get_or_none(flag: bool, p: Box) -> Box | None:\n"
        "    return p if flag else None\n"
        "def pick(flag: bool, a: Box | None, b: Box | None) -> Box | None:\n"
        "    return a if flag else b\n"
        "def main() -> None:\n"
        "    p = Box(1)\n"
        "    r1 = get_or_none(True, p)\n"
        "    if r1 is not None:\n"
        "        print(r1.val)\n"
        "    r3 = pick(True, r1, None)\n"
        "    if r3 is not None:\n"
        "        print(r3.val)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)
        assert "return ((flag) ? (&(p)) : (nullptr));" in thir[1]
        assert "return ((flag) ? (a) : (b));" in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("ret.ptr_opt_ternary", 0) >= 2


class TestRecordLvalueTernaryIntoOwn:
    # A plain-record both-lvalue ternary at an Own[T] arg: the ternary is a
    # BORROW lvalue the Own-slot COPY half captures into its `auto __tmp_N`
    # (the acknowledged copy-into-owned divergence, warned at sema).
    SRC = (
        "from tpy import Own\n" +
        _PRE +
        "def take(b: Own[Box]) -> Own[Box]:\n"
        "    b.val += 1\n"
        "    return b\n"
        "def main() -> None:\n"
        "    a = Box(10)\n"
        "    other = Box(20)\n"
        "    flag = True\n"
        "    r = take(a if flag else other)\n"
        "    print(r.val)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)
        assert "auto __tmp_1 = ((flag) ? (a) : (other));" in thir[1]
        assert "take(std::move(__tmp_1));" in thir[1]
        assert compiler._thir_face_witnesses.get("ifexpr.record", 0) >= 1


class TestOptCallPassthroughDeclAndPrint:
    # The two rider rows: a BORROW-returning ptr-opt free call binds bare at
    # its decl (no slot materializes), and the whole un-narrowed name prints
    # via `::tpy::print_optional`.
    SRC = (
        _PRE +
        "def get_or_none(flag: bool, p: Box) -> Box | None:\n"
        "    return p if flag else None\n"
        "def main() -> None:\n"
        "    p = Box(1)\n"
        "    r2 = get_or_none(False, p)\n"
        "    print(r2)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)
        assert "Box* r2 = get_or_none(false, p);" in thir[1]
        assert "::tpy::print_optional(r2)" in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("decl.opt_call_passthrough", 0) >= 1
        assert w.get("print.opt_ptr_name", 0) >= 1


class TestRecordTernaryCallArmDefers:
    # A record ternary with a BORROW-returning call arm now routes: the
    # `T&` result is an lvalue like the name arm, so the ternary stays an
    # lvalue -- the record-arm slice admits name and borrow-call arms
    # (value-returning call arms would make a prvalue and keep rejecting).
    SRC = (
        _PRE +
        "def trusted(b: Box) -> Box:\n"
        "    return b\n"
        "def choose(a: Box, c: bool) -> int:\n"
        "    t = a if c else trusted(a)\n"
        "    return t.val\n"
        "def main() -> None:\n"
        "    print(choose(Box(1), True))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, thir = _gen(self.SRC)


class TestPtrOptTernaryCallArmRoutes:
    # An Optional METHOD-CALL arm passes its `T*` result bare (the
    # pointee-accessor wave's ifexpr.optptr_call_arm row).
    SRC = (
        _PRE.replace(
            "    def __init__(self, b: Box | None) -> None:\n"
            "        self.opt = b\n",
            "    def __init__(self, b: Box | None) -> None:\n"
            "        self.opt = b\n"
            "    def grab(self) -> Box | None:\n"
            "        return self.opt\n") +
        "def pick(p: Box | None, h: Holder, c: bool) -> Box | None:\n"
        "    return p if c else h.grab()\n"
        "def main() -> None:\n"
        "    r = pick(Box(1), Holder(Box(2)), True)\n"
        "    if r is not None:\n"
        "        print(r.val)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)


class TestContainerElemTernaryStaysOut:
    # BOUNDARY: a ternary ELEMENT inside a container literal of optional
    # elements must not take the BORROW normalization -- the AST's
    # in_container_element carve-out renders storage there.
    #
    # That AST render is BROKEN for this shape and the element gate is what
    # keeps THIR away from it: `std::optional<Box>(p)` over a `const Box*`
    # param arm does not compile (the union twin of the carve-out lifts its
    # arms via to_value_variant; the Optional twin never got that row). A
    # RECORD-inner Optional is therefore outside the element-ternary family
    # -- only the dict and value-tuple inners, whose arms are self-contained
    # values, are admitted (test_thir_wave_optternary_elem.py).
    SRC = (
        _PRE +
        "def build(p: Box | None, h: Holder, c: bool) -> None:\n"
        "    xs: list[Box | None] = [p if c else h.opt]\n"
        "    print(len(xs))\n"
        "def main() -> None:\n"
        "    h = Holder(Box(7))\n"
        "    b = Box(3)\n"
        "    build(b, h, True)\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        fb = _reject_tally(self.SRC)
        assert fb.get("body:expr.container_literal") == 1, fb


class TestOwnDeclaredOptCallSlotLift:
    # An Own-declared `Own[Box | None]` callee OWNS its returned storage:
    # the bare-bind passthrough row must not capture it (a bare `T*` bind
    # would point into a dying temp) -- the OPT_STORAGE_CALL slot
    # materializes the optional and lifts the pointer instead.
    SRC = (
        "from tpy import Own\n" +
        _PRE +
        "def make(c: bool) -> Own[Box | None]:\n"
        "    if c:\n"
        "        return Box(5)\n"
        "    return None\n"
        "def main() -> None:\n"
        "    r = make(True)\n"
        "    if r is not None:\n"
        "        print(r.val)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)
        assert "::tpy::optional_to_ptr(__slot_" in thir[1]


class TestContainerLvalueTernary:
    # A container-RESULT ternary of NAME arms is a BORROW lvalue ternary
    # (`((flag) ? (xs) : (ys))`): a decl init binds the REFERENCE (`&` --
    # Python aliasing), native protocol slots (len) bind it bare. Non-name arms and plain-TPy callee slots keep
    # rejecting.
    _PRE = "from tpy import Int32\n\n"

    def test_decl_and_len_route(self):
        from .testutil import _assert_routes_byte_identical
        src = (self._PRE
               + "def main() -> None:\n"
               + "    xs: list[Int32] = [1, 2]\n"
               + "    ys: list[Int32] = [3]\n"
               + "    flag = True\n"
               + "    zs = xs if flag else ys\n"
               + "    print(len(zs))\n"
               + "    print(len(xs if flag else ys))\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::vector<int32_t>& zs = ((flag) ? (xs) : (ys));" in cpp
        assert "::tpy::__len__(((flag) ? (xs) : (ys)))" in cpp

    def test_plain_callee_slot_defers(self):
        # The plain-loop arg ladder does not admit the ternary shape -- a
        # user fn's list param keeps the body AST-side.
        src = (self._PRE
               + "def total(items: list[Int32]) -> Int32:\n"
               + "    t: Int32 = 0\n"
               + "    for x in items:\n        t += x\n"
               + "    return t\n"
               + "def main() -> None:\n"
               + "    xs: list[Int32] = [1, 2]\n"
               + "    ys: list[Int32] = [3]\n"
               + "    print(total(xs if False else ys))\n"
               + "main()\n")
        fb = _reject_tally(src)
        assert any("call.arg_shape.container" in k for k in fb), fb

    def test_for_head_ternary_iterable_defers(self):
        # The for-head iterable position keeps its own gate.
        src = (self._PRE
               + "def main() -> None:\n"
               + "    xs: list[Int32] = [1]\n"
               + "    ys: list[Int32] = [2]\n"
               + "    for v in (xs if True else ys):\n"
               + "        print(v)\n"
               + "main()\n")
        fb = _reject_tally(src)
        assert any("iter.if_expr_shape" in k for k in fb), fb
