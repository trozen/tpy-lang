"""Conditional-operand arg temps over the optptr faces.

An `and`/`or` RHS evaluates CONDITIONALLY. An AUDITED temp row (movable fact
mirrored off the AST creator) routes: the emit opens the same
`conditional_region` the AST does, so a movable+spellable temp DEFERS into
the operand (`std::optional<T> __tmp_N;` + the spliced emplace) and a
non-deferring one hoists eagerly at the statement. The LHS always evaluates:
its temp hoists at the statement (or banks into an enclosing region when the
logical nests inside a conditional operand). An UNAUDITED row that might
defer keeps rejecting (`argtemp.cond_defer` at the `_lower_expr` exit check,
whole-body fallback) instead of building a node the post-lowering validator
rejects with a hard `THIRValidationError`.

Each face keeps its flushable-position inverse pin; the conditional shapes
are routing pins with the deferred/eager render asserted.
"""

from .testutil import (
    _assert_rejects_at,
    _reject_tally, _assert_byte_identical, _assert_routes_byte_identical,
                       _compile, _entry, _lower_ctx_witnessed)
from ..codegen_cpp.context import CodeGenOptions

_MAIN = "\ndef main() -> None:\n    pass\nmain()\n"

_REC = ("from tpy import Int32\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def take(o: A | None) -> bool:\n"
        "    return o is not None\n")

_CONTAINER_ARG = ("from tpy import Int32\n"
                  "def take(o: list[Int32] | None) -> bool:\n"
                  "    return o is not None\n")

_OWN_ARG = ("from tpy import Int32, Own\n"
            "def take(o: Own[list[Int32]]) -> bool:\n"
            "    return True\n")

_SCALAR_BOX = ("from tpy import Int32\n"
               "class Box[T]:\n"
               "    _val: T | None\n"
               "    def __init__(self, val: T | None):\n"
               "        self._val = val\n"
               "    def probe(self, val: T | None) -> bool:\n"
               "        return val is not None\n")


def _reject_tags(source: str) -> dict:
    return _reject_tally(source)


def _assert_fenced(source: str, reject: str = "body:expr.call") -> None:
    """The fence claim: no validator crash, the enclosing body rejects at the
    call, and the emitted C++ still matches the AST oracle byte for byte."""
    assert _reject_tags(source).get(reject) == 1
    _assert_byte_identical(source)


# The four faces that reach the arg-temp hoist, each in an `and` RHS, an `or`
# RHS, and -- for the first -- the unconditional LHS the validator rejects too.

class TestCtorRvalueFace:
    """`optptr.ctor_rvalue`: a record ctor rvalue at a pointer-repr
    `Optional[record]` slot hoists `A __tmp_1 = A(7);` and passes its
    address."""

    AND = _REC + ("def f(b: bool) -> bool:\n"
                  "    return b and take(A(7))\n") + _MAIN
    OR = _REC + ("def f(b: bool) -> bool:\n"
                 "    return b or take(A(7))\n") + _MAIN
    LHS = _REC + ("def f(b: bool) -> bool:\n"
                  "    return take(A(7)) and b\n") + _MAIN
    PLAIN = _REC + ("def f() -> bool:\n"
                    "    return take(A(7))\n") + _MAIN

    def test_and_rhs_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.AND)
        assert "std::optional<A> __tmp_1;" in cpp
        assert "(b && (__tmp_1.emplace(A(7)), take(&((*__tmp_1)))))" in cpp

    def test_or_rhs_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.OR)
        assert "std::optional<A> __tmp_1;" in cpp
        assert "__tmp_1.emplace(A(7))" in cpp

    def test_lhs_hoists_eagerly(self):
        # The LHS always evaluates: its temp is the plain statement hoist.
        _hpp, cpp = _assert_routes_byte_identical(self.LHS)
        assert "A __tmp_1 = A(7);" in cpp
        assert "std::optional<A>" not in cpp

    def test_flushable_position_still_routes(self):
        _assert_routes_byte_identical(self.PLAIN)

    def test_inverse_witnesses_the_same_face(self):
        _, witnesses = _lower_ctx_witnessed(self.PLAIN)
        assert witnesses.get("optptr.ctor_rvalue", 0) == 1


class TestContainerTempFace:
    """`optptr.container_temp`: a container literal at a pointer-repr
    `Optional[list]` slot hoists the vector temp."""

    AND = _CONTAINER_ARG + ("def f(b: bool) -> bool:\n"
                            "    return b and take([1, 2, 3])\n") + _MAIN
    OR = _CONTAINER_ARG + ("def f(b: bool) -> bool:\n"
                           "    return b or take([1, 2, 3])\n") + _MAIN
    PLAIN = _CONTAINER_ARG + ("def f() -> bool:\n"
                              "    return take([1, 2, 3])\n") + _MAIN

    def test_and_rhs_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.AND)
        assert "std::optional<std::vector<int32_t>> __tmp_1;" in cpp

    def test_or_rhs_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.OR)
        assert "std::optional<std::vector<int32_t>> __tmp_1;" in cpp

    def test_flushable_position_still_routes(self):
        _assert_routes_byte_identical(self.PLAIN)

    def test_inverse_witnesses_the_same_face(self):
        _, witnesses = _lower_ctx_witnessed(self.PLAIN)
        assert witnesses.get("optptr.container_temp", 0) == 1


