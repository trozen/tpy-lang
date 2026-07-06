"""THIR form ladder F1/F2 rungs: record locals + field reads, pointer-local
reseat, Optional borrow<->storage writes/returns, rebind slots, _move."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..codegen_cpp.forms import LocalBinding
from .nodes import (
    Form, THIRAssign, THIRCall, THIRCtorCall, THIRFieldAccess, THIRFormConvert, THIRLiteral,
    THIRName, THIRReturn, THIRSelf, THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower_ctx, _lower_ctx_witnessed, _fn, _F1_RECORDS,
)

# --- F1 form rung: single-assignment non-value record locals + field reads ---

class TestF1Eligibility:
    def test_ref_alias_local_eligible(self):
        # x = b.inner -- a plain record field read binds a single-assignment T&
        # alias (REF_ALIAS); x.value is a scalar field read off it.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    x = b.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        assert decl.form is Form.BORROW
        assert decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFieldAccess)
        assert decl.init.field_cpp == "inner" and not decl.init.is_arrow
        assert decl.init.form is Form.STORAGE
        # the scalar field read off the REF_ALIAS local
        read = fn.body[1].value
        assert isinstance(read, THIRFieldAccess) and read.field_cpp == "value"
        assert read.form is Form.VALUE

    def test_optional_to_ptr_local_eligible(self):
        # p = b.opt -- a storage-form Optional[record] field read lifts to a
        # borrow T* via optional_to_ptr (OPTIONAL_TO_PTR); const because b is a
        # const-ref param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    p = b.opt\n    return 0\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const
        assert decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFormConvert)
        assert decl.init.form is Form.BORROW
        assert isinstance(decl.init.value, THIRFieldAccess)
        assert decl.init.value.form is Form.STORAGE

    def test_mixed_mutation_free_function_keys_on_param_index(self):
        # A free function mutates b but only reads a.opt; the const verdict must
        # key on a's param index (0), not b's (1) -- exercises the index-based
        # _param_is_const(record_name=None) lookup that a single-param fn never does.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def mix(a: Box, b: Box) -> Int32:\n"
            + "    b.n = 1\n    p = a.opt\n    return 0\n")
        decl = next(s for s in _fn(thir, "mix").body
                    if isinstance(s, THIRVarDecl)
                    and s.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR)
        assert decl.form is Form.BORROW and decl.is_const

    def test_scalar_field_read_off_param_eligible(self):
        # The working, common F1 pattern: scalar field reads off a record param
        # (value form, no borrow local). `return p.x + p.y` and `a = p.x`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    a = b.n\n    return a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        # a scalar local; the field read is a plain value-form field access
        assert decl.cpp_local_representation is None
        assert isinstance(decl.init, THIRFieldAccess)
        assert decl.init.form is Form.VALUE and decl.init.field_cpp == "n"

    def test_method_routes_via_self_receiver(self):
        # The method frontier (M1): a method's `self.field` reads route the same
        # as a record param's. `x = self.b` binds a REF_ALIAS off the `this`
        # receiver; `x.n` is a scalar read off it.
        thir = _lower_ctx(
            _F1_RECORDS
            + "class Wrap:\n    b: Box\n"
            + "    def __init__(self, b: Own[Box]):\n        self.b = b\n"
            + "    def get(self) -> Int32:\n        x = self.b\n        return x.n\n")
        fn = _fn(thir, "get")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        # the field source reads `self->b` (the `this` pointer renders `->`)
        assert isinstance(decl.init, THIRFieldAccess)
        assert isinstance(decl.init.receiver, THIRSelf) and decl.init.is_arrow

    def test_reassigned_nonvalue_local_routes_as_pointer(self):
        # F2: a reassigned plain-record local with lvalue field sources is a
        # reseatable `T*` pointer-local (POINTER), no longer AST-only.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box) -> Int32:\n"
            + "    x = b.inner\n    x = c.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.POINTER
        assert decl.form is Form.BORROW and decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFormConvert)  # &(b.inner)
        assert decl.init.form is Form.BORROW
        assert isinstance(decl.init.value, THIRFieldAccess)
        assert decl.init.value.form is Form.STORAGE

    def test_call_passing_record_arg_is_ineligible(self):
        # Passing an Own[record] / record param positionally crosses an ownership
        # boundary (an Own param auto-moves at last use: `consume(std::move(p))`),
        # which the bare-name THIRCall emit does not reproduce -- so the caller
        # stays on the AST path even though the callee is a plain free function.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def consume(p: Own[Inner]) -> Int32:\n        return p.value\n"
            + "def forward(b: Box) -> Int32:\n        return consume(b.inner)\n")
        # consume itself (Own[record] param + scalar field read) is eligible;
        # forward (passes a record arg) is not.
        assert _fn(thir, "consume") is not None
        assert _fn(thir, "forward") is None

    def test_method_call_on_record_routes(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    x = b.inner\n    return x.value + b.n\n")
        # A single-overload plain method call on a bare record name routes
        # (`c.bump()` -- the record method-call row of the call-arg cascade;
        # see test_thir_callargs for the full gate matrix).
        thir2 = _lower_ctx(
            _F1_RECORDS
            + "class Counter:\n    k: Int32\n"
            + "    def __init__(self):\n        self.k = 0\n"
            + "    def bump(self) -> Int32:\n        self.k = self.k + 1\n        return self.k\n"
            + "def g(c: Counter) -> Int32:\n    return c.bump()\n")
        assert _fn(thir2, "g") is not None
        # The pure field-read function is eligible.
        assert _fn(thir, "f") is not None



class TestF1Emit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def read_ref(b: Box) -> Int32:\n    x = b.inner\n    return x.value\n"
        + "def read_opt(b: Box) -> Int32:\n    p = b.opt\n    return 0\n"
        + "def scalar(b: Box) -> Int32:\n    a = b.n\n    return a + b.n\n"
        + "def chain(b: Box) -> Int32:\n    x = b.inner\n    y = x.value\n    return y\n"
        # const path: a readonly receiver makes the REF_ALIAS a `const Inner&`,
        # and the chained Optional read off that const local a `const Inner*`
        # (exercises both _f1_is_const branches: the ReadonlyType read and the
        # const-propagation through a const F1 local).
        + "def ro_chain(b: readonly[Box]) -> Int32:\n"
        + "    x = b.inner\n    p = x.opt\n    return x.value\n"
        + "def main():\n"
        + "    box = Box(Inner(3))\n"
        + "    print(read_ref(box) + read_opt(box) + scalar(box) + chain(box) + ro_chain(box))\n"
        + "main()\n"
    )

    def test_f1_byte_identical(self):
        # The load-bearing F1 contract: every routed form (REF_ALIAS,
        # OPTIONAL_TO_PTR, scalar field reads) emits identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_ref_alias_emits_reference(self):
        # T& alias of the field storage (non-const here: the field is a plain
        # record off the param -- sema does not mark the read readonly).
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner& x = b.inner;" in cpp
        assert "return x.value;" in cpp

    def test_optional_to_ptr_emits_lift(self):
        # const because the receiver param is const (an F1 body cannot mutate it).
        cpp = self._cpp(self.SRC, thir=True)
        assert "const Inner* p = ::tpy::optional_to_ptr(b.opt);" in cpp

    def test_scalar_field_read_emits(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "int32_t a = b.n;" in cpp        # scalar field read into a local
        assert cpp.count("b.n") >= 2            # the read + the return operand

    def test_const_borrow_local_forms(self):
        # A readonly receiver yields a `const Inner&` REF_ALIAS, and the chained
        # Optional read off that const local a `const Inner*` -- guards the two
        # _f1_is_const const paths (the byte-identical assertion above already
        # pins them to the AST path; these check the const spelling explicitly).
        cpp = self._cpp(self.SRC, thir=True)
        assert "const Inner& x = b.inner;" in cpp
        assert "const Leaf* p = ::tpy::optional_to_ptr(x.opt);" in cpp



# --- F2 form rung: reassigned/rebound pointer-locals (lvalue reseat) ---


class TestF2PointerLocal:
    def test_rvalue_reseat_is_ineligible(self):
        # Reseating from an rvalue (a constructor) needs the `__slot_N` rebind
        # machinery -- deferred past F2's lvalue-reseat slice -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = Inner(9)\n    return x.value\n")
        assert _fn(thir, "f") is None

    def test_name_alias_reseat_is_ineligible(self):
        # Reseating from a name (not a field source) is the deferred name-alias
        # case -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    y = c.inner\n"
            + "    if which < 0:\n        x = y\n    return x.value\n")
        assert _fn(thir, "f") is None

    def test_reseat_lowers_to_assign_with_convert(self):
        # The reseat is a THIRAssign whose value is the `&(...)` storage->borrow
        # convert; the read off the pointer-local is an arrow field access.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        reseat = fn.body[1].then_body[0]
        assert isinstance(reseat, THIRAssign) and reseat.target.name == "x"
        assert isinstance(reseat.value, THIRFormConvert)
        assert reseat.value.form is Form.BORROW
        assert isinstance(reseat.value.value, THIRFieldAccess)  # c.inner
        ret = fn.body[2]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow



class TestF2Emit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def reseat(b: Box, c: Box, which: Int32) -> Int32:\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n"
        + "def main():\n"
        + "    box = Box(Inner(3))\n    print(reseat(box, box, -1))\n"
        + "main()\n"
    )

    def test_f2_byte_identical(self):
        # The load-bearing F2a contract: the pointer-local init / reseat / arrow
        # read emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_pointer_local_init_and_reseat(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner* x = &(b.inner);" in cpp
        assert "x = &(c.inner);" in cpp

    def test_arrow_read_off_pointer_local(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return x->value;" in cpp

    def test_const_pointer_local(self):
        # A readonly receiver makes the reseatable pointer-local a `const Inner*`
        # (exercises _f1_is_const for POINTER), reseated and read identically.
        src = (
            _F1_RECORDS
            + "def f(b: readonly[Box], c: readonly[Box], which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n"
            + "def main():\n    box = Box(Inner(1))\n    print(f(box, box, -1))\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "const Inner* x = &(b.inner);" in cpp
        assert "return x->value;" in cpp



# --- F2b form rung: Optional borrow->storage write (ptr_to_optional) ---


class TestF2bWrite:
    def test_optional_field_write_routes(self):
        # p = src.opt (OPTIONAL_TO_PTR borrow) ; dst.opt = p lowers to a field-target
        # THIRAssign whose value is the borrow->storage convert (ptr_to_optional).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n")
        fn = _fn(thir, "move_opt")
        assert fn is not None
        write = fn.body[1]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "opt"
        assert isinstance(write.value, THIRFormConvert) and write.value.form is Form.STORAGE
        assert write.value.value.form is Form.BORROW  # the `p` borrow being lifted

    def test_copy_acknowledged_value_is_ineligible(self):
        # `copy(p)` (the explicit acknowledgment) is a call -- deferred to the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "from tpy import copy\n"
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = copy(p)\n")
        assert _fn(thir, "move_opt") is None

    def test_scalar_field_write_routes_as_plain_assign(self):
        # A scalar (non-optional) field write is not the F2b borrow->storage shape;
        # it routes via the scalar-field-write cell as a plain value assign.
        thir = _lower_ctx(
            _F1_RECORDS + "def setn(dst: Box):\n    dst.n = 5\n")
        st = _fn(thir, "setn").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert not isinstance(st.value, THIRFormConvert)

    def test_ref_alias_value_is_ineligible(self):
        # A `T&` REF_ALIAS value is not a `T*` pointer source (the AST path emits it
        # differently), so an optional-field write from it stays on the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(src: Box, dst: Box):\n    x = src.inner\n    dst.opt = x\n")
        assert _fn(thir, "f") is None



class TestCharField:
    """Char record fields (`self.c: Char` -> `char c;`): reads and writes off
    F1-record receivers render exactly like scalar fields (the access itself is
    type-independent; the Char VALUE lands only in positions whose own gates
    admit it)."""

    _SRC = (
        "from tpy import Char\n"
        "class P:\n"
        "    c: Char\n"
        "    def __init__(self, c: Char):\n        self.c = c\n"
        "    def get(self) -> Char:\n        return self.c\n"
        "    def put(self, c: Char) -> None:\n        self.c = c\n"
        "def swap(p: P, z: Char) -> Char:\n"
        "    x = p.c\n    p.c = z\n    return x\n"
        "def show(p: P) -> None:\n"
        '    if p.c == "x":\n        print(p.c)\n'
    )

    def test_char_field_read_write_route(self):
        thir = _lower_ctx(self._SRC)
        fn = _fn(thir, "swap")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.resolved_type.to_cpp() == "char"
        write = fn.body[1]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess)
        assert not isinstance(write.value, THIRFormConvert)  # plain value assign
        assert _fn(thir, "get") is not None   # `return self.c`
        assert _fn(thir, "put") is not None   # `self.c = c` via self receiver
        assert _fn(thir, "show") is not None  # compare + print positions

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_char_field_byte_identical(self):
        src = self._SRC + (
            "def main() -> None:\n"
            '    z: Char = "z"\n'
            "    p = P(z)\n"
            "    print(swap(p, z))\n"
            "    show(p)\n"
            "main()\n")
        cpp = self._cpp(src, thir=True)
        assert cpp == self._cpp(src, thir=False)
        assert "char x = p.c;" in cpp
        assert "p.c = z;" in cpp



class TestF2bEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n"
        + "def main():\n    a = Box(Inner(1))\n    b = Box(Inner(2))\n    move_opt(a, b)\nmain()\n"
    )

    def test_f2b_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_ptr_to_optional(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "dst.opt = ::tpy::ptr_to_optional(p);" in cpp

    def test_written_receiver_is_non_const(self):
        # Mutation enters the slice: the written receiver is a non-const `Box&`,
        # the read-only one a `const Box&` (pure const_borrow_params sema read).
        cpp = self._cpp(self.SRC, thir=True)
        assert "void move_opt(const Box& src, Box& dst)" in cpp



class TestF2PointerReceiver:
    """F2 paths where a POINTER local is itself the field-access receiver, so the
    field renders `x->field`: an Optional READ source (`optional_to_ptr(x->opt)`)
    and an Optional WRITE target (`x->opt = ptr_to_optional(leaf)`). Both are
    admitted by the F2a/F2b gates (a POINTER local is an F1-record receiver) and
    must stay byte-identical to the AST path; neither the scaffold nor the two
    corpus cases exercised them before."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def read_opt(b: Box, c: Box, which: Int32) -> Int32:\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    q = x.opt\n    return 0\n"
        + "def write_opt(b: Box, c: Box, src: Inner, which: Int32):\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    leaf = src.opt\n    x.opt = leaf\n"
        + "def main():\n"
        + "    bx = Box(Inner(1))\n    cx = Box(Inner(2))\n    s = Inner(3)\n"
        + "    print(read_opt(bx, cx, -1))\n    write_opt(bx, cx, s, 1)\nmain()\n"
    )

    def test_both_route(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "read_opt") is not None
        assert _fn(thir, "write_opt") is not None

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_optional_source_off_pointer_local(self):
        # `q = x.opt` off a POINTER local -> the storage read uses `x->opt`.
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::optional_to_ptr(x->opt)" in cpp

    def test_optional_write_off_pointer_local(self):
        # `x.opt = leaf` off a POINTER local -> the write target uses `x->opt`.
        cpp = self._cpp(self.SRC, thir=True)
        assert "x->opt = ::tpy::ptr_to_optional(leaf);" in cpp

    def test_reassigned_optional_local_is_ineligible(self):
        # A reassigned OPTIONAL_TO_PTR (optional pointer-local) needs the rebind-
        # slot machinery, so it stays on the AST path (the optional branch of
        # classify_local_binding returns OTHER for a reassigned name).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    p = b.opt\n    if which < 0:\n        p = c.opt\n    return 0\n")
        assert _fn(thir, "f") is None



