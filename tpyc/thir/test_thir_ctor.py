"""THIR constructor frontier M3: ctor MIL field cells + bodies + inheritance."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile, _entry, _fn, _lower_ctor, _lower_ctx, _lower_ctx_witnessed,
    _ctor_tail, _PRELUDE, _assert_routes_byte_identical,
    _assert_byte_identical,
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
        # chain intact. It emits no code and no source line (loc=None), but its
        # leading `#` comments still ride trivia_loc.
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
        # The byte-identity hinge, and it splits the two comment kinds: the AST's
        # gen_stmt flushes a statement's leading `#` comments BEFORE dispatch, then
        # the None simple-stmt code suppresses only the statement's own source line.
        # So `pass` keeps `loc` (both kinds), while a docstring drops `loc` (no
        # `// """doc"""` line) but keeps `trivia_loc` (the comments still emit).
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
        assert doc_ctor.body[0].trivia_loc is not None

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

    def test_record_field_demotion_routes_with_move(self):
        # A demoted record-field init from a body local routes since the
        # ctor-body cell: `self.rec = m` at m's last use emits the AST demote's
        # `this->rec = std::move(m);` (the field_write.record_name move row).
        ctor = _lower_ctor(
            self._INNER
            + "class W:\n    rec: Inner\n"
            + "    def __init__(self, v: Int32):\n"
            + "        m = Inner(v)\n        self.rec = m\n",
            "W")
        assert ctor is not None
        assert ctor.mil_inits == ()  # bare-name RHS demotes, like the AST
        assert _ctor_tail(ctor) == (
            " {\n        Inner m = Inner(v);\n"
            "        this->rec = std::move(m);\n    }\n")

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
        # A generic base whose arg is OUTSIDE the byte-identical slice (a
        # plain-alias union -- the alias registers only mid-emission, after
        # lowering) stays non-F1, keeping the derived ctor on the AST path.
        # Guards the recursive `_f1_record_type_arg_ok` reject.
        src = (
            _PRELUDE
            + "from tpy import StrView\n"
            + "type Num = Int32 | StrView\n"
            + "class Box[T]:\n    v: Int32\n    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class NumBox(Box[Num]):\n    n: Int32\n"
            + "    def __init__(self, v: Int32, n: Int32):\n"
            + "        super().__init__(v)\n        self.n = n\n")
        assert _lower_ctor(src, "NumBox") is None

    # --- base-init args beyond scalars (target-less bare renders) ---

    _STR_BASE = (
        _PRELUDE
        + "class Base:\n    name: str\n"
        + "    def __init__(self, name: str):\n        self.name = name\n")

    def test_base_init_str_param_arg_routes(self):
        # The dominant corpus shape (exception hierarchies): a str param
        # forwarded to super().__init__ renders as the bare name.
        ctor = _lower_ctor(
            self._STR_BASE
            + "class E(Base):\n    n: Int32\n"
            + "    def __init__(self, name: str, n: Int32):\n"
            + "        super().__init__(name)\n        self.n = n\n",
            "E")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : Base(name), n(n) {}\n"

    def test_base_init_str_literal_arg_routes(self):
        ctor = _lower_ctor(
            self._STR_BASE
            + "class F(Base):\n"
            + "    def __init__(self):\n        super().__init__(\"lit\")\n",
            "F")
        assert ctor is not None
        assert _ctor_tail(ctor) == ' : Base("lit") {}\n'

    def test_base_init_bigint_slot_int_literal_renders_bare(self):
        # An int literal at a BigInt base slot stays IntLiteralType, so the
        # target-less render is the bare digits (`Base(5)`, no BigInt wrap).
        ctor = _lower_ctor(
            "from tpy import Int32\n"
            + "class Base:\n    n: int\n"
            + "    def __init__(self, n: int):\n        self.n = n\n"
            + "class G(Base):\n"
            + "    def __init__(self):\n        super().__init__(5)\n",
            "G")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : Base(5) {}\n"

    _REC_BASE = (
        "from tpy import Int32, Own\n"
        + "class Payload:\n    v: Int32\n"
        + "    def __init__(self, v: Int32):\n        self.v = v\n"
        + "class Base:\n    p: Payload\n    opt: Payload | None\n"
        + "    def __init__(self, p: Payload, opt: Payload | None):\n"
        + "        self.p = p\n        self.opt = opt\n")

    def test_base_init_record_and_none_args_route(self):
        # A record param renders bare; a None renders `nullptr` (the base's
        # pointer-repr Optional param slot) -- both target-less gen_expr rows.
        ctor = _lower_ctor(
            self._REC_BASE
            + "class C(Base):\n"
            + "    def __init__(self, p: Payload):\n"
            + "        super().__init__(p, None)\n",
            "C")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : Base(p, nullptr) {}\n"

    def test_base_init_optional_param_arg_routes(self):
        ctor = _lower_ctor(
            self._REC_BASE
            + "class D(Base):\n"
            + "    def __init__(self, p: Payload, o: Payload | None):\n"
            + "        super().__init__(p, o)\n",
            "D")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : Base(p, o) {}\n"

    def test_base_init_own_param_arg_copies_mirrors_ast(self):
        # PRE-EXISTING AST QUIRK, mirrored: an Own[T] param forwarded to a
        # base init renders BARE (`Base(q, ...)` -- a COPY into the base's
        # const-ref slot, no std::move), because _extract_base_inits renders
        # target-less gen_expr with no _maybe_move. Pinned so a future AST
        # fix flags the THIR lockstep update.
        ctor = _lower_ctor(
            self._REC_BASE
            + "class M(Base):\n"
            + "    def __init__(self, q: Own[Payload]):\n"
            + "        super().__init__(q, None)\n",
            "M")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : Base(q, nullptr) {}\n"

    def test_base_init_concat_arg_is_ineligible(self):
        # A concat arg is a String rvalue -- not a bare-render row; the whole
        # ctor stays on the AST path.
        ctor = _lower_ctor(
            self._STR_BASE
            + "class H(Base):\n"
            + "    def __init__(self, s: str):\n"
            + "        super().__init__(s + \"!\")\n",
            "H")
        assert ctor is None

    def test_base_init_huge_int_literal_is_ineligible(self):
        # Outside the +-2^31-1 literal range the bare-digits render is not
        # pinned -- stays on the AST path.
        ctor = _lower_ctor(
            "class Base:\n    n: int\n"
            + "    def __init__(self, n: int):\n        self.n = n\n"
            + "class G(Base):\n"
            + "    def __init__(self):\n        super().__init__(4000000000)\n",
            "G")
        assert ctor is None

    def test_base_init_str_arg_byte_identical(self):
        src = (
            self._STR_BASE
            + "class E(Base):\n    n: Int32\n"
            + "    def __init__(self, name: str, n: Int32):\n"
            + "        super().__init__(name)\n        self.n = n\n"
            + "def main():\n    e = E(\"x\", 2)\n    print(e.name, e.n)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_base_init_record_args_byte_identical(self):
        src = (
            self._REC_BASE
            + "class C(Base):\n"
            + "    def __init__(self, p: Payload):\n"
            + "        super().__init__(p, None)\n"
            + "class M(Base):\n"
            + "    def __init__(self, q: Own[Payload]):\n"
            + "        super().__init__(q, None)\n"
            + "def main():\n    c = C(Payload(1))\n    m = M(Payload(2))\n"
            + "    print(c.p.v + m.p.v)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    # --- ctor demoted field writes (container / str / nondef record) ---

    def test_ctor_demoted_container_and_str_writes_route(self):
        # After a chain break the demoted container-literal / str field inits
        # are ordinary body statements (the Semaphore shape).
        ctor = _lower_ctor(
            _PRELUDE
            + "class S:\n    xs: list[Int32]\n    tag: str\n"
            + "    def __init__(self, v: Int32, s: str):\n"
            + "        if v < 0:\n            pass\n"
            + "        self.xs = [v]\n        self.tag = s\n",
            "S")
        assert ctor is not None
        assert ctor.mil_inits == ()

    def test_ctor_reassigned_str_param_is_ineligible(self):
        # A reassigned str param needs the AST's owned-copy prologue
        # (`std::string s = std::string(__param_s);` -- itself emitted against
        # an un-renamed ctor signature, a pre-existing AST bug), a shape the
        # slice does not reproduce -> whole ctor stays on the AST path.
        ctor = _lower_ctor(
            _PRELUDE
            + "class S:\n    tag: str\n"
            + "    def __init__(self, s: str, v: Int32):\n"
            + "        if v > 0:\n            s = \"pos\"\n"
            + "        self.tag = s\n",
            "S")
        assert ctor is None

    def test_ctor_demoted_nondef_record_field_is_ineligible(self):
        # A demoted init of a field whose record type suppresses its default
        # ctor (@nocopy + __del__) makes the AST raise a CodeGenError -- the
        # gate must keep the whole ctor on the AST path so the diagnostic
        # still fires.
        ctor = _lower_ctor(
            "from tpy import Int32, nocopy\n"
            + "@nocopy\n"
            + "class R:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "    def __del__(self):\n        pass\n"
            + "class W:\n    rec: R\n"
            + "    def __init__(self, v: Int32):\n"
            + "        m = R(v)\n        self.rec = m\n",
            "W")
        assert ctor is None

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

    def test_span_field_copies_the_view_bare(self):
        # A Span field from a SAME-TYPED span param is the bare view copy
        # (`: xs(xs)`) -- borrow and storage coincide for a view. The lifetime
        # question this row used to be held back for (the field aliasing
        # whatever the caller's span pointed at) is identical on both paths
        # and belongs to the borrow checker, not to codegen; the render is
        # byte-identical, pinned in test_thir_wave_mil_views.py along with the
        # non-exact-type source that still defers.
        ctor = _lower_ctor(
            "from tpy import Int32, Span\n"
            + "class S:\n    xs: Span[Int32]\n"
            + "    def __init__(self, xs: Span[Int32]):\n        self.xs = xs\n",
            "S")
        assert ctor is not None

    def test_bigint_field_routes(self):
        # A BigInt field rides the scalar MIL arm (`: n(n)`) -- BigInt is an
        # eligible value scalar since the BigInt value-binding cell.
        ctor = _lower_ctor(
            "class C:\n    n: int\n"
            + "    def __init__(self, n: int):\n        self.n = n\n",
            "C")
        assert ctor is not None

    def test_unused_callable_param_routes(self):
        # Constructor signatures stay AST-emitted, so an unused Callable param
        # does not constrain lowering of the scalar member initializer.
        src = (
            "from typing import Callable\n" + _PRELUDE
            + "class C:\n    n: Int32\n"
            + "    def __init__(self, n: Int32, f: Callable[[Int32], Int32]):\n"
            + "        self.n = n\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : n(n) {}\n"
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

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


def _lower_ctor_witnessed(source: str, record_name: str):
    """`_lower_ctor` plus the face-witness counts the lowering recorded."""
    from ..compilation_context import activate_compiler
    from .lower import lower_constructor
    from .lower.functions import iter_module_constructors
    compiler, modules = _compile(source)
    entry = _entry(modules)
    with activate_compiler(compiler):
        for rec, init, self_type in iter_module_constructors(entry.ast,
                                                             entry.analyzer):
            if rec.name == record_name:
                ctor = lower_constructor(rec, init, entry.analyzer,
                                         self_type=self_type)
                return ctor, compiler._thir_face_witnesses
    return None, compiler._thir_face_witnesses


class TestCtorViewFamilyFields:
    """Ctor MIL str / StrView / bytes field-init cells: the bare str-family
    renders (std::string's explicit string_view ctor fires in the direct-init),
    the bytes view->owned `::tpy::bytes_copy` copy, owned literal / `bytes()`
    rvalues -- plus the stays-AST bounds (native `bytes(x)` init, String param,
    `copy()` wrap, a param reassigned later in the body)."""

    def _hpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, _ = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp

    def test_str_field_from_str_param_routes_bare(self):
        ctor, w = _lower_ctor_witnessed(
            "class N:\n    name: str\n"
            "    def __init__(self, name: str):\n        self.name = name\n",
            "N")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : name(name) {}\n"
        assert w.get("mil.str_field", 0) == 1

    def test_str_field_from_strview_param_materializes(self):
        # The sema strview_to_str ASSIGN coerce materializes itself:
        # `std::string(v)` -- the only wrapped str-family MIL render.
        ctor = _lower_ctor(
            "from tpy import StrView\n"
            "class N:\n    name: str\n"
            "    def __init__(self, v: StrView):\n        self.name = v\n",
            "N")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : name(std::string(v)) {}\n"

    def test_str_field_from_literal_routes(self):
        ctor = _lower_ctor(
            "class N:\n    name: str\n"
            '    def __init__(self):\n        self.name = "hello"\n',
            "N")
        assert ctor is not None
        assert _ctor_tail(ctor) == ' : name("hello") {}\n'

    def test_strview_field_from_param_routes_bare(self):
        ctor = _lower_ctor(
            "from tpy import StrView\n"
            "class V:\n    view: StrView\n"
            "    def __init__(self, v: StrView):\n        self.view = v\n",
            "V")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : view(v) {}\n"

    def test_strview_field_from_literal_routes_bare(self):
        # The literal arrives under the identity str_to_strview coerce.
        ctor = _lower_ctor(
            "from tpy import StrView\n"
            "class V:\n    view: StrView\n"
            '    def __init__(self):\n        self.view = "hi"\n',
            "V")
        assert ctor is not None
        assert _ctor_tail(ctor) == ' : view("hi") {}\n'

    def test_bytes_field_from_param_copies(self):
        # The span param lifts through the S6 STORAGE convert -- vector has
        # no span ctor (the AST's `_view_source_to_owned` chokepoint).
        ctor, w = _lower_ctor_witnessed(
            "class P:\n    data: bytes\n"
            "    def __init__(self, data: bytes):\n        self.data = data\n",
            "P")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : data(::tpy::bytes_copy(data)) {}\n"
        assert w.get("mil.bytes_field", 0) == 1

    def test_bytes_field_from_bytesview_param_copies(self):
        # The bytesview_to_bytes coerce's lambda IS the same copy render.
        ctor = _lower_ctor(
            "from tpy import BytesView\n"
            "class P:\n    data: bytes\n"
            "    def __init__(self, v: BytesView):\n        self.data = v\n",
            "P")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : data(::tpy::bytes_copy(v)) {}\n"

    def test_bytes_field_from_literal_owned_render(self):
        ctor = _lower_ctor(
            "class P:\n    data: bytes\n"
            '    def __init__(self):\n        self.data = b"ab"\n',
            "P")
        assert ctor is not None
        assert _ctor_tail(ctor) == ' : data(::tpy::bytes_literal_owned("ab", 2)) {}\n'

    def test_bytes_field_from_empty_literal(self):
        ctor = _lower_ctor(
            "class P:\n    data: bytes\n"
            '    def __init__(self):\n        self.data = b""\n',
            "P")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : data(std::vector<uint8_t>{}) {}\n"

    def test_bytes_field_from_empty_ctor_call(self):
        # `bytes()` is the zero-arg @cpp_template __init__ expansion -- an
        # owned rvalue landing bare (note parens, not the literal's braces).
        ctor = _lower_ctor(
            "class P:\n    data: bytes\n"
            "    def __init__(self):\n        self.data = bytes()\n",
            "P")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : data(std::vector<uint8_t>()) {}\n"

    def test_bytes_native_ctor_call_stays_ast(self):
        # `bytes(x)` resolves to the @native(function=True) __init__ overload
        # (`tpy::bytes_copy`) -- an emit the call slice does not spell.
        ctor = _lower_ctor(
            "class P:\n    data: bytes\n"
            "    def __init__(self, src: bytes):\n        self.data = bytes(src)\n",
            "P")
        assert ctor is None

    def test_string_param_routes(self):
        # A `String` param spells `const std::string&` in the SKELETON, which
        # no ctor body arm renders: the field init is the same bare member
        # init a `str` param takes, so the ctor routes.
        ctor = _lower_ctor(
            "from tpy import String\n"
            "class N:\n    name: str\n"
            "    def __init__(self, name: String):\n        self.name = name\n",
            "N")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : name(name) {}\n"

    def test_param_reassigned_in_body_stays_ast(self):
        # The AST DEMOTES an init whose RHS references a top-level body
        # binding -- including a param reassigned later (blocked_by_body_local).
        # THIR must not hoist it; the conservative verdict is the whole-ctor
        # reject (the AST path also emits the demoted shape).
        ctor = _lower_ctor(
            "class N:\n    name: str\n"
            "    def __init__(self, name: str):\n"
            "        self.name = name\n"
            '        name = "other"\n        print(name)\n',
            "N")
        assert ctor is None

    def test_copy_wrapped_str_source_stays_ast(self):
        # copy() at a view-family field is an unprobed wrap contract -> AST.
        ctor = _lower_ctor(
            "from tpy import copy\n"
            "class N:\n    name: str\n"
            "    def __init__(self, s: str):\n        self.name = copy(s)\n",
            "N")
        assert ctor is None

    def test_fstring_str_source_stays_ast(self):
        ctor = _lower_ctor(
            "class N:\n    name: str\n"
            "    def __init__(self, s: str):\n        self.name = f\"[{s}]\"\n",
            "N")
        assert ctor is None

    def test_viewfam_fields_byte_identical(self):
        # End-to-end byte-identity for every routed (field family x source)
        # cell through the THIR seam vs the AST path.
        src = (
            "from tpy import StrView, BytesView\n"
            "class N:\n    name: str\n    tag: str\n    view: StrView\n"
            "    def __init__(self, name: str, v: StrView):\n"
            "        self.name = name\n        self.tag = \"t\"\n"
            "        self.view = v\n"
            "class P:\n    data: bytes\n    lit: bytes\n    empty: bytes\n"
            "    def __init__(self, data: bytes):\n"
            "        self.data = data\n        self.lit = b\"ab\"\n"
            "        self.empty = bytes()\n"
            "def main():\n"
            "    n = N(\"a\", \"b\")\n    print(n.name, n.tag, n.view)\n"
            "    p = P(b\"xy\")\n    print(len(p.data), len(p.lit), len(p.empty))\n"
            "main()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)


class TestConstructorContainerFields:
    """MIL inits of builtin-container fields (list / dict / set / Array):
    container-literal sources through the shared container-literal machinery
    (the MIL is target-threaded like a decl init), and bare container-param
    copies / Own moves."""

    def _hpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, _ = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp

    def test_list_literal_mil_routes(self):
        ctor, wit = _lower_ctor_witnessed(
            _PRELUDE
            + "class A:\n    items: list[Int32]\n"
            + "    def __init__(self):\n        self.items = [1, 2]\n",
            "A")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : items({1, 2}) {}\n"
        assert wit.get("mil.container_literal", 0) >= 1

    def test_empty_literals_spell_their_type(self):
        # An empty list spells its vector type (the T*-assignment-ambiguity
        # guard); an empty dict spells the runtime map ctor.
        ctor = _lower_ctor(
            _PRELUDE
            + "class A:\n    items: list[Int32]\n    m: dict[str, Int32]\n"
            + "    def __init__(self):\n"
            + "        self.items = []\n        self.m = {}\n",
            "A")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : items(std::vector<int32_t>{}), "
            "m(::tpy::ordered_map<std::string, int32_t>()) {}\n")

    def test_dict_set_array_literal_tails(self):
        ctor = _lower_ctor(
            "from tpy import Array, Int32\n"
            + "class A:\n    d: dict[Int32, Int32]\n    s: set[Int32]\n"
            + "    arr: Array[Int32, 3]\n"
            + "    def __init__(self):\n"
            + "        self.d = {1: 2, 3: 4}\n        self.s = {1, 2}\n"
            + "        self.arr = [1, 2, 3]\n",
            "A")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : d(::tpy::ordered_map<int32_t, int32_t>({{1, 2}, {3, 4}})), "
            "s(::tpy::ordered_set<int32_t>({1, 2})), arr({1, 2, 3}) {}\n")

    def test_str_view_elem_copies(self):
        # A string_view param element into an owned std::string slot takes the
        # S5 view->owned copy inside the MIL brace-init; the literal lands bare.
        ctor = _lower_ctor(
            "class A:\n    names: list[str]\n    m: dict[str, str]\n"
            "    def __init__(self, prefix: str):\n"
            "        self.names = [prefix, \"lit\"]\n"
            "        self.m = {\"k\": prefix}\n",
            "A")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : names({std::string(prefix), \"lit\"}), "
            "m(::tpy::ordered_map<std::string, std::string>"
            "({{\"k\", std::string(prefix)}})) {}\n")

    def test_own_record_elem_moves_via_make_vector(self):
        # An Own record param element moves at its last use; a moved element in
        # a const std::initializer_list would silently copy, so the lowering
        # flips to the reserve+emplace make_vector helper (the AST's switch).
        ctor = _lower_ctor(
            "from tpy import Int32, Own\n"
            "class P:\n    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "class A:\n    ps: list[P]\n"
            "    def __init__(self, q: Own[P]):\n        self.ps = [q]\n",
            "A")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : ps(::tpy::make_vector<P>(std::move(q))) {}\n")

    def test_record_elems_and_nested_list_route(self):
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class A:\n    ps: list[P]\n    grid: list[list[Int32]]\n"
            + "    def __init__(self, p: P):\n"
            + "        self.ps = [P(1), p]\n        self.grid = [[1], [2, 3]]\n",
            "A")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : ps({P(1), p}), grid({{1}, {2, 3}}) {}\n")

    def test_container_param_copy_routes(self):
        # A container param name copies bare into the field slot.
        ctor, wit = _lower_ctor_witnessed(
            _PRELUDE
            + "class D:\n    items: list[Int32]\n    d: dict[Int32, Int32]\n"
            + "    def __init__(self, items: list[Int32], d: dict[Int32, Int32]):\n"
            + "        self.items = items\n        self.d = d\n",
            "D")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : items(items), d(d) {}\n"
        assert wit.get("mil.container_name", 0) >= 1

    def test_own_container_param_mil_move_routes(self):
        # The composed shape the container cell + the ctor-params cell each
        # pinned as stays-AST on their own trees: the container name row in the
        # MIL lowering renders `items(std::move(items))`.
        src = (
            "from tpy import Int32, Own\n"
            "class E:\n    items: list[Int32]\n"
            "    def __init__(self, items: Own[list[Int32]]):\n"
            "        self.items = items\n"
            "def main():\n    e = E([4])\n    print(len(e.items))\nmain()\n")
        ctor = _lower_ctor(src, "E")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : items(std::move(items)) {}\n"
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_nested_empty_list_elem_stays_ast(self):
        # An un-threaded nested EMPTY list renders bare `{}` on the AST (no
        # elem target below a list slot); THIR's spelled empty emit would
        # diverge, so the gate rejects and the whole ctor stays AST.
        src = (
            _PRELUDE
            + "class A:\n    grid: list[list[Int32]]\n"
            + "    def __init__(self):\n        self.grid = [[], [1]]\n"
            + "def main():\n    a = A()\n    print(len(a.grid))\nmain()\n")
        ctor = _lower_ctor(src, "A")
        assert ctor is None
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_list_repeat_stays_ast(self):
        # `[0] * n` is a TpyListRepeat, not a container literal -- outside the
        # slice on both the decl-init and MIL faces.
        ctor = _lower_ctor(
            _PRELUDE
            + "class A:\n    items: list[Int32]\n"
            + "    def __init__(self):\n        self.items = [0] * 3\n",
            "A")
        assert ctor is None

    def test_float_key_dict_stays_ast(self):
        # Dict keys keep the receiver-slice rule (fixed-int / BigInt / owned
        # str); a float key is outside it, so the ctor stays AST.
        ctor = _lower_ctor(
            _PRELUDE
            + "class A:\n    m: dict[float, Int32]\n"
            + "    def __init__(self):\n        self.m = {1.5: 2}\n",
            "A")
        assert ctor is None

    def test_body_local_elem_byte_identical(self):
        # An element referencing a body local: the AST demotes the init to the
        # body (`this->items = {tmp};`); THIR conservatively keeps the whole
        # ctor on the AST path (the element name is not in `declared`), so the
        # two paths stay byte-identical either way.
        src = (
            _PRELUDE
            + "class C:\n    items: list[Int32]\n"
            + "    def __init__(self):\n"
            + "        tmp = 3\n        self.items = [tmp]\n"
            + "def main():\n    c = C()\n    print(len(c.items))\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_container_mil_byte_identical(self):
        # End-to-end byte-identity for the whole container-MIL family in one
        # record: literals (incl. empties, str views, records, nested lists,
        # Own move) and container-param copies.
        src = (
            "from tpy import Array, Int32, Own\n"
            "class P:\n    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "class A:\n"
            "    items: list[Int32]\n    names: list[str]\n"
            "    d: dict[str, Int32]\n    s: set[Int32]\n"
            "    arr: Array[Int32, 3]\n    ps: list[P]\n"
            "    grid: list[list[Int32]]\n    empty_l: list[Int32]\n"
            "    moved: list[P]\n    copied: list[Int32]\n"
            "    def __init__(self, prefix: str, p: P, q: Own[P],\n"
            "                 copied: list[Int32]):\n"
            "        self.items = [1, 2]\n"
            "        self.names = [prefix, \"lit\"]\n"
            "        self.d = {\"k\": 1}\n"
            "        self.s = {1, 2}\n"
            "        self.arr = [1, 2, 3]\n"
            "        self.ps = [P(1), p]\n"
            "        self.grid = [[1], [2, 3]]\n"
            "        self.empty_l = []\n"
            "        self.moved = [q]\n"
            "        self.copied = copied\n"
            "def main():\n"
            "    a = A(\"pre\", P(5), P(7), [9])\n"
            "    print(len(a.items), len(a.d), len(a.s))\n"
            "main()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)


class TestCtorMilSmallFamilies:
    """The small value families of MIL field cells: None into any Optional /
    Ptr / union field, value- and pointer-variant union sources, tuple
    sources, and the AST-demote mirror."""

    _AB = (
        _PRELUDE
        + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
        + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n")

    def _hpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, _ = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp

    def test_none_into_nonrecord_optional_routes(self):
        # `f(std::nullopt)` is inner-independent: a pointer-repr Optional of a
        # CONTAINER inner (outside the F1-record slice) still routes on a
        # None source.
        ctor = _lower_ctor(
            _PRELUDE
            + "class H:\n    items: list[Int32] | None\n"
            + "    def __init__(self):\n        self.items = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : items(std::nullopt) {}\n"

    def test_none_into_value_optional_routes(self):
        # A VALUE-repr Optional (`Optional[Int32]` -> std::optional<int32_t>)
        # renders the same `f(std::nullopt)`.
        ctor = _lower_ctor(
            _PRELUDE
            + "class H:\n    x: Int32 | None\n"
            + "    def __init__(self):\n        self.x = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : x(std::nullopt) {}\n"

    def test_value_optional_field_param_copy_routes(self):
        # A value-repr Optional field <- same-typed optional param: the bare
        # whole-optional copy (`value(value)`), no conversion helper.
        ctor = _lower_ctor(
            _PRELUDE
            + "class H:\n    value: Int32 | None\n"
            + "    def __init__(self, value: Int32 | None):\n"
            + "        self.value = value\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : value(value) {}\n"

    def test_value_optional_bigint_field_param_copy_stays_bare(self):
        # An expensive-copy (BigInt) inner still copies BARE in the MIL: the
        # AST's move-at-last-use machinery keys on the Own-param set there
        # (movable_locals is unpopulated in the MIL scope), so `std::move`
        # must NOT appear.
        ctor = _lower_ctor(
            "class H:\n    v: int | None\n"
            + "    def __init__(self, v: int | None):\n        self.v = v\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : v(v) {}\n"

    def test_value_optional_str_field_shim_routes(self):
        # Optional[str]: the param slot is optional<string_view> against the
        # field's optional<string>, so the MIL renders the arg-split shim
        # (`view_to_owned_conv`), byte-identical to the AST.
        src = ("class H:\n    s: str | None\n"
               + "    def __init__(self, s: str | None):\n        self.s = s\n")
        ctor, w = _lower_ctor_witnessed(src, "H")
        assert ctor is not None
        assert w.get("mil.optview_shim")
        assert _ctor_tail(ctor) == (
            " : s(s ? std::make_optional(std::string(*s)) : std::nullopt) {}\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_value_optional_field_inner_param_absorbs(self):
        # A bare `Int32` param into an `Int32 | None` field takes optional's
        # converting ctor, so the cell is the BARE `value(value)` -- the same
        # render as the same-typed optional param, no wrap on either path.
        src = (_PRELUDE
               + "class H:\n    value: Int32 | None\n"
               + "    def __init__(self, value: Int32):\n        self.value = value\n")
        ctor = _lower_ctor(src, "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : value(value) {}\n"
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_callable_field_param_copy_routes(self):
        # A std::function field <- same-typed callable param: bare copy.
        ctor = _lower_ctor(
            "from typing import Callable\nfrom tpy import Int32\n"
            + "class H:\n    on_event: Callable[[Int32], None]\n"
            + "    def __init__(self, cb: Callable[[Int32], None]) -> None:\n"
            + "        self.on_event = cb\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : on_event(cb) {}\n"

    def test_callable_field_lambda_source_stays_ast(self):
        # A lambda RHS needs lambda lowering in MIL position -- stays AST.
        ctor = _lower_ctor(
            "from typing import Callable\nfrom tpy import Int32\n"
            + "class H:\n    on_event: Callable[[Int32], None]\n"
            + "    def __init__(self) -> None:\n"
            + "        self.on_event = lambda x: None\n",
            "H")
        assert ctor is None

    _BOX = (
        _PRELUDE
        + "class Box:\n    val: Int32\n"
        + "    def __init__(self, v: Int32) -> None:\n        self.val = v\n")

    def test_ptr_tuple_literal_mixed_elems_route(self):
        # A pointer-repr tuple field from a spelled literal: bare record
        # param into the Optional slot (converting ctor), bare record param
        # into the record slot -- the storage brace-init + tuple_to_storage.
        ctor = _lower_ctor(
            self._BOX
            + "class H:\n    t: tuple[Box | None, Box]\n"
            + "    def __init__(self, a: Box, b: Box) -> None:\n"
            + "        self.t = (a, b)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : t(::tpy::tuple_to_storage<std::tuple<std::optional<Box>, "
            "Box>>(std::tuple<std::optional<Box>, Box>{a, b})) {}\n")

    def test_ptr_tuple_literal_copy_elem_routes(self):
        # An explicit copy(p) element renders the copy-ctor call `Box(p)`.
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class Box:\n    val: Int32\n"
            + "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
            + "class H:\n    t: tuple[Box, Int32]\n"
            + "    def __init__(self, p: Box, n: Int32) -> None:\n"
            + "        self.t = (copy(p), n)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : t(::tpy::tuple_to_storage<std::tuple<Box, int32_t>>("
            "std::tuple<Box, int32_t>{Box(p), n})) {}\n")

    def test_ptr_tuple_literal_optional_param_elem_stays_ast(self):
        # A pointer-repr optional PARAM (`Box*` binding) into the Optional
        # slot has no implicit conversion -- outside the cell, stays AST.
        ctor = _lower_ctor(
            self._BOX
            + "class H:\n    t: tuple[Box | None, Box]\n"
            + "    def __init__(self, a: Box | None, b: Box) -> None:\n"
            + "        self.t = (a, b)\n",
            "H")
        assert ctor is None

    def test_none_into_ptr_field_routes(self):
        # None into a `Ptr[T]` cell renders `p(nullptr)` (the VALUE-form None).
        ctor = _lower_ctor(
            "from tpy import Int32, Ptr\n"
            + "class H:\n    p: Ptr[Int32]\n"
            + "    def __init__(self):\n        self.p = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : p(nullptr) {}\n"

    def test_value_union_sources_route(self):
        # F4 U1 bare renders: a same-union param name, a scalar literal, and
        # the monostate None.
        ctor = _lower_ctor(
            "from tpy import Int32, Float64\n"
            + "class H:\n    u: Int32 | Float64\n    v: Int32 | Float64\n"
            + "    w: Int32 | Float64 | None\n"
            + "    def __init__(self, u: Int32 | Float64):\n"
            + "        self.u = u\n        self.v = 5\n        self.w = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : u(u), v(5), w(std::monostate{}) {}\n")

    def test_value_union_bigint_member_literal_renders_bare(self):
        # An int literal into an `int | float` union renders bare `u(5)` --
        # the AST threads the UNION as the render target, so the BigInt ctor
        # wrap keyed on the literal's own scalar type must not fire.
        ctor = _lower_ctor(
            "class H:\n    u: int | float\n"
            + "    def __init__(self):\n        self.u = 5\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : u(5) {}\n"

    def test_ptr_union_sources_route(self):
        # F4 U2: a borrow ptr-variant param lifts via to_value_variant; a
        # member-record ctor rvalue constructs the variant directly; None is
        # the monostate member.
        ctor = _lower_ctor(
            self._AB
            + "class H:\n    u: A | B\n    v: A | B\n    w: A | B | None\n"
            + "    def __init__(self, u: A | B):\n"
            + "        self.u = u\n        self.v = A(3)\n        self.w = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : u(::tpy::to_value_variant<std::variant<A, B>>(u)), "
            "v(A(3)), w(std::monostate{}) {}\n")

    def test_ptr_union_member_name_source_stays_ast(self):
        # A member-typed record NAME source (`self.u = a` with `a: A`) is not
        # the same-union borrow name `_ptr_union_source_ok` admits -- the AST
        # emits a direct record copy the slice does not mirror -> AST path.
        ctor = _lower_ctor(
            self._AB
            + "class H:\n    u: A | B\n"
            + "    def __init__(self, a: A):\n        self.u = a\n",
            "H")
        assert ctor is None

    def test_f1_tuple_param_storage_lift_routes(self):
        # A borrow pointer-repr tuple param stores via tuple_to_storage,
        # spelled with the field's storage type.
        ctor = _lower_ctor(
            self._AB
            + "class H:\n    t: tuple[A, Int32]\n"
            + "    def __init__(self, t: tuple[A, Int32]):\n        self.t = t\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : t(::tpy::tuple_to_storage<std::tuple<A, int32_t>>(t)) {}\n")

    def test_f1_tuple_copy_source_stays_ast(self):
        # A copy() of a pointer-repr tuple takes the AST's storage-form
        # _gen_copy_expr render, which the MIL slice does not mirror -> AST.
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
            + "class H:\n    t: tuple[A, Int32]\n"
            + "    def __init__(self, t: tuple[A, Int32]):\n        self.t = copy(t)\n",
            "H")
        assert ctor is None

    def test_value_tuple_name_and_literal_route(self):
        # A value tuple copies bare (`t(t)`); a literal spells the slot type.
        ctor = _lower_ctor(
            _PRELUDE
            + "class H:\n    t: tuple[Int32, Int32]\n    u: tuple[Int32, Int32]\n"
            + "    def __init__(self, t: tuple[Int32, Int32]):\n"
            + "        self.t = t\n        self.u = (1, 2)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : t(t), u(std::tuple<int32_t, int32_t>{1, 2}) {}\n")

    def test_demote_mirror_body_local_ref_stays_in_body(self):
        # An init whose RHS reads a body-local demotes on the AST path
        # (`blocked_by_body_local`); THIR mirrors the demote -- the ctor
        # routes with the local decl AND the init in the body.
        ctor = _lower_ctor(
            _PRELUDE
            + "class H:\n    n: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        b = a + 1\n        self.n = b\n",
            "H")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert len(ctor.body) == 2

    def test_demote_nondefault_constructible_field_stays_ast(self):
        # A demoted own-field init of a non-default-constructible field type
        # raises CodeGenError on the AST path -- THIR must reject so that
        # error still fires (never route around a diagnostic).
        ctor = _lower_ctor(
            "from tpy import Int32\n"
            + "from tplib.box import Box\n"
            + "G: Int32 = 7\n"
            + "class H:\n    b: Box[Int32]\n"
            + "    def __init__(self):\n        self.b = Box(G)\n",
            "H")
        assert ctor is None

    def test_small_families_byte_identical(self):
        # End-to-end byte-identity for the new MIL cells through the THIR seam
        # vs the AST path: optional-None (container inner + value repr), Ptr
        # None, both union families, both tuple families, and a demoted
        # bare-name init.
        src = (
            self._AB
            + "G: Int32 = 9\n"
            + "class H:\n"
            + "    items: list[Int32] | None\n"
            + "    ox: Int32 | None\n"
            + "    vu: Int32 | float\n"
            + "    pu: A | B\n"
            + "    pr: A | B\n"
            + "    pn: A | B | None\n"
            + "    ft: tuple[A, Int32]\n"
            + "    vt: tuple[Int32, Int32]\n"
            + "    def __init__(self, pu: A | B, ft: tuple[A, Int32]):\n"
            + "        self.items = None\n        self.ox = None\n"
            + "        self.vu = 5\n        self.pu = pu\n"
            + "        self.pr = A(3)\n        self.pn = None\n"
            + "        self.ft = ft\n        self.vt = (1, 2)\n"
            + "class D:\n    n: Int32\n"
            + "    def __init__(self):\n        self.n = G\n"
            + "def main():\n"
            + "    a = A(1)\n"
            + "    h = H(a, (a, 2))\n"
            + "    d = D()\n"
            + "    print(d.n)\n"
            + "main()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)


class TestTypedDictCtorCall:
    # The kwargs-pack rewrite: sema turns `connect(host=..., port=...)`
    # into a fi-less `Options(...)` ctor arg with field-ordered
    # positionals; the free-call slot hoists the ArgTemp, the const method
    # slot inlines the expansion (both AST renders mirrored).

    _SRC = (
        "from typing import TypedDict, Unpack\n"
        "from tpy import Int32\n"
        "class Options(TypedDict):\n"
        "    host: str\n"
        "    port: Int32\n"
        "def connect(**kwargs: Unpack[Options]) -> None:\n"
        "    print(kwargs[\"host\"])\n"
        "def main() -> None:\n"
        "    connect(host=\"localhost\", port=Int32(8080))\n"
        "main()\n"
    )

    def test_kwargs_pack_routes_byte_identical(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("ctor.typed_dict")
        compiler, modules = _compile(self._SRC)
        entry = _entry(modules)
        outs = {}
        for flag in (True, False):
            compiler2, modules2 = _compile(self._SRC)
            entry2 = _entry(modules2)
            _, outs[flag] = compiler2.generate_code_to_strings(
                entry2, options=CodeGenOptions(emit_source_comments=False,
                                               thir_codegen=flag))
        assert outs[True] == outs[False]


    def _cpp_pair(self, src: str) -> tuple[str, str]:
        outs = []
        for flag in (True, False):
            compiler, modules = _compile(src)
            entry = _entry(modules)
            _, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=flag))
            outs.append(cpp)
        return outs[0], outs[1]

    def test_method_slot_inlines_byte_identical(self):
        # The const method-slot row inlines the pack expansion
        # (`c.connect(Options("localhost", 8080));` -- no temp).
        src = (
            "from typing import TypedDict, Unpack\n"
            "from tpy import Int32\n"
            "class Options(TypedDict):\n"
            "    host: str\n"
            "    port: Int32\n"
            "class Client:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def connect(self, **kwargs: Unpack[Options]) -> None:\n"
            "        print(kwargs[\"host\"])\n"
            "def main() -> None:\n"
            "    c = Client(1)\n"
            "    c.connect(host=\"localhost\", port=Int32(8080))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        a, b = self._cpp_pair(src)
        assert "c.connect(Options(" in a
        assert a == b

    def test_double_star_unpack_still_defers(self):
        # `connect(**o)` re-spreads an existing pack -- the classifier
        # excludes double_star_unpack shapes at the CALL, so the caller
        # body stays AST (byte-identical via fallback).
        src = (
            "from typing import TypedDict, Unpack\n"
            "from tpy import Int32\n"
            "class Options(TypedDict):\n"
            "    host: str\n"
            "    port: Int32\n"
            "def connect(**kwargs: Unpack[Options]) -> None:\n"
            "    print(kwargs[\"host\"])\n"
            "def use(o: Options) -> None:\n"
            "    connect(**o)\n"
            "def main() -> None:\n"
            "    use(Options(host=\"x\", port=Int32(1)))\n"
            "main()\n"
        )
        a, b = self._cpp_pair(src)
        assert a == b


class TestCtorArgOptionalPtrCtorFace:
    # The optional-ptr 'ctor' face's flush rows: a record-ctor rvalue into
    # a `Inner | None` ctor slot hoists the ArgTemp and lifts its address
    # (`Inner __tmp_N = Inner(7); Holder(&__tmp_N)`).

    _SRC = (
        _PRELUDE
        + "class Inner:\n    v: Int32\n"
        + "    def __init__(self, v: Int32):\n        self.v = v\n"
        + "class Holder:\n    opt: Inner | None\n"
        + "    def __init__(self, opt: Inner | None):\n        self.opt = opt\n"
        + "def main() -> None:\n"
        + "    h = Holder(Inner(7))\n"
        + "    if h.opt is not None:\n        print(h.opt.v)\n"
        + "main()\n")

    def test_ctor_face_routes_byte_identical(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("optptr.ctor_rvalue")
        outs = []
        for flag in (True, False):
            compiler, modules = _compile(self._SRC)
            entry = _entry(modules)
            _, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=flag))
            outs.append(cpp)
        assert "__tmp_1 = Inner(" in outs[0]
        assert outs[0] == outs[1]


def _cpp_both(source: str) -> 'tuple[str, str]':
    """(thir_cpp, ast_cpp) for the entry module -- the byte-identity pair."""
    outs = []
    for flag in (True, False):
        compiler, modules = _compile(source)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=flag))
        outs.append(cpp)
    return outs[0], outs[1]


class TestCtorOwnStrLiteralArg:
    """The `ctor.own_str_literal` row: a str LITERAL passes bare into an
    `Own[str]` ctor slot (prvalue conversion, no auto-move cascade)."""

    SRC = (
        "from tpy import Own\n"
        "class H:\n"
        "    s: str\n"
        "    def __init__(self, s: Own[str]) -> None:\n"
        "        self.s = s\n"
        "def main() -> None:\n"
        "    h = H(\"hi\")\n"
        "    print(h.s)\n"
        "main()\n"
    )

    def test_literal_routes_and_witnesses(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert w.get("ctor.own_str_literal", 0) > 0
        t, a = _cpp_both(self.SRC)
        assert t == a

    def test_lvalue_name_stays_off_the_row(self):
        # The row is literal-keyed: a NAME source must ride the auto-move
        # cascade rows (or fall back), never the bare-literal render.
        src = (
            "from tpy import Own\n"
            "class H:\n"
            "    s: str\n"
            "    def __init__(self, s: Own[str]) -> None:\n"
            "        self.s = s\n"
            "def main() -> None:\n"
            "    t = \"hi\"\n"
            "    h = H(t)\n"
            "    print(h.s)\n"
            "main()\n"
        )
        _, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.own_str_literal", 0) == 0
        t, a = _cpp_both(src)
        assert t == a


class TestBaseInitCoerceAndValueOptArgs:
    """The wave-6 base-init arg rows: an IDENTITY str coerce (String param
    into a `str` base slot, both sides std::string) peels to its inner name
    (`: ::tpy::OSError(message)`); a value-repr Optional param passes WHOLE
    into the matching base slot (`: Tagged(tag, note)` -- the
    allow_whole_optional read). A temp-registering CALL arg keeps the ctor
    on the AST path (no flush point in a base-init cell)."""

    _BASE = (
        "from typing import Optional\n"
        "from tpy import Int32, String\n"
        "class Base:\n"
        "    tag: str\n"
        "    note: Optional[str]\n"
        "    n: Int32 | None\n"
        "    def __init__(self, tag: str, note: Optional[str] = None,\n"
        "                 n: Int32 | None = None) -> None:\n"
        "        self.tag = tag\n"
        "        self.note = note\n"
        "        self.n = n\n")

    def test_identity_coerce_and_value_opt_params_route(self):
        ctor = _lower_ctor(
            self._BASE
            + "class Sub(Base):\n"
            + "    extra: Int32\n"
            + "    def __init__(self, tag: String, note: Optional[str],\n"
            + "                 n: Int32 | None) -> None:\n"
            + "        super().__init__(tag, note, n)\n"
            + "        self.extra = 1\n",
            "Sub")
        assert ctor is not None
        assert _ctor_tail(ctor).startswith(" : Base(tag, note, n)")

    def test_identity_coerce_value_opt_byte_identical(self):
        from ..codegen_cpp.context import CodeGenOptions
        src = (self._BASE
               + "class Sub(Base):\n"
               + "    extra: Int32\n"
               + "    def __init__(self, tag: String, note: Optional[str],\n"
               + "                 n: Int32 | None) -> None:\n"
               + "        super().__init__(tag, note, n)\n"
               + "        self.extra = 1\n"
               + "def main() -> None:\n"
               + "    s = Sub(String(\"a\"), \"note\", 5)\n"
               + "    print(s.tag, s.extra)\n"
               + "main()\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)
        outs = [compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=t))
                for t in (True, False)]
        assert outs[0] == outs[1]

    def test_call_arg_still_defers(self):
        ctor = _lower_ctor(
            self._BASE
            + "def mk_tag() -> str:\n"
            + "    return \"t\"\n"
            + "class SubCall(Base):\n"
            + "    def __init__(self) -> None:\n"
            + "        super().__init__(mk_tag())\n",
            "SubCall")
        assert ctor is None


class TestMilSourceWidenings:
    """Three ctor member-init source legs: a scalar LITERAL at a value-opt
    field (`slot(1)` -- the converting ctor absorbs the retyped literal);
    a Send[Own[T]] param moving into the T field (the own-param set
    unwraps the Send marker); an owned-str-returning call rvalue landing
    bare (`message(s.speak())`)."""

    def test_value_opt_literal_mil_routes(self):
        src = ("class Holder:\n"
               "    slot: int | None\n"
               "    def __init__(self) -> None:\n"
               "        self.slot = 1\n"
               "def main() -> None:\n"
               "    h = Holder()\n"
               "    print(h.slot is None)\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_send_own_param_mil_moves(self):
        # The corpus shape: an OPEN bounded T (the Send marker check
        # defers on the type param), moved into the T field -- the
        # own-param set peels the Send wrapper.
        src = ("from tpy import Int32, Own, Send, nocopy\n"
               "from typing import Protocol\n"
               "class Counted(Protocol):\n"
               "    def value(self) -> Int32: ...\n"
               "@nocopy\n"
               "class Token(Counted):\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "    def value(self) -> Int32:\n"
               "        return self.n\n"
               "class Holder[T: Counted]:\n"
               "    item: T\n"
               "    def __init__(self, item: Send[Own[T]]) -> None:\n"
               "        self.item = item\n"
               "def main() -> None:\n"
               "    h = Holder(Token(3))\n"
               "    print(h.item.value())\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_str_call_rvalue_mil_routes(self):
        src = ("class Maker:\n"
               "    def __init__(self) -> None:\n"
               "        pass\n"
               "    def speak(self) -> str:\n"
               "        return \"hi\"\n"
               "class Recorder:\n"
               "    message: str\n"
               "    def __init__(self, s: Maker) -> None:\n"
               "        self.message = s.speak()\n"
               "def main() -> None:\n"
               "    print(Recorder(Maker()).message)\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_borrow_str_method_mil_stays_ast(self):
        # The str-call MIL leg is RVALUE-only: a borrow-returning (T&-ish
        # view) accessor at the str field keeps falling back.
        src = ("class Holder:\n"
               "    msg: str\n"
               "    def __init__(self, m: str) -> None:\n"
               "        self.msg = m\n"
               "    def peek(self) -> str:\n"
               "        return self.msg\n"
               "class Wrap:\n"
               "    copy_of: str\n"
               "    def __init__(self, h: Holder) -> None:\n"
               "        self.copy_of = h.peek()\n"
               "def main() -> None:\n"
               "    print(Wrap(Holder(\"x\")).copy_of)\n"
               "main()\n")
        # h.peek() returns an owned str RVALUE here, so this ROUTES; the
        # genuinely-borrow flavor (a `String`-returning accessor) is not
        # constructible in a plain fixture -- assert byte-identity either
        # way so the leg's behavior is pinned.
        _assert_byte_identical(src)
