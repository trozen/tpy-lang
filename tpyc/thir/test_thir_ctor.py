"""THIR constructor frontier M3: ctor MIL field cells + bodies + inheritance."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile, _entry, _lower_ctor, _ctor_tail, _PRELUDE,
)

class TestConstructor:
    """The M3a ctor frontier: pure-MIL scalar constructors of flat records --
    every `__init__` statement is a hoistable own-scalar field init, so the
    member-init-list is the whole body and the C++ body is `{}`."""

    _POINT = (
        _PRELUDE
        + "class Point:\n    x: Int32\n    y: Int32\n"
        + "    def __init__(self, x: Int32, y: Int32):\n"
        + "        self.x = x\n        self.y = y\n")

    def test_pure_scalar_ctor_routes(self):
        ctor = _lower_ctor(self._POINT, "Point")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x", "y"]
        assert ctor.body == ()  # pure-MIL: empty body

    def test_pure_scalar_ctor_tail_emit(self):
        ctor = _lower_ctor(self._POINT, "Point")
        assert _ctor_tail(ctor) == " : x(x), y(y) {}\n"

    def test_no_param_literal_inits_route(self):
        ctor = _lower_ctor(
            _PRELUDE
            + "class Counter:\n    n: Int32\n    step: Int32\n"
            + "    def __init__(self):\n        self.n = 0\n        self.step = 1\n",
            "Counter")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : n(0), step(1) {}\n"

    def test_field_init_from_sibling_field_routes(self):
        # An RHS reading a sibling scalar field renders `b(this->a)` (the corpus
        # byte-diff validates this against the AST path).
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        self.a = a\n        self.b = self.a\n",
            "C")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : a(a), b(this->a) {}\n"

    def test_char_field_mil_routes(self):
        # A Char field is a value-scalar-like MIL source: `c(c)`, the plain
        # scalar form (M3a widened to Char).
        ctor = _lower_ctor(
            "from tpy import Char, Int32\n"
            "class P:\n    c: Char\n    n: Int32\n"
            "    def __init__(self, c: Char, n: Int32):\n"
            "        self.c = c\n        self.n = n\n",
            "P")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : c(c), n(n) {}\n"

    def test_pass_body_ctor_routes(self):
        # M3c-trivia: `pass` is non-init trivia -- it stays in the body (so the
        # braces are ` {\n    }`, not ` {}`) but breaks no chain, so the field init
        # still hoists. It emits no code; its `loc` carries the `// pass` comment.
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n        pass\n",
            "P")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x"]
        assert len(ctor.body) == 1
        assert _ctor_tail(ctor) == " : x(x) {\n    }\n"

    def test_docstring_ctor_routes(self):
        # M3c-trivia: a docstring is non-init trivia too -- same body-brace effect,
        # chain intact. It emits neither code nor comment (loc=None).
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + '        """doc"""\n        self.x = x\n',
            "P")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x"]
        assert _ctor_tail(ctor) == " : x(x) {\n    }\n"

    def test_trivia_only_ctor_routes(self):
        # A ctor whose body is only trivia (no field inits) -- the body is
        # non-empty but emits nothing, so ` {\n    }` with no init list.
        ctor = _lower_ctor(
            _PRELUDE
            + "class E:\n    def __init__(self) -> None:\n        pass\n",
            "E")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert _ctor_tail(ctor) == " {\n    }\n"

    def test_trivia_comment_loc_asymmetry(self):
        # The byte-identity hinge: `pass` keeps its source loc (the AST emits its
        # `// pass` source comment), a docstring lowers with loc=None (the AST emits
        # NO comment for a docstring -- its simple-stmt code is None).
        pass_ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n        pass\n",
            "P")
        assert pass_ctor.body[0].loc is not None
        doc_ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + '        """doc"""\n        self.x = x\n',
            "P")
        assert doc_ctor.body[0].loc is None

    def test_non_init_print_body_routes(self):
        # A print() body statement is an eligible expression statement, so a ctor
        # with a hoistable field init + a print demotes cleanly: the field init
        # hoists to the MIL, the print rides the body (byte-identical ctor tail).
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        self.x = x\n        print(x)\n",
            "P")
        assert ctor is not None
        assert len(ctor.mil_inits) == 1  # self.x = x hoisted to the MIL
        assert _ctor_tail(ctor) == ' : x(x) {\n        std::cout << x << "\\n";\n    }\n'

    def test_non_init_self_method_call_body_routes(self):
        # A `self.reset()` body statement rides the self-receiver method-call
        # row (`this->reset();` in the ctor tail -- probe-verified
        # byte-identical to the AST render).
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        self.x = x\n        self.reset()\n"
            + "    def reset(self) -> None:\n        self.x = 0\n",
            "C")
        assert ctor is not None
        assert "this->reset();" in _ctor_tail(ctor)

    def test_body_local_demotion_routes(self):
        # M3c-demotion: a field init whose RHS reads a body-local can't hoist (the
        # local isn't in scope at MIL time), so it demotes into the body alongside
        # the local's var-decl. No MIL; the body holds both statements.
        ctor = _lower_ctor(
            _PRELUDE
            + "class W:\n    size: Int32\n"
            + "    def __init__(self, w: Int32, h: Int32):\n"
            + "        area = w * h\n        self.size = area\n",
            "W")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert len(ctor.body) == 2  # the var-decl + the demoted field write
        assert _ctor_tail(ctor) == (
            " {\n        int32_t area = (::tpy::mul_check<int32_t>(w, h));\n"
            "        this->size = area;\n    }\n")

    def test_chain_break_demotes_hoistable_init(self):
        # M3c-demotion: a non-init statement breaks the hoist chain, so a *hoistable*
        # field init after it must demote (the MIL runs before the body -- hoisting
        # would reorder it past the chain-breaking statement). `self.a` (before the
        # break) hoists; `self.b` (after) demotes.
        ctor = _lower_ctor(
            _PRELUDE
            + "def helper(x: Int32) -> Int32:\n    return x\n"
            + "class W:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.a = x\n        z = helper(y)\n        self.b = z\n",
            "W")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["a"]
        assert _ctor_tail(ctor) == (
            " : a(x) {\n        int32_t z = helper(y);\n"
            "        this->b = z;\n    }\n")

    def test_demotion_byte_identical(self):
        # End-to-end byte-identity for the demotion shapes (body-local demote +
        # chain-break demote of a hoistable init) through the THIR seam vs the AST.
        src = (
            _PRELUDE
            + "def helper(x: Int32) -> Int32:\n    return x\n"
            + "class W:\n    a: Int32\n    b: Int32\n    c: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.a = x\n        t = helper(y)\n"
            + "        self.b = t\n        self.c = x\n"
            + "def main():\n    w = W(1, 2)\n    print(w.a + w.b + w.c)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_demotion_cascade_byte_identical(self):
        # A demoted init breaks the chain, so a subsequent otherwise-hoistable init
        # also demotes (cascade). All three end up in the body in source order.
        src = (
            _PRELUDE
            + "class W:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        n = x + 1\n        self.a = n\n        self.b = x\n"
            + "def main():\n    w = W(5)\n    print(w.a + w.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_record_field_demotion_is_ineligible(self):
        # A demoted *record*-field write is not a body-eligible statement
        # (`_stmt_eligible` admits only scalar / Optional field writes), so the ctor
        # stays on the AST path. Byte-safe; bounds the M3c-demotion slice.
        ctor = _lower_ctor(
            self._INNER
            + "class W:\n    rec: Inner\n"
            + "    def __init__(self, v: Int32):\n"
            + "        m = Inner(v)\n        self.rec = m\n",
            "W")
        assert ctor is None

    def test_single_base_super_init_routes(self):
        # M3d-1: a single-F1-base ctor routes -- `super().__init__(a)` lowers to a
        # `Base(a)` base initializer prepended to the MIL; own fields hoist as usual.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.b = b\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["Base"]
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["b"]
        assert _ctor_tail(ctor) == " : Base(a), b(b) {}\n"
        assert _lower_ctor(src, "Base") is not None  # the flat base routes too

    def test_inherited_field_write_routes(self):
        # M3d: a direct inherited-field write (`self.a = ...`, `a` owned by the base)
        # goes to the BODY (the base ctor owns the MIL slot), without breaking the hoist
        # chain -- so the own field `b` still hoists. `this->a = a;` lands in the body.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = b\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["Base"]
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["b"]  # own field hoists
        assert _ctor_tail(ctor) == " : Base(a), b(b) {\n        this->a = a;\n    }\n"

    def test_init_reading_inherited_field_demotes(self):
        # An own-field init reading an inherited field written earlier in the body must
        # demote (the MIL runs before that write) -- the expr_reads_self_field trigger.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = self.a\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert ctor.mil_inits == ()  # b demotes (reads self.a written in the body)
        assert _ctor_tail(ctor) == (
            " : Base(a) {\n        this->a = a;\n        this->b = this->a;\n    }\n")

    def test_multi_base_routes(self):
        # M3d: multiple bases route -- each explicit `BaseN.__init__(self, ...)` lowers
        # to a base initializer, sorted by parent declaration order (A before B).
        src = (
            _PRELUDE
            + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
            + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
            + "class C(A, B):\n    z: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32, z: Int32):\n"
            + "        A.__init__(self, x)\n        B.__init__(self, y)\n        self.z = z\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["A", "B"]
        assert _ctor_tail(ctor) == " : A(x), B(y), z(z) {}\n"

    def test_multi_base_byte_identical(self):
        src = (
            _PRELUDE
            + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
            + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
            + "class C(A, B):\n    z: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32, z: Int32):\n"
            + "        A.__init__(self, x)\n        B.__init__(self, y)\n        self.z = z\n"
            + "def main():\n    c = C(1, 2, 3)\n    print(c.x + c.y + c.z)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_single_base_byte_identical(self):
        # End-to-end byte-identity for the single-base super-init ctor through the
        # THIR seam vs the AST path (the derived signature is AST-emitted; only the
        # base-init + field MIL tail routes).
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.b = b\n"
            + "def main():\n    d = Derived(1, 2)\n    print(d.a + d.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_concrete_arg_generic_base_routes(self):
        # F5 stage A: a generic base with CONCRETE args (`Box[Int32]`) is now an
        # F1 record (its `to_cpp()` recursion spells `Box<int32_t>`, matching the
        # resolver), so a derived ctor over it routes byte-identically -- no corpus
        # case covers a generic base, so this guards the `_f1_record(parent)` gate.
        src = (
            _PRELUDE
            + "class Box[T]:\n    v: T\n    def __init__(self, v: T):\n        self.v = v\n"
            + "class IntBox(Box[Int32]):\n    n: Int32\n"
            + "    def __init__(self, v: Int32, n: Int32):\n"
            + "        super().__init__(v)\n        self.n = n\n"
            + "def main():\n    b = IntBox(3, 4)\n    print(b.n)\nmain()\n")
        assert _lower_ctor(src, "IntBox") is not None
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_nonslice_arg_generic_base_is_ineligible(self):
        # A generic base whose arg is OUTSIDE the byte-identical slice (a tuple --
        # element qualification diverges) stays non-F1, keeping the derived ctor
        # on the AST path. Guards the recursive `_f1_record_type_arg_ok` reject.
        src = (
            _PRELUDE
            + "class Box[T]:\n    v: Int32\n    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class TupBox(Box[tuple[Int32, Int32]]):\n    n: Int32\n"
            + "    def __init__(self, v: Int32, n: Int32):\n"
            + "        super().__init__(v)\n        self.n = n\n")
        assert _lower_ctor(src, "TupBox") is None

    def test_field_read_optional_byte_identical(self):
        # A param field-read into an Optional[record] field (`self.opt = b.inner`)
        # constructs the optional directly -- the TpyFieldAccess arm of
        # `_is_record_value_source` flowing into the Optional `else` (no ptr_to_optional).
        src = (
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, b: Box):\n        self.opt = b.inner\n"
            + "def main():\n    h = H(Box(Inner(5)))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_optional_own_param_sibling_byte_identical(self):
        # The `Optional[Own[Inner]]` own-optional peel shape emits identically end-to-end
        # (the sibling `Own[Inner | None]` is `test_own_optional_param_byte_identical`).
        src = (
            "from typing import Optional\n" + self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Optional[Own[Inner]]):\n        self.opt = m\n"
            + "def main():\n    h = H(Inner(4))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_inherited_field_write_byte_identical(self):
        # End-to-end byte-identity for the M3d inherited-field-write body branch and
        # the demote-reads-inherited-field path through the THIR seam vs the AST.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = self.a\n"
            + "def main():\n    d = Derived(7)\n    print(d.a + d.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_non_scalar_field_is_ineligible(self):
        # A str field is M3b+ form work, not M3a scalar.
        ctor = _lower_ctor(
            _PRELUDE
            + "class S:\n    name: str\n"
            + "    def __init__(self, name: str):\n        self.name = name\n",
            "S")
        assert ctor is None

    def test_bigint_field_routes(self):
        # A BigInt field rides the scalar MIL arm (`: n(n)`) -- BigInt is an
        # eligible value scalar since the BigInt value-binding cell.
        ctor = _lower_ctor(
            "class C:\n    n: int\n"
            + "    def __init__(self, n: int):\n        self.n = n\n",
            "C")
        assert ctor is not None

    def test_ineligible_param_with_scalar_fields_is_ineligible(self):
        # The PARAM gate must reject a ctor whose fields are all scalar but a param
        # is non-eligible: it would otherwise emit `: n(n) {}` byte-identically, so the
        # corpus byte-diff cannot guard a regression here -- only this unit test can.
        # (An `Optional` param stays on the AST path; `list[scalar]` is now admitted.)
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    n: Int32\n"
            + "    def __init__(self, n: Int32, x: Int32 | None):\n"
            + "        self.n = n\n",
            "C")
        assert ctor is None

    def _hpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, _ = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp

    def test_ctor_byte_identical(self):
        # End-to-end: the ctor MIL tail emits identically through the THIR seam
        # (generator -> records -> emit) and the AST path. The ctor lives in the
        # .hpp (inline in the struct), so compare that half.
        src = (
            _PRELUDE
            + "class Point:\n    x: Int32\n    y: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.x = x\n        self.y = y\n"
            + "def main():\n    p = Point(1, 2)\n    print(p.x + p.y)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    # --- M3b: record / Optional[record] member-init-list fields ---

    _INNER = (
        "from tpy import Int32\n"
        "class Inner:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n")

    def test_optional_field_from_optional_param_routes(self):
        # The M3 cell: an Optional[record] field <- Optional[record] borrow param
        # lifts via ptr_to_optional (the F2b conversion, now in MIL position).
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Inner | None):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(::tpy::ptr_to_optional(m)) {}\n"

    def test_optional_field_none_routes(self):
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self):\n        self.opt = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::nullopt) {}\n"

    def test_record_field_copy_routes(self):
        # A plain record field <- non-own record param: an implicit MIL copy.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Inner):\n        self.rec = p\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(p) {}\n"

    def test_record_field_explicit_copy_unwraps(self):
        # `copy(p)` is the explicit field-copy acknowledgment; it unwraps to the same
        # `rec(p)` direct-init as the bare `self.rec = p` (the MIL copies implicitly).
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Inner):\n        self.rec = copy(p)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(p) {}\n"

    _OWN_INNER = (
        "from tpy import Int32, Own\n"
        "class Inner:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n")

    def test_own_record_param_record_field_moves(self):
        # M3b-move: an Own[record] param at last use moves into a record field
        # (the common ownership-taking ctor) -- `rec(std::move(p))`.
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Own[Inner]):\n        self.rec = p\n",
            "H")
        assert ctor is not None
        assert ctor.mil_inits[0].move
        assert _ctor_tail(ctor) == " : rec(std::move(p)) {}\n"

    def test_own_record_param_optional_field_moves(self):
        # M3b-move: an Own[record] param moves into an Optional[record] field --
        # `opt(std::move(p))`, NOT ptr_to_optional (an own source skips that arm).
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, p: Own[Inner]):\n        self.opt = p\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::move(p)) {}\n"

    def test_own_optional_param_moves(self):
        # M3b-rvalue: an own-optional param (`Own[Inner | None]`) moves into an
        # Optional[record] field via the move arm -- `opt(std::move(m))`, NOT
        # ptr_to_optional (the own source skips that arm, as for a plain Own param).
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Own[Inner | None]):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert ctor.mil_inits[0].move
        assert _ctor_tail(ctor) == " : opt(std::move(m)) {}\n"

    def test_optional_own_param_moves(self):
        # The sibling own-optional shape `Optional[Own[Inner]]` peels differently but
        # emits the same move MIL.
        ctor = _lower_ctor(
            "from typing import Optional\n" + self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Optional[Own[Inner]]):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::move(m)) {}\n"

    def test_ctor_call_record_source_routes(self):
        # M3b-rvalue: an rvalue ctor-call source (`self.rec = Inner(v)`) constructs the
        # record field directly from the prvalue -- `rec(Inner(v))`.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, v: Int32):\n        self.rec = Inner(v)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(Inner(v)) {}\n"

    def test_ctor_call_optional_source_routes(self):
        # M3b-rvalue: an rvalue ctor-call into an Optional[record] field constructs the
        # optional directly from the prvalue -- `opt(Inner(v))`, NOT ptr_to_optional.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, v: Int32):\n        self.opt = Inner(v)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(Inner(v)) {}\n"

    def test_record_field_from_param_field_read_routes(self):
        # M3b-rvalue: a field-read off a param record (`self.rec = b.inner`) copies the
        # field into the record member -- `rec(b.inner)`.
        ctor = _lower_ctor(
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, b: Box):\n        self.rec = b.inner\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(b.inner) {}\n"

    def test_self_field_record_read_source_is_ineligible(self):
        # A `self.<record field>` read source is ordering-sensitive in the MIL (the
        # pointee may be uninitialized) -- excluded; the ctor falls to the AST path.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    a: Inner\n    b: Inner\n"
            + "    def __init__(self, p: Inner):\n"
            + "        self.a = p\n        self.b = self.a\n",
            "H")
        assert ctor is None

    def test_optional_field_byte_identical(self):
        # End-to-end byte-identity for the ptr_to_optional + None MIL cases, mixed
        # with a scalar field, through the THIR seam vs the AST path.
        src = (
            self._INNER
            + "class H:\n    n: Int32\n    opt: Inner | None\n"
            + "    def __init__(self, n: Int32, m: Inner | None):\n"
            + "        self.n = n\n        self.opt = m\n"
            + "def main():\n    h = H(5, None)\n    print(h.n)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_own_param_move_byte_identical(self):
        # The move arm's load-bearing contract: the own-param std::move MIL (into
        # both a record field and an Optional field) emits identically through THIR
        # and the AST path.
        src = (
            "from tpy import Int32, Own\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    inner: Inner\n    opt: Inner | None\n"
            + "    def __init__(self, a: Own[Inner], b: Own[Inner]):\n"
            + "        self.inner = a\n        self.opt = b\n"
            + "def main():\n    h = H(Inner(1), Inner(2))\n    print(h.inner.v)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_rvalue_source_byte_identical(self):
        # End-to-end byte-identity for the M3b-rvalue shapes: a ctor-call source into
        # a record field and into an Optional field, and a param field-read into a
        # record field, all through the THIR seam vs the AST path.
        src = (
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    rec: Inner\n    opt: Inner | None\n    cp: Inner\n"
            + "    def __init__(self, v: Int32, b: Box):\n"
            + "        self.rec = Inner(v)\n        self.opt = Inner(v)\n"
            + "        self.cp = b.inner\n"
            + "def main():\n    h = H(7, Box(Inner(3)))\n    print(h.rec.v)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_own_optional_param_byte_identical(self):
        # An own-optional param (`Own[Inner | None]`) moving into an Optional field
        # emits identically through THIR and the AST path.
        src = (
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Own[Inner | None]):\n        self.opt = m\n"
            + "def main():\n    h = H(Inner(4))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_trivia_body_byte_identical(self):
        # M3c-trivia: docstring + pass non-init bodies emit the ` {\n    }` braces
        # identically through THIR and the AST path (the byte-diff with source
        # comments ON further validates the pass/docstring comment asymmetry).
        src = (
            _PRELUDE
            + "class P:\n    x: Int32\n    y: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + '        """A point."""\n        self.x = x\n        self.y = y\n        pass\n'
            + "def main():\n    p = P(1, 2)\n    print(p.x + p.y)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_optional_copy_source_routes(self):
        # M3b-rvalue: `copy()` is unwrapped before the Optional check (matching the
        # record arm, `test_record_field_explicit_copy_unwraps`), so a copy()-wrapped
        # pointer-repr Optional borrow source lifts via ptr_to_optional just like the
        # bare `self.opt = m` -- `opt(::tpy::ptr_to_optional(m))`.
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Inner | None):\n        self.opt = copy(m)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(::tpy::ptr_to_optional(m)) {}\n"