# --- F2c form rung: storage-form Optional[record] return + None write ---


class TestF2cReturn:
    def test_borrow_return_routes(self):
        # A storage-form `Own[Inner] | None` return lifts a borrow `T*` via
        # ptr_to_optional (copy): the return value is a borrow->storage convert.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def find(b: Box) -> Own[Inner] | None:\n    p = b.opt\n    return p\n")
        fn = _fn(thir, "find")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFormConvert) and ret.value.form is Form.STORAGE
        assert ret.value.value.form is Form.BORROW  # the `p` borrow being lifted

    def test_none_return_routes(self):
        # `return None` into a storage-form Optional lowers to a STORAGE-form None
        # literal (-> std::nullopt), not a borrow convert.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def nothing(b: Box) -> Own[Inner] | None:\n    return None\n")
        fn = _fn(thir, "nothing")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRLiteral) and ret.value.value is None
        assert ret.value.form is Form.STORAGE

    def test_pointer_repr_return_routes_bare_name(self):
        # `Inner | None` is pointer-repr (the function returns a borrow
        # `Inner*`); an already-pointer OPTIONAL_TO_PTR local returns bare
        # (the Optional-param cell's return arm).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Inner | None:\n    p = b.opt\n    return p\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and not ret.value.deref

    def test_rvalue_return_is_ineligible(self):
        # A non-borrow, non-None source (here an rvalue ctor) into the storage
        # return slot is the direct-construction branch -- deferred to the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Own[Inner] | None:\n    return Inner(5)\n")
        assert _fn(thir, "f") is None

    def test_pointer_local_borrow_return_routes(self):
        # The borrow-return source via the POINTER (not OPTIONAL_TO_PTR) branch of
        # `_is_borrow_ptr_local`: a reseatable `T*` returned into Own[Inner]|None
        # copies (a POINTER is a non-owning borrow -> move=False).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Own[Inner] | None:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[2]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE and ret.value.move is False



