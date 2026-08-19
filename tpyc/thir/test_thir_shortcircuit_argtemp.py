"""The short-circuit fence over arg faces that hoist a `THIRArgTemp`.

An `and`/`or` operand evaluates CONDITIONALLY, so a hoisted arg temp there
would run at the enclosing statement even when the operand is skipped. The
flush right therefore stops at the logical binop (`_lower_binop`), exactly as
it stops at a ternary's arms -- a temp-needing operand raises `ThirUnsupported`
(whole-body fallback) instead of building a node the post-lowering validator
rejects with a hard `THIRValidationError`, which escapes the per-body fallback
boundary and kills the compile.

Each face gets a fence pin (identity, no crash, body rejected at the call) and
an inverse pin proving the same face still ROUTES from a flushable position.
Deleting the fence turns every fence pin into an error, not a silent pass.
"""

from .testutil import (_assert_byte_identical, _assert_routes_byte_identical,
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


def _fallback(source: str) -> dict:
    compiler, modules = _compile(source)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


def _assert_fenced(source: str, reject: str = "body:expr.call") -> None:
    """The fence claim: no validator crash, the enclosing body rejects at the
    call, and the emitted C++ still matches the AST oracle byte for byte."""
    assert _fallback(source).get(reject) == 1
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

    def test_and_rhs_is_fenced(self):
        _assert_fenced(self.AND)

    def test_or_rhs_is_fenced(self):
        _assert_fenced(self.OR)

    def test_lhs_is_fenced(self):
        # The LHS evaluates unconditionally, so its temp would be sound to
        # hoist -- but the validator rejects an arg temp under EITHER logical
        # operand, so the fence has to cover both to stay crash-free.
        _assert_fenced(self.LHS)

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

    def test_and_rhs_is_fenced(self):
        _assert_fenced(self.AND)

    def test_or_rhs_is_fenced(self):
        _assert_fenced(self.OR)

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

    def test_and_rhs_is_fenced(self):
        _assert_fenced(self.AND, "body:expr.method_call")

    def test_or_rhs_is_fenced(self):
        _assert_fenced(self.OR, "body:expr.method_call")

    def test_flushable_position_still_routes(self):
        _assert_routes_byte_identical(self.PLAIN)

    def test_inverse_witnesses_the_same_face(self):
        _, witnesses = _lower_ctx_witnessed(self.PLAIN)
        assert witnesses.get("optptr.scalar_temp", 0) == 1


class TestOwnCopyFace:
    """`argtemp.own_copy`: a borrowed name at an `Own[T]` slot copies into a
    temp and moves the temp in."""

    AND = _OWN_ARG + ("def f(b: bool, xs: list[Int32]) -> bool:\n"
                      "    return b and take(xs)\n") + _MAIN
    OR = _OWN_ARG + ("def f(b: bool, xs: list[Int32]) -> bool:\n"
                     "    return b or take(xs)\n") + _MAIN
    PLAIN = _OWN_ARG + ("def f(xs: list[Int32]) -> bool:\n"
                        "    return take(xs)\n") + _MAIN

    def test_and_rhs_is_fenced(self):
        _assert_fenced(self.AND)

    def test_or_rhs_is_fenced(self):
        _assert_fenced(self.OR)

    def test_flushable_position_still_routes(self):
        _assert_routes_byte_identical(self.PLAIN)

    def test_inverse_witnesses_the_same_face(self):
        _, witnesses = _lower_ctx_witnessed(self.PLAIN)
        assert witnesses.get("argtemp.own_copy", 0) == 1


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


class TestTernaryArmStaysFenced:
    """The pre-existing sibling fence: a ternary ARM never receives the flush
    right either. Boundary pin -- it must keep rejecting, not start routing."""

    SRC = _REC + ("def f(b: bool) -> bool:\n"
                  "    return take(A(7)) if b else False\n") + _MAIN

    def test_arm_is_fenced(self):
        _assert_fenced(self.SRC)


class TestIfConditionUnchanged:
    """An `if` condition reaches the same faces through `_lower_truthy`, which
    never threaded the flush right into logical operands -- pinned so the
    binop-level fence is not mistaken for the only guard."""

    SRC = _REC + ("def f(b: bool) -> bool:\n"
                  "    if b and take(A(7)):\n"
                  "        return True\n"
                  "    return False\n") + _MAIN

    def test_condition_is_fenced(self):
        assert _fallback(self.SRC).get(
            "body:stmt.if:call.arg_shape.optional") == 1
        _assert_byte_identical(self.SRC)
