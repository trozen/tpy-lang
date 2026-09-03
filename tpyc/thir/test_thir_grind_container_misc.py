"""Three remaining baseline shapes: the bare NAME expression statement, a
RECORD argument at an explicitly spelled user dunder, and the plain upcast
into a pointer-repr `Optional[record]` local -- plus the NUL-`Char`
truthiness divergence, pinned where it is visible."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
    _reject_tally,
)

_BARE_NAME_SRC = (
    "from tpy import Int32\n"
    "def f(n: Int32) -> None:\n"
    "    x = n + 1\n"
    "    x\n"
    "    print(x)\n"
    "def main() -> None:\n"
    "    f(2)\n"
    "main()\n"
)

_DUNDER_SRC = (
    "from tpy import Int32\n"
    "class Acc:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.v = v\n"
    "    def __add__(self, other: 'Acc') -> Int32:\n"
    "        return self.v + other.v\n"
    "def total(a: Acc, b: Acc) -> Int32:\n"
    "    return a.__add__(b)\n"
    "def main() -> None:\n"
    "    print(total(Acc(1), Acc(2)))\n"
    "main()\n"
)

_UPCAST_SRC = (
    "class Pet:\n"
    "    name: str\n"
    "    def __init__(self, name: str) -> None:\n"
    "        self.name = name\n"
    "class Dog(Pet):\n"
    "    def __init__(self, name: str) -> None:\n"
    "        super().__init__(name)\n"
    "def upcast(d: Dog) -> None:\n"
    "    p: Pet | None = d\n"
    "    if p is not None:\n"
    "        print(p.name)\n"
    "def main() -> None:\n"
    "    upcast(Dog(\"rex\"))\n"
    "main()\n"
)


class TestBareNameStatement:
    def test_routes(self):
        thir, faces = _lower_ctx_witnessed(_BARE_NAME_SRC)
        assert _fn(thir, "f") is not None
        assert faces["expr_stmt.name"] == 1

    def test_renders_the_discarded_read(self):
        cpp = _assert_routes_byte_identical(_BARE_NAME_SRC, comments=False)[1]
        assert "\n    x;\n" in cpp

    def test_bare_field_read_statement_keeps_rejecting(self):
        src = ("from tpy import Int32\n"
               "class Holder:\n"
               "    n: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 1\n"
               "def f(h: Holder) -> None:\n"
               "    h.n\n"
               "    print(h.n)\n"
               "def main() -> None:\n"
               "    f(Holder())\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.expr_stmt",
                           shape="expr_stmt.field_access")


class TestDunderRecordArg:
    def test_routes(self):
        thir, faces = _lower_ctx_witnessed(_DUNDER_SRC)
        assert _fn(thir, "total") is not None
        assert faces["method.ptr_template_record_arg"] >= 1

    def test_renders_the_operator_template(self):
        cpp = _assert_routes_byte_identical(_DUNDER_SRC)[1]
        assert "return (a) + (b);" in cpp

    def test_record_rvalue_arg_keeps_rejecting(self):
        # The adjacent shape: a ctor rvalue is not a bare-rendering NAME, and
        # the template slot has no temp to hoist into.
        src = _DUNDER_SRC.replace("    return a.__add__(b)\n",
                                  "    return a.__add__(Acc(b.v))\n")
        _assert_rejects_at(_reject_tally(src), "body:expr.method_call",
                           shape="method.ptr_template.arg_shape")

    def test_pointer_bound_name_arg_rejects(self):
        # The adjacent BINDING shape: a reassigned record alias binds a
        # pointer, so its read is `(*p)` -- not the bare name the template
        # slot interpolates.
        src = ("from tpy import Int32\n"
               "class Acc:\n    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               "    def __add__(self, other: 'Acc') -> Int32:\n"
               "        return self.v + other.v\n"
               "def total(a: Acc, b: Acc, c: Acc, flip: bool) -> Int32:\n"
               "    p = b\n"
               "    if flip:\n        p = c\n"
               "    return a.__add__(p)\n"
               "def main() -> None:\n"
               "    print(total(Acc(1), Acc(2), Acc(3), True))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:expr.method_call",
                           shape="method.ptr_template.arg_shape")


class TestUpcastIntoOptionalLocal:
    def test_routes(self):
        thir, faces = _lower_ctx_witnessed(_UPCAST_SRC)
        assert _fn(thir, "upcast") is not None
        assert faces["decl.opt_name_addr"] >= 1

    def test_renders_the_address_of_lift(self):
        cpp = _assert_routes_byte_identical(_UPCAST_SRC)[1]
        assert "Pet* p = &(d);" in cpp

    def test_reassigned_upcast_local_keeps_rejecting(self):
        # A reseat needs the slot machinery this row declares none of.
        src = _UPCAST_SRC.replace(
            "def upcast(d: Dog) -> None:\n"
            "    p: Pet | None = d\n",
            "def upcast(d: Dog, e: Dog) -> None:\n"
            "    p: Pet | None = d\n"
            "    p = e\n").replace("    upcast(Dog(\"rex\"))\n",
                                   "    upcast(Dog(\"rex\"), Dog(\"fido\"))\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.var_decl",
                           shape="decl.slot_type")


class TestCharTruthinessDivergence:
    def test_nul_char_condition_renders_the_scalar_test(self):
        # NOT an endorsement: `Char` lowers to C++ `char`, so a bare `if (c)`
        # is the `!= 0` test and a NUL Char takes the else branch, while
        # CPython (and this project's own str-like truthiness rule) make
        # every Char truthy -- see BUGS.md#char-nul-truthiness-diverges.
        # Pinned here so the render is visible at the arm rather than only
        # in a corpus output nobody writes with NUL in it; when the bug is
        # fixed this pin fails and names the entry to retire.
        src = ("from tpy import Char\n"
               "def main() -> None:\n"
               "    nul = Char(\"\\0\")\n"
               "    if nul:\n        print(\"t\")\n"
               "    else:\n        print(\"f\")\n"
               "main()\n")
        cpp = _assert_routes_byte_identical(src, comments=False)[1]
        assert "if (nul) {" in cpp