class TestF2cNoneWrite:
    def test_none_field_write_routes(self):
        # `b.opt = None` lowers to a field-target THIRAssign whose value is a
        # STORAGE-form None literal (-> std::nullopt), no form convert.
        thir = _lower_ctx(
            _F1_RECORDS + "def clear(b: Box):\n    b.opt = None\n")
        fn = _fn(thir, "clear")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "opt"
        assert isinstance(write.value, THIRLiteral) and write.value.value is None
        assert write.value.form is Form.STORAGE



class TestF2cEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def find(b: Box) -> Own[Inner] | None:\n    p = b.opt\n    return p\n"
        + "def nothing(b: Box) -> Own[Inner] | None:\n    return None\n"
        + "def clear(b: Box):\n    b.opt = None\n"
        + "def main():\n"
        + "    bx = Box(Inner(3))\n    clear(bx)\n    a = find(bx)\n    c = nothing(bx)\n    print(0)\n"
        + "main()\n"
    )

    def test_f2c_byte_identical(self):
        # The load-bearing F2c contract: the borrow-return lift, the None return,
        # and the None field write all emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_borrow_return_emits_ptr_to_optional(self):
        assert "return ::tpy::ptr_to_optional(p);" in self._cpp(self.SRC, thir=True)

    def test_none_return_emits_nullopt(self):
        assert "return std::nullopt;" in self._cpp(self.SRC, thir=True)

    def test_none_write_emits_nullopt(self):
        assert "b.opt = std::nullopt;" in self._cpp(self.SRC, thir=True)

    def test_none_write_via_pointer_receiver(self):
        # `x.opt = None` off a POINTER local receiver -> `x->opt = std::nullopt;`
        # (the None write through the arrow-receiver path), byte-identical.
        src = (
            _F1_RECORDS
            + "def clearp(b: Box, c: Box, which: Int32):\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    x.opt = None\n"
            + "def main():\n    bx = Box(Inner(1))\n    clearp(bx, bx, 1)\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "x->opt = std::nullopt;" in self._cpp(src, thir=True)



# --- F2d form rung: rvalue rebind-slot pointer-locals (the __slot_N machinery) ---


class TestF2dRebindSlot:
    def test_rvalue_reassigned_routes(self):
        # An rvalue-reassigned plain-record local is a rebind-slot pointer-local:
        # the decl is a REBIND_SLOT whose init is the rvalue ctor (no convert),
        # the reseat a plain THIRAssign of the ctor, and reads are arrow accesses.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def reb() -> Int32:\n"
            + "    p = Inner(1)\n    a = p.value\n    p = Inner(2)\n    return p.value + a\n")
        fn = _fn(thir, "reb")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert decl.form is Form.BORROW and decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRCtorCall) and decl.init.type_cpp == "Inner"
        read = fn.body[1].init  # a = p.value -> arrow read off the pointer-local
        assert isinstance(read, THIRFieldAccess) and read.is_arrow
        reseat = fn.body[2]
        assert isinstance(reseat, THIRAssign) and reseat.target.name == "p"
        assert isinstance(reseat.value, THIRCtorCall) and reseat.value.type_cpp == "Inner"

    def test_single_assignment_rvalue_is_plain_value_local(self):
        # No reassignment -> a plain value local (`Inner p = Inner(1);`), not a
        # rebind-slot pointer-local -- the owned-record decl arm routes it in
        # storage form (no pointer indirection, `.` reads).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n    p = Inner(1)\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is None
        assert decl.form is Form.STORAGE and decl.cpp_type == "Inner"

    def test_kwarg_ctor_normalizes_and_routes(self):
        # sema rewrites a single-param ctor kwarg to a positional arg before
        # lowering (`Inner(value=1)` -> `Inner(1)`), so it still routes as a
        # rebind-slot rvalue source. (The `init.kwargs` guard only fires for a
        # ctor sema leaves un-normalized.)
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    p = Inner(value=1)\n    a = p.value\n    p = Inner(value=2)\n    return a + p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert isinstance(decl.init, THIRCtorCall) and len(decl.init.args) == 1

    def test_record_arg_ctor_source_is_ineligible(self):
        # A rebind-slot ctor whose arg is a non-scalar (a record value-local) needs
        # the AST's arg deref / auto-move, which the bare THIRCall arg emit does not
        # reproduce -- so the arg gate (mirroring _call_eligible) rejects it -> AST
        # path. (Box's ctor takes Own[Inner]; `a` is a record value-local.)
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    a = Inner(0)\n    p = Box(a)\n    p = Box(a)\n    return p.n\n")
        assert _fn(thir, "f") is None

    def test_union_arg_ctor_source_is_ineligible(self):
        # A rebind-slot ctor whose member-valued scalar arg lands in a
        # union-typed __init__ slot hoists a variant temp on the AST path
        # (`_member_valued_union_slot` in `_is_record_rvalue_source`'s arg
        # loop), which the bare `Name(args)` emit does not reproduce -> AST.
        thir = _lower_ctx(
            "from tpy import Int32, Float64\n"
            "class W:\n    u: Int32 | Float64\n"
            "    def __init__(self, v: Int32 | Float64):\n        self.u = v\n"
            "def f(k: Int32) -> Int32:\n"
            "    p = W(k)\n    p = W(k)\n    return 0\n")
        assert _fn(thir, "f") is None

    def test_function_call_rebind_source_routes(self):
        # A by-value record-returning FREE FUNCTION (not a ctor) is also a valid
        # rebind-slot rvalue source; it emits as the bare `make_inner()`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def make_inner() -> Own[Inner]:\n    return Inner(9)\n"
            + "def f() -> Int32:\n"
            + "    p = make_inner()\n    p = make_inner()\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert isinstance(decl.init, THIRCall) and decl.init.callee == "make_inner"

    def test_generic_call_rebind_source_is_ineligible(self):
        # A generic record-returning callee spells inferred type args on the
        # AST path -- the free-call face shares `_call_eligible`'s
        # callee-shape head (`_plain_free_callee_ok`), so it rejects -> AST.
        thir = _lower_ctx(
            _F1_RECORDS
            + "from tpy import ValueType\n"
            + "def mk[T: ValueType](v: T) -> Own[Inner]:\n    return Inner(1)\n"
            + "def f() -> Int32:\n"
            + "    p = mk(5)\n    p = mk(7)\n    return p.value\n")
        assert _fn(thir, "f") is None

    def test_conditional_reseat_routes(self):
        # A REBIND_SLOT reseat inside an `if`-body (the in-branch reseat path).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = Inner(2)\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].cpp_local_representation is LocalBinding.REBIND_SLOT
        reseat = fn.body[1].then_body[0]  # the in-branch reseat
        assert isinstance(reseat, THIRAssign) and isinstance(reseat.value, THIRCtorCall)

    def test_lvalue_reseat_of_rebind_slot_is_ineligible(self):
        # A REBIND_SLOT local (rvalue first decl) reseated with an lvalue field
        # source is the deferred mixed case -- `_is_record_rvalue_source` needs a
        # ctor/call, so it stays on the AST path (mirror of the POINTER+rvalue case).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = b.inner\n    return p.value\n")
        assert _fn(thir, "f") is None