class TestScalarTempFace:
    """`optptr.scalar_temp`: a resolved-scalar rvalue at a pointer-repr
    `Optional[scalar]` slot hoists a typed temp and lifts its address."""

    AND = _SCALAR_BOX + ("def g(c: Box[Int32], b: bool) -> bool:\n"
                         "    return b and c.probe(Int32(1))\n"
                         "def f() -> None:\n"
                         "    c = Box[Int32](None)\n"
                         "    print(g(c, True))\n") + _MAIN
    OR = _SCALAR_BOX + ("def g(c: Box[Int32], b: bool) -> bool:\n"
                        "    return b or c.probe(Int32(1))\n"
                        "def f() -> None:\n"
                        "    c = Box[Int32](None)\n"
                        "    print(g(c, True))\n") + _MAIN
    PLAIN = _SCALAR_BOX + ("def g(c: Box[Int32]) -> bool:\n"
                           "    return c.probe(Int32(1))\n"
                           "def f() -> None:\n"
                           "    c = Box[Int32](None)\n"
                           "    print(g(c))\n") + _MAIN

    def test_and_rhs_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.AND)
        assert "std::optional<int32_t> __tmp_1;" in cpp + _hpp

    def test_or_rhs_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.OR)
        assert "std::optional<int32_t> __tmp_1;" in cpp + _hpp

    def test_flushable_position_still_routes(self):
        _assert_routes_byte_identical(self.PLAIN)

    def test_inverse_witnesses_the_same_face(self):
        _, witnesses = _lower_ctx_witnessed(self.PLAIN)
        assert witnesses.get("optptr.scalar_temp", 0) == 1


class TestOwnCopyFace:
    """`argtemp.own_copy`: a borrowed name at an `Own[T]` slot copies into a
    temp and moves the temp in. The temp is `auto`-spelled, so the AST keeps
    it EAGER even inside a conditional region (`std::optional<auto>` is not
    a spelling) -- the RHS shapes now ROUTE through the eager-only right,
    byte-identically (the copy running in a skipped branch is the AST's own
    documented residue, BUGS.md)."""

    AND = _OWN_ARG + ("def f(b: bool, xs: list[Int32]) -> bool:\n"
                      "    return b and take(xs)\n") + _MAIN
    OR = _OWN_ARG + ("def f(b: bool, xs: list[Int32]) -> bool:\n"
                     "    return b or take(xs)\n") + _MAIN
    PLAIN = _OWN_ARG + ("def f(xs: list[Int32]) -> bool:\n"
                        "    return take(xs)\n") + _MAIN

    def test_and_rhs_routes_eager(self):
        _assert_routes_byte_identical(self.AND)

    def test_or_rhs_routes_eager(self):
        _assert_routes_byte_identical(self.OR)

    def test_flushable_position_still_routes(self):
        _assert_routes_byte_identical(self.PLAIN)

    def test_inverse_witnesses_the_same_face(self):
        _, witnesses = _lower_ctx_witnessed(self.PLAIN)
        assert witnesses.get("argtemp.own_copy", 0) == 1


class TestMutableRefContainerLiteralDefers:
    """The plain mutable-ref container-literal temp row
    (`argtemp.container_literal`, audited movable off the slot) defers in a
    conditional operand like its optptr siblings."""

    SRC = ("from tpy import Int64\n"
           "def take_i64(xs: list[Int64]) -> Int64:\n"
           "    return len(xs)\n"
           "def f(flag: bool) -> bool:\n"
           "    return flag and take_i64([1, 2, 3]) > 0\n"
           "def main() -> None:\n    print(f(True))\nmain()\n")

    def test_and_rhs_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::optional<std::vector<int64_t>> __tmp_1;" in cpp
        assert "__tmp_1.emplace(std::vector<int64_t>{1, 2, 3})" in cpp


class TestGenericRefSlotDefers:
    """The generic ref-slot temp row (`argtemp.generic_ref_slot`, audited
    movable off the resolved slot) defers in a ternary arm."""

    SRC = ("from tpy import Int64\n"
           "def take_any[T](xs: T) -> Int64:\n"
           "    return 1\n"
           "def f(flag: bool) -> Int64:\n"
           "    return take_any([1, 2, 3]) if flag else 0\n"
           "def main() -> None:\n    print(f(True))\nmain()\n")

    def test_ternary_arm_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::optional<std::vector<int32_t>> __tmp_1;" in cpp
        assert ("(__tmp_1.emplace(std::vector<int32_t>{1, 2, 3}), "
                "take_any<std::vector<int32_t>>((*__tmp_1)))" in cpp)


class TestFenceDoesNotOverTrigger:
    """A short-circuit whose operand call needs NO temp keeps routing -- the
    fence drops the flush right, it does not reject the operand."""

    SRC = ("from tpy import Int32\n"
           "def take(n: Int32) -> bool:\n"
           "    return n > 0\n"
           "def f(b: bool, n: Int32) -> bool:\n"
           "    return b and take(n)\n") + _MAIN

    def test_routes(self):
        _assert_routes_byte_identical(self.SRC)


class TestTernaryArmDefers:
    """The ternary sibling: an audited movable temp in an ARM defers through
    the arm's own region (`(cond) ? (__tmp_1.emplace(..), ..) : (..)`)."""

    SRC = _REC + ("def f(b: bool) -> bool:\n"
                  "    return take(A(7)) if b else False\n") + _MAIN

    def test_arm_defers(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::optional<A> __tmp_1;" in cpp
        assert "? (__tmp_1.emplace(A(7)), take(&((*__tmp_1))))" in cpp


class TestIfConditionUnchanged:
    """An `if` condition reaches the same faces through `_lower_truthy`, which
    never threaded the flush right into logical operands -- pinned so the
    binop-level fence is not mistaken for the only guard."""

    SRC = _REC + ("def f(b: bool) -> bool:\n"
                  "    if b and take(A(7)):\n"
                  "        return True\n"
                  "    return False\n") + _MAIN

    def test_condition_is_fenced(self):
        _assert_rejects_at(_reject_tally(self.SRC),
                           "body:stmt.if:call.arg_shape.optional")