class TestF2dEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def reb() -> Int32:\n"
        + "    p = Inner(1)\n    a = p.value\n    p = Inner(2)\n    return p.value + a\n"
        # two rebind-slot locals -> __slot_1.._slot_4, exercising slot numbering.
        + "def two() -> Int32:\n"
        + "    p = Inner(1)\n    q = Inner(2)\n    p = Inner(3)\n    q = Inner(4)\n"
        + "    return p.value + q.value\n"
        + "def main():\n    print(reb() + two())\nmain()\n"
    )

    def test_f2d_byte_identical(self):
        # The load-bearing F2d contract: the two-slot init, the optional rebind
        # slot, the pointer reseat, and the arrow reads emit identically to the
        # AST path -- including slot numbering across two rebind-slot locals.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_two_slot_init_and_reseat(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner __slot_1 = Inner(1);" in cpp
        assert "std::optional<Inner> __slot_2;" in cpp
        assert "Inner* p = &__slot_1;" in cpp
        assert "p = &*(__slot_2 = Inner(2));" in cpp

    def test_arrow_reads_off_rebind_local(self):
        assert "int32_t a = p->value;" in self._cpp(self.SRC, thir=True)

    def test_slot_numbering_across_two_locals(self):
        # The second rebind-slot local numbers after the first (init then rebind):
        # p -> __slot_1/__slot_2, q -> __slot_3/__slot_4.
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner __slot_3 = Inner(2);" in cpp
        assert "std::optional<Inner> __slot_4;" in cpp
        assert "Inner* q = &__slot_3;" in cpp
        assert "q = &*(__slot_4 = Inner(4));" in cpp

    def test_function_call_source_byte_identical(self):
        # A by-value record-returning function as the rvalue source emits the bare
        # call into the two-slot form, byte-identical to the AST path.
        src = (
            _F1_RECORDS
            + "def make_inner() -> Own[Inner]:\n    return Inner(9)\n"
            + "def f() -> Int32:\n"
            + "    p = make_inner()\n    a = p.value\n    p = make_inner()\n    return p.value + a\n"
            + "def main():\n    print(f())\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "Inner __slot_1 = make_inner();" in self._cpp(src, thir=True)

    def test_conditional_reseat_byte_identical(self):
        # A rebind-slot reseat inside an `if`-body emits identically to the AST path
        # (the slot is allocated at the top-level decl, reused in the branch).
        src = (
            _F1_RECORDS
            + "def f(x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = Inner(2)\n    return p.value\n"
            + "def main():\n    print(f(-1))\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)



# --- F2e form rung: the _move write + return variants (owned source) ---


class TestF2eMove:
    def test_write_move_routes(self):
        # An owned rebind-slot local written into an optional field at last use
        # lifts borrow->storage with move=True (-> ptr_to_optional_move).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def store(dst: Box):\n    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n")
        fn = _fn(thir, "store")
        assert fn is not None
        write = fn.body[2]
        assert isinstance(write, THIRAssign) and isinstance(write.value, THIRFormConvert)
        assert write.value.form is Form.STORAGE and write.value.move is True

    def test_write_copy_when_not_last_use(self):
        # The same rebind-slot read again after the write is not a last use -> copy.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def keep(dst: Box) -> Int32:\n    p = Inner(1)\n    p = Inner(2)\n"
            + "    dst.opt = p\n    return p.value\n")
        write = _fn(thir, "keep").body[2]
        assert isinstance(write.value, THIRFormConvert) and write.value.move is False

    def test_return_move_routes(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def make(flag: Int32) -> Own[Inner] | None:\n"
            + "    p = Inner(1)\n    p = Inner(2)\n    return p\n")
        ret = _fn(thir, "make").body[2]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE and ret.value.move is True

    def test_nonowning_borrow_write_stays_copy(self):
        # Regression: an OPTIONAL_TO_PTR (non-owning) source is never movable, so
        # the optional-field write stays a copy (move=False) -- F2b unchanged.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n")
        write = _fn(thir, "move_opt").body[1]
        assert isinstance(write.value, THIRFormConvert) and write.value.move is False



class TestF2eEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def store(dst: Box):\n    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n"
        + "def keep(dst: Box) -> Int32:\n"
        + "    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n    return p.value\n"
        + "def make(flag: Int32) -> Own[Inner] | None:\n"
        + "    p = Inner(1)\n    p = Inner(2)\n    return p\n"
        + "def main():\n    d = Box(Inner(0))\n    store(d)\n    print(keep(d))\n    r = make(0)\n"
        + "main()\n"
    )

    def test_f2e_byte_identical(self):
        # The load-bearing F2e contract: the move write, the copy write (not last
        # use), and the move return all emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_write_move_emits_move_helper(self):
        assert "dst.opt = ::tpy::ptr_to_optional_move(p);" in self._cpp(self.SRC, thir=True)

    def test_write_copy_emits_copy_helper(self):
        assert "dst.opt = ::tpy::ptr_to_optional(p);" in self._cpp(self.SRC, thir=True)

    def test_return_move_emits_move_helper(self):
        assert "return ::tpy::ptr_to_optional_move(p);" in self._cpp(self.SRC, thir=True)



class TestRecordBorrowReturn:
    def test_param_return_routes(self):
        # `-> Box` is a borrow-form record return (`Box&`); a bare record
        # param name returns bare -- no form convert, no move.
        thir, faces = _lower_ctx_witnessed(
            _F1_RECORDS
            + "def pick(a: Box, b: Box, flag: bool) -> Box:\n"
            + "    if flag:\n        return a\n    return b\n")
        fn = _fn(thir, "pick")
        assert fn is not None
        ret = fn.body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and not ret.value.deref
        assert ret.value.form is Form.BORROW
        assert faces.get("ret.record_borrow", 0) == 2

    def test_ref_alias_local_return_routes(self):
        # A REF_ALIAS local (`x = b.inner` -> `Inner& x`) returns bare.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def g(b: Box) -> Inner:\n    x = b.inner\n    return x\n")
        fn = _fn(thir, "g")
        assert fn is not None
        ret = fn.body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and not ret.value.deref

    def test_field_source_return_is_ineligible(self):
        # A field read (`return b.inner`) is not a bare-name source.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Inner:\n    return b.inner\n")
        assert _fn(thir, "f") is None

    def test_pointer_local_return_is_ineligible(self):
        # A reassigned record local is an F2 pointer-local (`Inner* x`); its
        # return derefs (`return (*x);`) -- a render this cell does not mirror.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, flag: bool) -> Inner:\n"
            + "    x = b.inner\n    if flag:\n        x = c.inner\n    return x\n")
        assert _fn(thir, "f") is None

    def test_self_return_is_ineligible(self):
        # `return self` renders `return *this;` -- not mirrored.
        thir = _lower_ctx(
            _F1_RECORDS
            + "class Chain:\n"
            + "    n: Int32\n"
            + "    def __init__(self):\n        self.n = 0\n"
            + "    def bump(self) -> Chain:\n        self.n += 1\n        return self\n")
        assert _fn(thir, "bump") is None

    def test_narrowed_union_source_is_ineligible(self):
        # An isinstance-narrowed union member read renames to the extraction
        # alias on the AST path -- not the bare-name render; the return arm's
        # `ws.narrowed` exclusion keeps such bodies on the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(u: Box | Inner, d: Inner) -> Inner:\n"
            + "    if isinstance(u, Inner):\n        return u\n"
            + "    return d\n")
        assert _fn(thir, "f") is None

    def test_own_return_takes_storage_arm(self):
        # `-> Own[Inner]` is the storage (by-value) direction: the Own
        # rvalue-ref param returns bare (`return a;`, C++ implicit move) via
        # the storage arm, not the borrow one.
        thir, faces = _lower_ctx_witnessed(
            _F1_RECORDS
            + "def f(a: Own[Inner]) -> Own[Inner]:\n    return a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and not ret.value.deref
        assert faces.get("ret.record_storage", 0) == 1
        assert faces.get("ret.record_borrow", 0) == 0


class TestRecordBorrowReturnEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def pick(a: Box, b: Box, flag: bool) -> Box:\n"
        + "    if flag:\n        return a\n    return b\n"
        + "def main():\n"
        + "    x = Box(Inner(1))\n    y = Box(Inner(2))\n"
        + "    c = pick(x, y, True)\n    print(c.n)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_returns_bare_name(self):
        assert "return b;" in self._cpp(self.SRC, thir=True)


class TestRecordStorageReturn:
    def test_ctor_rvalue_return_routes(self):
        # `return Inner(5)` into `-> Own[Inner]` -- the bare ctor expansion.
        thir, faces = _lower_ctx_witnessed(
            _F1_RECORDS
            + "def f(n: Int32) -> Own[Inner]:\n    return Inner(n)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRCtorCall)
        assert faces.get("ret.record_storage", 0) == 1

    def test_owned_local_return_routes(self):
        # `b = Inner(n); return b` -- owned decl + bare-name return (NRVO).
        thir, faces = _lower_ctx_witnessed(
            _F1_RECORDS
            + "def f(n: Int32) -> Own[Inner]:\n    b = Inner(n)\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[-1].value, THIRName)
        assert faces.get("decl.owned_record", 0) == 1

class TestOwnedRecordDecl:
    def test_reads_and_move_route(self):
        # The owned local is a storage-form receiver: `.` field reads, and a
        # last-use pass into an Own slot moves (`consume(std::move(i))`).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def consume(i: Own[Inner]) -> Int32:\n    return i.value\n"
            + "def f(n: Int32) -> Int32:\n    i = Inner(n)\n    return consume(i)\n")
        assert _fn(thir, "f") is not None

    def test_reassigned_local_stays_rebind_slot(self):
        # A reassigned rvalue local takes the F2d rebind-slot machinery, not
        # the owned-decl arm.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(n: Int32) -> Int32:\n"
            + "    p = Inner(n)\n    p = Inner(n)\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT


class TestRecordStorageReturnEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def make(n: Int32) -> Own[Inner]:\n    b = Inner(n)\n    return b\n"
        + "def make2(n: Int32) -> Own[Inner]:\n    return Inner(n)\n"
        + "def consume(i: Own[Inner]) -> Int32:\n    return i.value\n"
        + "def mv(n: Int32) -> Int32:\n    i = Inner(n)\n    return consume(i)\n"
        + "def main():\n"
        + "    a = make(1)\n    b = make2(2)\n    print(a.value + b.value + mv(3))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_owned_decl_and_nrvo_return(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner b = Inner(n);" in cpp
        assert "return b;" in cpp

    def test_ctor_rvalue_return(self):
        assert "return Inner(n);" in self._cpp(self.SRC, thir=True)

    def test_last_use_move(self):
        assert "return consume(std::move(i));" in self._cpp(self.SRC, thir=True)


_PTR_RECORDS = (
    "from tpy import Int32, Ptr, readonly\n"
    "class Node:\n"
    "    val: Int32\n"
    "    def __init__(self, v: Int32):\n        self.val = v\n"
    "class Handle:\n"
    "    _p: Ptr[Node]\n"
    "    def __init__(self, p: Ptr[Node]):\n        self._p = p\n"
    "    def get(self) -> Ptr[Node]:\n        return self._p\n"
    "    def reset(self, p: Ptr[Node]):\n        self._p = p\n"
)


class TestPtrValueFamily:
    def test_param_passthrough_routes(self):
        thir, faces = _lower_ctx_witnessed(
            _PTR_RECORDS
            + "def pass_ptr(p: Ptr[Node]) -> Ptr[Node]:\n    return p\n")
        fn = _fn(thir, "pass_ptr")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and not ret.value.deref
        # One admission per Ptr slot across the module: Handle ctor param +
        # MIL field, get's return sig + field-read result, reset's param +
        # field write, pass_ptr's param+return (the sig gate checks param
        # then return; the return check is the 7th). An exact pin so a
        # dropped call site (e.g. the return-side check) fails loudly.
        assert faces.get("ptr.value_slot", 0) == 7

    def test_field_read_return_routes(self):
        # `return self._p` -- the dominant stdlib shape (re.py handles).
        thir = _lower_ctx(_PTR_RECORDS)
        fn = _fn(thir, "get")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess)

    def test_field_write_routes_plain_assign(self):
        # `self._p = p` -- a plain value assign, no borrow<->storage convert.
        thir = _lower_ctx(_PTR_RECORDS)
        fn = _fn(thir, "reset")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.value, THIRName)  # bare, not a THIRFormConvert

    def test_ptr_local_from_call_routes(self):
        # `q = pass_ptr(p)` -- a Ptr value local (`Node* q = pass_ptr(p);`).
        thir = _lower_ctx(
            _PTR_RECORDS
            + "def pass_ptr(p: Ptr[Node]) -> Ptr[Node]:\n    return p\n"
            + "def chain(p: Ptr[Node]) -> Ptr[Node]:\n"
            + "    q = pass_ptr(p)\n    return q\n")
        fn = _fn(thir, "chain")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is None

    def test_member_access_through_ptr_falls_back(self):
        # `p.val` takes the AST's `::tpy::deref_check(p).val` non-null render
        # -- not mirrored, the body stays on the AST path.
        thir = _lower_ctx(
            _PTR_RECORDS
            + "def read_ptr(p: Ptr[readonly[Node]]) -> Int32:\n    return p.val\n")
        assert _fn(thir, "read_ptr") is None


class TestPtrValueFamilyEmit:
    def _cpp(self, src: str, thir: bool):
        # hpp + cpp: the inline method bodies (`get` / `reset`) land in the
        # header, the free functions in the source.
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _PTR_RECORDS
        + "def pass_ptr(p: Ptr[Node]) -> Ptr[Node]:\n    return p\n"
        + "def chain(p: Ptr[Node]) -> Ptr[Node]:\n"
        + "    q = pass_ptr(p)\n    return q\n"
        + "def main():\n    print(0)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_ptr_local_decl_spelling(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Node* q = pass_ptr(p);" in cpp
        assert "return this->_p;" in cpp
        assert "this->_p = p;" in cpp
