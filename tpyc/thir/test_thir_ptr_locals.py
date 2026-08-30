"""Slot-hoist pointer-repr locals (OPT_PTR_SLOT + the union slot kinds).

Routes/ineligible pairs for the gate + targeted byte-identical tests (the
byte-diff in miniature) for each emit arm: the None init (`T* x = nullptr;`
with the rebind-slot pre-decl), the rvalue init slot (`T __slot_N = ...;`),
the None / rvalue reseats, and the ptr-variant union rvalue / address kinds.
"""

from .testutil import (_compile, _entry, _lower_ctx, _lower_ctx_witnessed,
                       _fn, _F1_RECORDS, _assert_byte_identical,
                       _assert_routes_byte_identical, _thir_ctx)
from ..codegen_cpp import CodeGenOptions
from ..codegen_cpp.forms import LocalBinding
from .nodes import (
    Form,
    PtrSlotKind,
    THIRAssign,
    THIRPtrLocalDecl,
    THIRPtrLocalRebind,
    THIRVarDecl,
)

_UNION_RECORDS = (
    "from tpy import Int32, Own, readonly\n"
    "class Dog:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Cat:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
)


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


class TestOptPtrSlotDecl:
    def test_none_init_with_rvalue_reseat_routes(self):
        # `p = None` + later rvalue reseat: the decl pre-declares the
        # `std::optional<Inner>` rebind slot, the reseat writes through it.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    p: Inner | None = None\n"
            + "    p = Inner(1)\n"
            + "    if p is not None:\n        return p.value\n"
            + "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.OPT_NONE
        assert decl.needs_rebind_slot and decl.cpp_type == "Inner"
        reseat = fn.body[1]
        assert isinstance(reseat, THIRAssign) and reseat.target.name == "p"

    def test_none_init_single_assignment_routes(self):
        # Never reassigned: no rebind-slot pre-decl.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> bool:\n"
            + "    p: Inner | None = None\n"
            + "    return p is None\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.OPT_NONE
        assert not decl.needs_rebind_slot

    def test_rvalue_init_routes(self):
        # `p: Inner | None = Inner(7)` -> direct init slot + `&__slot_1`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    p: Inner | None = Inner(7)\n"
            + "    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.OPT_RVALUE
        assert decl.init is not None and not decl.needs_rebind_slot

    def test_none_reseat_routes(self):
        # A None reseat renders `p = nullptr;` via the rebind node (the
        # slot-holder path -- the decl's rvalue init makes it a slot local).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> bool:\n"
            + "    p: Inner | None = Inner(1)\n"
            + "    p = None\n"
            + "    return p is None\n")
        fn = _fn(thir, "f")
        assert fn is not None
        reseat = fn.body[1]
        assert isinstance(reseat, THIRPtrLocalRebind)
        assert reseat.kind is PtrSlotKind.OPT_NONE

    def test_no_init_decl_routes(self):
        # `p: Inner | None` (annotation only): the same `T* p = nullptr;`
        # render as `= None`, but NO rebind-slot pre-decl -- the first
        # rvalue reseat declares its plain block slot in place
        # (INLINE_RVALUE: `Inner __slot_N = Inner(1); p = &__slot_N;`).
        src = (
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    p: Inner | None\n"
            + "    p = Inner(1)\n"
            + "    if p is not None:\n        return p.value\n"
            + "    return 0\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.OPT_NONE
        assert not decl.needs_rebind_slot
        reseat = fn.body[1]
        assert isinstance(reseat, THIRPtrLocalRebind)
        assert reseat.kind is PtrSlotKind.INLINE_RVALUE
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_no_init_double_reseat_reuses_slot(self):
        # The second rvalue reseat reuses the first's plain slot
        # (`p = &(__slot_N = Inner(2));`).
        src = (
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    p: Inner | None\n"
            + "    p = Inner(1)\n"
            + "    p = Inner(2)\n"
            + "    if p is not None:\n        return p.value\n"
            + "    return 0\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "Inner __slot_1 = Inner(1);" in cpp
        assert "p = &(__slot_1 = Inner(2));" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_no_init_ptr_param_copy_reseat_routes(self):
        # `q = a` off a same-Optional borrow param copies the pointer bare.
        src = (
            _F1_RECORDS
            + "def f(a: Inner | None) -> bool:\n"
            + "    q: Inner | None\n"
            + "    q = a\n"
            + "    return q is None\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        reseat = fn.body[1]
        assert isinstance(reseat, THIRAssign)
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_no_init_branch_reseat_still_defers(self):
        # A branch-positioned rvalue reseat would block-scope the slot
        # under the branch -- stays AST.
        src = (
            _F1_RECORDS
            + "def f(c: bool) -> bool:\n"
            + "    p: Inner | None\n"
            + "    if c:\n        p = Inner(1)\n"
            + "    else:\n        p = Inner(2)\n"
            + "    return p is None\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_own_optional_call_init_routes_slot_lift(self):
        # An `Own[Inner | None]`-returning call init on a REASSIGNED name:
        # the OPT_STORAGE_CALL slot machinery routes it (`std::optional
        # <Inner> __slot_1 = make(); Inner* p = optional_to_ptr(__slot_1);`)
        # and the None reseat renders the plain `p = nullptr;` (former
        # fence, converted per the un-defer rule -- dualgen-verified
        # byte-identical when the reassigned admission landed).
        src = (_F1_RECORDS
               + "def make() -> Own[Inner | None]:\n    return Inner(1)\n"
               + "def f() -> bool:\n"
               + "    p: Inner | None = make()\n"
               + "    p = None\n"
               + "    return p is None\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "std::optional<Inner> __slot_1 = make();" in cpp
        assert "p = nullptr;" in cpp

    def test_own_optional_call_local_name_reseat_stays_ast(self):
        # The OPT_STORAGE_CALL boundary: a NAME-source reseat of a
        # slot-carrying local (`p = q`) is not the own-call re-fill shape --
        # the arm rejects it (the generic THIRAssign rebind emit would
        # hijack it into the `&*(__slot = ...)` render), so the body stays
        # AST byte-identically.
        src = (_F1_RECORDS
               + "def make() -> Own[Inner | None]:\n    return Inner(1)\n"
               + "def f(q: Inner | None) -> bool:\n"
               + "    p: Inner | None = make()\n"
               + "    p = q\n"
               + "    return p is None\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_lvalue_reseat_routes(self):
        # Reseating a pointer-repr Optional local from a field lvalue
        # (`p = b.inner`) routes byte-identically via the lvalue-reseat arm.
        src = (_F1_RECORDS
               + "def f(b: Box) -> bool:\n"
               + "    p: Inner | None = None\n"
               + "    p = b.inner\n"
               + "    return p is None\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_readonly_pointee_is_ineligible(self):
        # A readonly-rooted Optional local binds `const T*` (const
        # indirection) -- a later rung; the body falls back.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: readonly[Box]) -> Int32:\n"
            + "    p = b.opt\n"
            + "    q: Inner | None = None\n"
            + "    q = Inner(2)\n"
            + "    return 0\n")
        # The const OPTIONAL_TO_PTR read of b.opt still routes (existing
        # arm); the None+rvalue local `q` is mutable and routes; nothing
        # here requires the const-slot rung, so assert the readonly SOURCE
        # local `p` did not silently take a slot arm.
        fn = _fn(thir, "f")
        if fn is not None:
            decl = fn.body[0]
            assert not isinstance(decl, THIRPtrLocalDecl)


class TestOptPtrSlotByteIdentical:
    SRC_NONE_RESEAT = (
        _F1_RECORDS
        + "def f() -> Int32:\n"
        + "    p: Inner | None = None\n"
        + "    p = Inner(1)\n"
        + "    if p is not None:\n        return p.value\n"
        + "    return 0\n"
        + "def g() -> Int32:\n"
        + "    q: Inner | None = Inner(3)\n"
        + "    r = q.value\n"
        + "    q = None\n"
        + "    if q is None:\n        return r\n"
        + "    return 0\n")

    def test_byte_identical(self):
        assert (_cpp(self.SRC_NONE_RESEAT, thir=True)
                == _cpp(self.SRC_NONE_RESEAT, thir=False))
        cpp = _cpp(self.SRC_NONE_RESEAT, thir=True)
        assert "std::optional<Inner> __slot_1;" in cpp
        assert "Inner* p = nullptr;" in cpp
        assert "p = &*(__slot_1 = Inner(1));" in cpp
        assert "Inner __slot_1 = Inner(3);" in cpp
        assert "Inner* q = &__slot_1;" in cpp
        assert "q = nullptr;" in cpp

    def test_branch_reseat_byte_identical(self):
        # The witness shape: None-seeded, reseated inside a branch through
        # the pre-declared slot (inference/reassign_none_branch_narrowing).
        src = (
            _F1_RECORDS
            + "def f(k: Int32) -> Int32:\n"
            + "    b: Inner | None = None\n"
            + "    if k > 0:\n        b = Inner(k)\n"
            + "    if b is not None:\n        return b.value\n"
            + "    return 0\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert "b = &*(__slot_1 = Inner(k));" in _cpp(src, thir=True)


class TestUnionPtrSlot:
    def test_member_rvalue_decl_routes(self):
        thir = _lower_ctx(
            _UNION_RECORDS
            + "def f() -> None:\n"
            + "    d: Dog | Cat = Dog(1)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.UNION_RVALUE
        assert decl.val_cpp is not None and "variant" in decl.cpp_type

    def test_member_lvalue_addr_decl_routes(self):
        thir = _lower_ctx(
            _UNION_RECORDS
            + "def f() -> None:\n"
            + "    dog = Dog(1)\n"
            + "    d: Dog | Cat = dog\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[1]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.UNION_ADDR

    def test_rvalue_reseat_through_slot_routes(self):
        thir = _lower_ctx(
            _UNION_RECORDS
            + "def f() -> None:\n"
            + "    d: Dog | Cat = Dog(1)\n"
            + "    d = Cat(2)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        reseat = fn.body[1]
        assert isinstance(reseat, THIRPtrLocalRebind)
        assert reseat.kind is PtrSlotKind.UNION_RVALUE

    def test_byte_identical(self):
        src = (
            _UNION_RECORDS
            + "def f() -> None:\n"
            + "    d: Dog | Cat = Dog(1)\n"
            + "    d = Cat(2)\n"
            + "def g() -> None:\n"
            + "    dog = Dog(3)\n"
            + "    d: Dog | Cat = dog\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        cpp = _cpp(src, thir=True)
        assert "std::optional<std::variant<Cat, Dog>> __slot_2;" in cpp
        assert "std::variant<Cat, Dog> __slot_1 = Dog(1);" in cpp
        assert "std::variant<Cat*, Dog*> d = ::tpy::to_ptr_variant(__slot_1);" in cpp
        assert "__slot_2.emplace(Cat(2));" in cpp
        assert "d = ::tpy::to_ptr_variant(*__slot_2);" in cpp
        assert "d{&(dog)}" in cpp

    def test_whole_union_call_rvalue_routes(self):
        # RE-PINNED ROUTED (thir-wave-next6): the union-returning call
        # expression landed (the call-ret union row), so the UNION_RVALUE
        # decl fills its storage slot from the bare call
        # (`__slot_N = mk();` + `to_ptr_variant` -- corpus:
        # union_own_return).
        src = (_UNION_RECORDS
               + "def mk() -> Own[Dog | Cat]:\n    return Dog(1)\n"
               + "def f() -> None:\n"
               + "    d = mk()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_const_member_source_is_ineligible(self):
        # A const-rooted member lvalue would need the const-pointee variant
        # spelling -- a later rung; the body falls back.
        thir = _lower_ctx(
            _UNION_RECORDS
            + "def f(dog: readonly[Dog]) -> None:\n"
            + "    d: Dog | Cat = dog\n")
        assert _fn(thir, "f") is None


class TestPtrSpanCoerceDispositions:
    """The ptr/readonly/span `_coerce_disposition` rows: address-taking Ptr
    coercions (`&{e}`), the ptr/slice/span identity family, and the
    spanlike -> Span `as_span`/`as_mut_span` wraps."""

    PTR_SRC = (
        "from tpy import Int32, Ptr, readonly, take_ptr\n"
        "class Inner:\n"
        "    value: Int32\n"
        "    def __init__(self, value: Int32):\n        self.value = value\n"
        "def use(p: Ptr[Inner]) -> None:\n    pass\n"
        "def f() -> None:\n"
        "    n = Inner(7)\n"
        "    use(take_ptr(n))\n"
        "def g(p: Ptr[Inner]) -> Ptr[readonly[Inner]]:\n"
        "    return p\n"
    )

    def test_value_to_ptr_and_const_widen_route(self):
        thir = _lower_ctx(self.PTR_SRC)
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is not None

    def test_value_to_ptr_byte_identical(self):
        assert _cpp(self.PTR_SRC, thir=True) == _cpp(self.PTR_SRC, thir=False)
        cpp = _cpp(self.PTR_SRC, thir=True)
        # take_ptr's cpp_template is identity, so the arg's value_to_ptr
        # coercion IS the whole render.
        assert "use(&n);" in cpp

    SPAN_SRC = (
        "from tpy import Int32, Span, readonly\n"
        "def sum_span(values: Span[Int32]) -> Int32:\n"
        "    total: Int32 = 0\n"
        "    for v in values:\n        total += v\n"
        "    return total\n"
        "def take_ro(values: Span[readonly[Int32]]) -> Int32:\n"
        "    return len(values)\n"
        "def f(sp: Span[Int32]) -> None:\n"
        "    lst: list[Int32] = [1, 2, 3]\n"
        "    print(sum_span(lst))\n"
        "    print(take_ro(lst))\n"
        "    print(take_ro(sp))\n"
    )

    def test_spanlike_args_route_byte_identical(self):
        thir = _lower_ctx(self.SPAN_SRC)
        assert _fn(thir, "f") is not None
        assert _cpp(self.SPAN_SRC, thir=True) == _cpp(self.SPAN_SRC, thir=False)
        cpp = _cpp(self.SPAN_SRC, thir=True)
        # Mutable slot takes as_mut_span, readonly slot as_span, and the
        # span -> const-span widening passes bare (C++-implicit).
        assert "sum_span(::tpy::as_mut_span(lst))" in cpp
        assert "take_ro(::tpy::as_span(lst))" in cpp
        assert "take_ro(sp)" in cpp

    def test_array_literal_span_arg_routes(self):
        # A scalar array literal at a `Span[readonly[T]]` slot: the readonly
        # element slot resolves through `_resolved_scalar` (readonly peeled),
        # and the spelled-aggregate coerce render is byte-identical
        # (`::tpy::as_span(std::array<int32_t, 2>{5, 5})`).
        src = ("from tpy import Int32, Span, readonly\n"
               "def take_ro(values: Span[readonly[Int32]]) -> Int32:\n"
               "    return len(values)\n"
               "def f() -> None:\n"
               "    print(take_ro([5, 5]))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestBranchHoistDecls:
    """Branch-first non-value hoists (`if_branch_decls`) -- the
    OPTIONAL_STORAGE / pointer / @dynamic flavors of `_emit_branch_decls`."""

    def test_optional_storage_single_bind_routes(self):
        # One branch binds, the other returns: `std::optional<Inner> p;`
        # predecl, the branch decl assigns PLAIN into the optional.
        src = (_F1_RECORDS
               + "def f(c: bool) -> Int32:\n"
               + "    if c:\n        p = Inner(1)\n"
               + "    else:\n        return 0\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].hoist_decls == (("p", "std::optional<Inner>"),)
        assert fn.body[0].hoist_slots == ()
        assign = fn.body[0].then_body[0]
        assert isinstance(assign, THIRAssign) and assign.target.name == "p"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert "std::optional<Inner> p;" in _cpp(src, thir=True)

    def test_rvalue_reassigned_slot_at_if_head(self):
        # Both branches bind rvalues: `std::optional<Inner> __slot_1;` +
        # `Inner* p;` at the chain head, reseats through the slot.
        src = (_F1_RECORDS
               + "def f(c: bool) -> Int32:\n"
               + "    if c:\n        p = Inner(1)\n"
               + "    else:\n        p = Inner(2)\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].hoist_decls == (("p", "Inner*"),)
        assert fn.body[0].hoist_slots == (("p", "Inner"),)
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "p = &*(__slot_1 = Inner(1));" in cpp

    def test_lvalue_then_rvalue_slot_at_if_head(self):
        # An lvalue-first mixed pair: the later rvalue makes the name
        # rvalue-reassigned, so the slot pre-decls at the if-head and the
        # rvalue reseat rides the rebind-slot THIRAssign arm; the lvalue
        # branch aliases via PTR_ADDR.
        src = (_F1_RECORDS
               + "def f(c: bool) -> Int32:\n"
               + "    base = Inner(9)\n"
               + "    if c:\n        p = base\n"
               + "    else:\n        p = Inner(2)\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[1].hoist_slots == (("p", "Inner"),)
        reseat_alias = fn.body[1].then_body[0]
        assert isinstance(reseat_alias, THIRPtrLocalRebind)
        assert reseat_alias.kind is PtrSlotKind.PTR_ADDR
        assert isinstance(fn.body[1].else_body[0], THIRAssign)
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "p = &(base);" in cpp
        assert "p = &*(__slot_1 = Inner(2));" in cpp

    def test_rvalue_then_lvalue_lazy_slot(self):
        # Rvalue FIRST (the textual decl), lvalue second: the name is NOT
        # rvalue-reassigned, so no if-head slot -- the rvalue branch
        # allocates the function-top slot lazily (BRANCH_RVALUE).
        src = (_F1_RECORDS
               + "def f(c: bool) -> Int32:\n"
               + "    base = Inner(9)\n"
               + "    if c:\n        p = Inner(2)\n"
               + "    else:\n        p = base\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[1].hoist_slots == ()
        reseat_rv = fn.body[1].then_body[0]
        assert isinstance(reseat_rv, THIRPtrLocalRebind)
        assert reseat_rv.kind is PtrSlotKind.BRANCH_RVALUE
        reseat_alias = fn.body[1].else_body[0]
        assert isinstance(reseat_alias, THIRPtrLocalRebind)
        assert reseat_alias.kind is PtrSlotKind.PTR_ADDR
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "p = &*(__slot_1 = Inner(2));" in cpp
        assert "p = &(base);" in cpp

    def test_subscript_elem_reseat_routes(self):
        # `p = items[i]` reseats via the address-of over the checked
        # element read (`p = &(::tpy::__getitem__(items, 0));`).
        src = (_F1_RECORDS
               + "def f(items: list[Inner], c: bool) -> Int32:\n"
               + "    if c:\n        p = items[0]\n"
               + "    else:\n        p = items[1]\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "p = &(::tpy::__getitem__(items, 0));" in cpp

    def test_readonly_hoist_rejects(self):
        # A readonly-sourced hoist is the const-indirect rung -- whole-body
        # fallback (byte-identical either way: fallback emits AST).
        src = (_F1_RECORDS
               + "def f(a: readonly[Inner], b: readonly[Inner], c: bool)"
               + " -> Int32:\n"
               + "    if c:\n        p = a\n"
               + "    else:\n        p = b\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_resumable_nonvalue_hoist_rejects(self):
        # Resumable leaves cannot drain the reseat hoist lines -- the body
        # stays AST (and the AST branch-list-literal flavor is itself a
        # tracked crash, so the record shape pins the guard).
        src = (_F1_RECORDS
               + "from typing import Iterator\n"
               + "def g(c: bool) -> Iterator[Int32]:\n"
               + "    if c:\n        p = Inner(1)\n"
               + "    else:\n        p = Inner(2)\n"
               + "    yield p.value\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "g") is None

    def test_record_name_alias_decl_routes_addr(self):
        # `x = a; ...; x = b` (both plain record locals): the POINTER decl
        # takes the PTR_ADDR address-of over the bare lvalue
        # (`Inner* x = &(a);`) -- no convert node, no validator trap.
        src = (_F1_RECORDS
               + "def f() -> Int32:\n"
               + "    a: Inner = Inner(1)\n"
               + "    b: Inner = Inner(2)\n"
               + "    x = a\n"
               + "    x = b\n"
               + "    return x.value\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_nested_branch_nonvalue_hoist_rejects(self):
        # An INNER-scope if's non-value hoist (any in_branch body)
        # registers into that scope's declared COPY, but the name is
        # function-scoped in Python (the AST tracks it flat) -- a later
        # same-name decl would classify differently on the two paths, so
        # inner-scope non-value hoists stay AST. Byte-identity is exactly
        # the regression guard.
        src = (_F1_RECORDS
               + "def f(a: bool, b: bool) -> Int32:\n"
               + "    r = 0\n"
               + "    if a:\n"
               + "        if b:\n            p = Inner(1)\n"
               + "        else:\n            return 0\n"
               + "        r = p.value\n"
               + "    p = Inner(9)\n"
               + "    p = Inner(10)\n"
               + "    return r + p.value\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_sibling_branch_same_name_hoist_restores(self):
        # Registry restore: branch 1's nested-if hoist registrations
        # (optional_locals / branch_hoisted) must pop with the branch so a
        # same-named decl in the SIBLING branch classifies fresh.
        src = (_F1_RECORDS
               + "def f(a: bool, b: bool) -> Int32:\n"
               + "    if a:\n"
               + "        if b:\n            p = Inner(1)\n"
               + "        else:\n            return 0\n"
               + "        return p.value\n"
               + "    else:\n"
               + "        p = Inner(2)\n"
               + "        return p.value\n")
        thir = _lower_ctx(src)
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_move_through_hoist_rejects(self):
        # A move-through hoisted name takes the AST's plain storage decl (a
        # different arm) -- the classification rejects it.
        src = (_F1_RECORDS
               + "def f(c: bool) -> Int32:\n"
               + "    if c:\n        a = Inner(1)\n"
               + "    else:\n        a = Inner(2)\n"
               + "    b = a\n"
               + "    return b.value\n")
        thir = _lower_ctx(src)
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_dict_hoist_routes(self):
        # The container flavors beyond list: a both-branch dict hoist rides
        # the same slot machinery.
        src = (_F1_RECORDS
               + "def f(c: bool) -> Int32:\n"
               + "    if c:\n        d: dict[Int32, Int32] = {1: 2}\n"
               + "    else:\n        d = {3: 4}\n"
               + "    return len(d)\n")
        fn = _fn(_lower_ctx(src), "f")
        assert fn is not None
        assert fn.body[0].hoist_slots == (
            ("d", "::tpy::ordered_map<int32_t, int32_t>"),)
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_set_single_bind_optional_storage_routes(self):
        # A single-bind set hoist takes the OPTIONAL_STORAGE flavor and the
        # hoisted-container print wrap (`SetPrinter((*s))`).
        src = (_F1_RECORDS
               + "def f(c: bool) -> None:\n"
               + "    if c:\n        s: set[Int32] = {1, 2}\n"
               + "    else:\n        return\n"
               + "    print(s)\n")
        fn = _fn(_lower_ctx(src), "f")
        assert fn is not None
        assert fn.body[0].hoist_decls == (
            ("s", "std::optional<::tpy::ordered_set<int32_t>>"),)
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_async_name_alias_stays_byte_identical(self):
        # The decl.ptr_alias_borrow guard exempts resumable leaves (the no-op
        # rule admits the shape everywhere now); today the name-alias
        # shape falls back upstream anyway, so byte-identity pins the
        # exemption's current (vacuous) reach.
        src = (_F1_RECORDS
               + "import asyncio\n"
               + "async def f() -> Int32:\n"
               + "    a = Inner(1)\n"
               + "    b = Inner(2)\n"
               + "    x = a\n"
               + "    await asyncio.sleep(0)\n"
               + "    x = b\n"
               + "    return x.value\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_f2d_slot_container_print_routes(self):
        # The print wrap's rebind_slot_locals admission also covers a plain
        # F2d slot container (function-scope reassigned, NOT branch-hoisted)
        # -- its own witness, not riding the hoist cases' green.
        src = (_F1_RECORDS
               + "def f() -> None:\n"
               + "    items: list[Int32] = [1, 2]\n"
               + "    items = [3]\n"
               + "    print(items)\n")
        fn = _fn(_lower_ctx(src), "f")
        assert fn is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "::tpy::ListPrinter((*items))" in cpp

    def test_optional_storage_comprehension_source_routes(self):
        # A COMPREHENSION source at the same assign: the whole stmt-expr
        # lands in the engaged optional. Dispatched at the reseat because
        # the generic expression lowering has no comprehension arm --
        # admitting it at the gate alone only moves the reject.
        from .testutil import _assert_routes_byte_identical
        src = ("from tpy import Int32\n"
               "class Node:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.v = v\n"
               "def f(c: bool, src: list[Node]) -> Int32:\n"
               "    if c:\n"
               "        xs = [n.v + 1 for n in src]\n"
               "    else:\n"
               "        return -1\n"
               "    xs.append(9)\n"
               "    return xs[0] + len(xs)\n"
               "def main() -> None:\n"
               "    print(f(True, [Node(1), Node(2)]))\n"
               "main()\n")
        _assert_routes_byte_identical(src)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("reseat.opt_storage_comp", 0) >= 1
        cpp = _cpp(src, thir=True)
        assert "std::optional<std::vector<int32_t>> xs;" in cpp
        assert "xs = ({" in cpp

    def test_optional_storage_comp_shadowed_loop_var_defers(self):
        # BOUNDARY: the comprehension route still owns its own rejects --
        # a loop var shadowing a specially-classified local (here a
        # reassigned pointer local) keeps the whole body on the AST path.
        src = ("from tpy import Int32\n"
               "class Node:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.v = v\n"
               "def f(c: bool, src: list[Node]) -> Int32:\n"
               "    n = src[0]\n"
               "    if c:\n"
               "        n = src[1]\n"
               "    if c:\n"
               "        xs = [n.v + 1 for n in src]\n"
               "    else:\n"
               "        return -1\n"
               "    return xs[0] + len(xs)\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_optional_storage_call_source_routes(self):
        # The OPTIONAL_STORAGE assign's storage-call source flavor
        # (`items = make()` in a single-bind branch) -- reseat.opt_storage
        # over a call, not a literal/ctor.
        src = (_F1_RECORDS
               + "def make() -> Own[list[Int32]]:\n    return [1, 2, 3]\n"
               + "def f(c: bool) -> None:\n"
               + "    if c:\n        items = make()\n"
               + "    else:\n        return\n"
               + "    print(items)\n")
        fn = _fn(_lower_ctx(src), "f")
        assert fn is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "items = make();" in cpp


class TestAliasPtrDerefSource:
    """A REF_ALIAS decl off a PLAIN pointer-local source (`alias = p` where
    `p` is reassigned later): the alias binds through the deref (`Point&
    alias = (*p);`). Optional-declared pointer sources keep the deref_check
    machinery and stay AST."""

    _SRC = ("from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n")

    def test_pointer_source_alias_routes(self):
        src = (self._SRC
               + "def f() -> None:\n"
               + "    p = Point(1)\n"
               + "    alias = p\n"
               + "    alias.x = 5\n"
               + "    print(p.x)\n"
               + "    p = Point(2)\n"
               + "    print(alias.x, p.x)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("decl.alias_ptr_deref_src")
        _assert_byte_identical(src)

    def test_optional_pointer_source_still_defers(self):
        # BOUNDARY: an Optional-declared pointer name carries deref_check
        # nullability -- the alias arm must not admit it.
        src = (self._SRC
               + "def f(flag: bool) -> None:\n"
               + "    q: Point | None = Point(3) if flag else None\n"
               + "    if q is not None:\n"
               + "        r = q\n"
               + "        print(r.x)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("decl.alias_ptr_deref_src")
        _assert_byte_identical(src)


class TestElementBorrowPtrDecl:
    """A container-ELEMENT borrow local that is later reassigned: the decl
    lifts the element lvalue to a reseatable `T*` (`decl.subscript_elem_addr`),
    the twin of the `reseat.subscript_elem` arm every later reseat takes."""

    def test_reassigned_list_elem_decl_routes(self):
        src = (_F1_RECORDS
               + "def f(items: list[Inner]) -> Int32:\n"
               + "    p = items[0]\n"
               + "    p = items[1]\n"
               + "    return p.value\n")
        thir, faces = _lower_ctx_witnessed(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("decl.subscript_elem_addr")
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.PTR_ADDR
        assert not decl.needs_rebind_slot
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "Inner* p = &(::tpy::__getitem__(items, 0));" in cpp
        _assert_byte_identical(src)

    def test_readonly_container_elem_still_defers(self):
        # BOUNDARY: a readonly container's elements are const, and the RESEAT
        # arm keeps its own const rung -- so the body still falls back whole
        # even though the decl's const verdict (`const Inner*`, via
        # `_f1_is_const`) would be right. Widen both in lockstep or not at all.
        src = (_F1_RECORDS
               + "def f(items: readonly[list[Inner]]) -> Int32:\n"
               + "    p = items[0]\n"
               + "    p = items[1]\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_rvalue_reseat_predeclares_rebind_slot(self):
        # A later RVALUE reseat needs its own `std::optional<Inner>` slot so
        # the alias to the init element is not overwritten; the decl reserves
        # it (held back until the reseat draws it).
        src = (_F1_RECORDS
               + "def f(items: list[Inner]) -> Int32:\n"
               + "    p = items[0]\n"
               + "    p = Inner(9)\n"
               + "    return p.value\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.PTR_ADDR and decl.needs_rebind_slot
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "std::optional<Inner> __slot_1;" in cpp
        assert "p = &*(__slot_1 = Inner(9));" in cpp

    def test_reassigned_dict_elem_decl_routes(self):
        # The element family is shared with dict VALUES, so a dict-element
        # source reaches the same arm -- pinned because the family predicate
        # admits it generically, not because a corpus case witnesses it.
        src = (_F1_RECORDS
               + "def f(d: dict[str, Inner]) -> Int32:\n"
               + "    p = d[\"a\"]\n"
               + "    p = d[\"b\"]\n"
               + "    return p.value\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("decl.subscript_elem_addr")
        assert "Inner* p = &(::tpy::__getitem__(d, \"a\"));" in _cpp(src, thir=True)
        _assert_byte_identical(src)

    def test_reassigned_field_recv_elem_decl_routes(self):
        # The receiver resolver admits a one-level FIELD off an in-scope name,
        # so `h.items[i]` reaches the arm too -- same generic-admission reason.
        src = (_F1_RECORDS
               + "class Bag:\n"
               + "    items: list[Inner]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.items = [Inner(1), Inner(2)]\n"
               + "def f(h: Bag) -> Int32:\n"
               + "    p = h.items[0]\n"
               + "    p = h.items[1]\n"
               + "    return p.value\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("decl.subscript_elem_addr")
        assert ("Inner* p = &(::tpy::__getitem__(h.items, 0));"
                in _cpp(src, thir=True))
        _assert_byte_identical(src)

    def test_tparam_elem_decl_and_return_route(self):
        # The open-T twin: `result = a[i]` inside a generic function binds
        # `T* result` and the return derefs it. The element borrow is NOT a
        # move source (the pointee belongs to the caller's list), so the
        # deref returns bare -- the counterweight to the hoisted-optional
        # flavor below, which moves.
        src = ("from tpy import Int32\n"
               + "def last[T](a: list[T]) -> T:\n"
               + "    result: T = a[0]\n"
               + "    for i in range(1, len(a)):\n"
               + "        result = a[i]\n"
               + "    return result\n"
               + "def main() -> None:\n"
               + "    print(last([1, 2, 3]))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "last") is not None
        assert faces.get("decl.subscript_elem_addr")
        assert faces.get("ret.tparam_ptr_local")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (*result);" in hpp + cpp

    def test_nested_container_elem_reassign_still_defers(self):
        # BOUNDARY: an element that is itself a CONTAINER binds the REF_ALIAS
        # `T&` only -- the reseatable pointer form for it is a later rung, so
        # a reassigned nested-element local must keep falling back.
        src = (_F1_RECORDS
               + "def f(m: list[list[Inner]]) -> Int32:\n"
               + "    row = m[0]\n"
               + "    row = m[1]\n"
               + "    return Int32(len(row))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is None
        assert not faces.get("decl.subscript_elem_addr")
        _assert_byte_identical(src)

    def test_slice_source_still_defers(self):
        # BOUNDARY: a SLICE is not an element borrow (it materializes a new
        # container) -- the shape must not reach the address-of decl.
        src = (_F1_RECORDS
               + "def f(items: list[Inner]) -> Int32:\n"
               + "    part = items[0:2]\n"
               + "    part = items[1:2]\n"
               + "    return Int32(len(part))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("decl.subscript_elem_addr")
        _assert_byte_identical(src)


class TestTparamHoistedOptionalReturn:
    """A `with`-hoisted `std::optional<T>` in a generic body: the open-T call
    rvalue reseats the hoist plainly (neither side has a form until the
    template instantiates, so the traits pick the same one for both), and the
    return derefs and MOVES at a movable last use -- the same question the
    record pointer-local flavors ask, since one `is_indirect_name` branch
    serves every pointer-local return on the oracle side.

    The stdlib witness is `tplib.channel`'s `Receiver.recv`."""

    _HEAD = (
        "from tpy import Int32, Own\n"
        "class Guard:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def __enter__(self) -> Int32:\n"
        "        return self.n\n"
        "    def __exit__(self, et, ev, tb) -> None:\n"
        "        pass\n"
        "class Bag[T]:\n"
        "    xs: list[T]\n"
        "    def __init__(self, xs: Own[list[T]]) -> None:\n"
        "        self.xs = xs\n"
        "    def pop_one(self) -> Own[T]:\n"
        "        return self.xs.pop()\n"
    )
    _TAIL = (
        "def main() -> None:\n"
        "    b = Bag([1, 2, 3])\n"
        "    print(b.take(1))\n"
        "main()\n"
    )
    SRC = _HEAD + (
        "    def take(self, n: Int32) -> Own[T]:\n"
        "        with Guard(n) as g:\n"
        "            value = self.pop_one()\n"
        "        return value\n"
    ) + _TAIL

    def test_open_t_call_reseats_the_hoist_and_returns_moved(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("reseat.opt_storage")
        assert faces.get("ret.tparam_ptr_local")
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        out = hpp + cpp
        assert "std::optional<T> value;" in out
        assert "value = this->pop_one();" in out
        assert "return std::move((*value));" in out

    def test_non_call_rvalue_at_the_same_hoist_defers(self):
        # BOUNDARY: the reseat admits a CALL rvalue whose open-T result is the
        # slot's own type param; a ternary at the same slot is a different
        # source shape with no vetted render, so it keeps rejecting.
        src = self._HEAD + (
            "    def take(self, n: Int32) -> Own[T]:\n"
            "        with Guard(n) as g:\n"
            "            value = self.pop_one() if n > 0 else self.pop_one()\n"
            "        return value\n"
        ) + self._TAIL
        _ctx, fb = _thir_ctx(src)
        assert fb == {"body:stmt.var_decl:reseat.opt_storage_source": 1}


class TestOptNameCopyDecl:
    """Same-repr pointer-Optional name-copy decl (`const Point* q = a;`)."""

    _SRC = ("from tpy import Int32, Own\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n")

    def test_opt_name_copy_routes(self):
        src = (self._SRC
               + "def f(a: Point | None) -> Int32:\n"
               + "    q: Point | None = a\n"
               + "    if q is not None:\n"
               + "        return q.x\n"
               + "    return 0\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("decl.opt_name_copy")
        _assert_byte_identical(src)

    def test_reassigned_target_takes_the_same_bare_copy(self):
        # A name-REASSIGNED target binds the same bare pointer copy: the
        # AST's direct-pointer-copy arm returns before the rebind-slot
        # predecl, so neither flavor allocates a slot and the reseats ride
        # the slotless pointer arms.
        src = (self._SRC
               + "def f(a: Point | None, b: Point | None) -> Int32:\n"
               + "    q: Point | None = a\n"
               + "    q = b\n"
               + "    if q is not None:\n"
               + "        return q.x\n"
               + "    return 0\n"
               + "def main():\n    print(f(None, None))\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("decl.opt_name_copy")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "Point* q = a;" in out
        assert "q = b;" in out
        assert "__slot_" not in out

    def test_narrowed_inner_target_stays_out(self):
        # BOUNDARY: a plain-T target off a narrowed Optional name reads the
        # inner ((*a) / bare) -- a DIFFERENT target type, not this row.
        src = (self._SRC
               + "def f(a: Point | None) -> Int32:\n"
               + "    if a is not None:\n"
               + "        p = a\n"
               + "        return p.x\n"
               + "    return 0\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("decl.opt_name_copy")
        _assert_byte_identical(src)


class TestOptReassignedFieldDecl:
    """Reassigned pointer-Optional local off an lvalue FIELD init: the same
    OPTIONAL_TO_PTR lift as the single-assignment shape (`Point* p =
    optional_to_ptr(h.value);`), reseats via the slotless pointer arms --
    the field re-lift (reseat.opt_field_lift), nullptr, and the inline
    rvalue slot. Subscript-optional sources stay out."""

    _SRC = ("from tpy import Int32, Own, copy\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "class Holder:\n"
            "    value: Point | None\n"
            "    def __init__(self):\n        self.value = None\n")

    def test_field_decl_and_reseats_route(self):
        src = (self._SRC
               + "def test(h: Holder):\n"
               + "    p: Point | None = h.value\n"
               + "    print(p is None)\n"
               + "    h.value = copy(Point(1))\n"
               + "    p = h.value\n"
               + "    print(p is None)\n"
               + "    p = None\n"
               + "    print(p is None)\n"
               + "test(Holder())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "test") is not None
        assert faces.get("reseat.opt_field_lift")
        _assert_byte_identical(src)

    def test_inline_rvalue_then_field_lift_no_hijack(self):
        # REGRESSION: the INLINE_RVALUE reseat's plain block slot must NOT
        # register in the THIRAssign rebind-slot registry -- the later field
        # lift is a plain `p = optional_to_ptr(h.value);`, not the hijacked
        # `p = &*(__slot_1 = ...)` render.
        src = (self._SRC
               + "def test(h: Holder):\n"
               + "    p: Point | None = h.value\n"
               + "    print(p is None)\n"
               + "    p = Point(9)\n"
               + "    if p is not None:\n"
               + "        print(p.x)\n"
               + "    h.value = copy(Point(3))\n"
               + "    p = h.value\n"
               + "    if p is not None:\n"
               + "        print(p.x)\n"
               + "test(Holder())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "test") is not None
        assert faces.get("reseat.opt_inline_rvalue")
        assert faces.get("reseat.opt_field_lift")
        _assert_byte_identical(src)

    def test_inline_rvalue_then_ptr_copy_no_hijack(self):
        # The pointer-copy sibling of the hijack regression: `p = q` after
        # the inline-rvalue reseat stays the bare pointer copy.
        src = (self._SRC
               + "def test(h: Holder):\n"
               + "    q: Point | None = h.value\n"
               + "    p: Point | None = h.value\n"
               + "    p = Point(9)\n"
               + "    if p is not None:\n"
               + "        print(p.x)\n"
               + "    p = q\n"
               + "    print(p is None)\n"
               + "h = Holder()\n"
               + "h.value = copy(Point(3))\n"
               + "test(h)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "test") is not None
        assert faces.get("reseat.opt_ptr_copy")
        _assert_byte_identical(src)

    def test_nested_def_inline_slot_isolated(self):
        # REGRESSION: nested-def emission must snapshot/restore
        # inline_rvalue_slots like its rebind_slots sibling. Without it, a
        # lambda-local inline block slot leaks to a same-named outer local,
        # whose rvalue reseat then references the lambda's out-of-scope
        # __slot_N instead of declaring its own.
        src = (self._SRC
               + "def outer(h: Holder):\n"
               + "    def helper():\n"
               + "        p: Point | None = h.value\n"
               + "        p = Point(9)\n"
               + "        print(p is None)\n"
               + "    helper()\n"
               + "    p: Point | None = h.value\n"
               + "    p = Point(20)\n"
               + "    print(p is None)\n"
               + "h = Holder()\n"
               + "h.value = copy(Point(1))\n"
               + "outer(h)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "outer") is not None
        _assert_byte_identical(src)

    def test_branch_field_reseat_byte_identical(self):
        # A branch reseat of the reassigned optional local renders the same
        # plain lift at branch indent (no slot, no hoist).
        src = (self._SRC
               + "def f(b: Holder, c: Holder, which: Int32) -> Int32:\n"
               + "    p = b.value\n"
               + "    if which < 0:\n"
               + "        p = c.value\n"
               + "    if p is not None:\n"
               + "        return p.x\n"
               + "    return 0\n"
               + "c = Holder()\n"
               + "c.value = copy(Point(5))\n"
               + "print(f(Holder(), c, -1))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("reseat.opt_field_lift")
        _assert_byte_identical(src)

    def test_subscript_optional_source_stays_out(self):
        # BOUNDARY: a subscript-optional source keeps the field-receiver
        # pin at the decl -- the body stays AST.
        src = (self._SRC
               + "from typing import Optional\n"
               + "def test(items: list[Optional[Point]]):\n"
               + "    p = items[0]\n"
               + "    print(p is None)\n"
               + "    p = items[1]\n"
               + "    print(p is None)\n"
               + "test([None, None])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "test") is None
        assert not faces.get("reseat.opt_field_lift")
        _assert_byte_identical(src)


class TestCopyReseatRows:
    """`copy(x)` as a reseat rvalue -- the copy-construct rides the rebind
    machinery (`saved = &*(__slot_N = Point(p));` / the engaging optional
    assign), the sinks intercepting the row like the decl sink (the
    special-builtin gate rejects copy() in the generic call tail). Corpus
    witnesses: pointers/escape_{explicit_storage,local_safe}."""

    _P = ("from tpy import Int32, copy\n"
          "class Point:\n"
          "    x: Int32\n"
          "    def __init__(self, x: Int32) -> None:\n"
          "        self.x = x\n")

    def test_branch_hoisted_copy_reseat(self):
        src = (self._P
               + "def f(flag: bool) -> Int32:\n"
               + "    saved = Point(-1)\n"
               + "    for i in range(3):\n"
               + "        p = Point(i)\n"
               + "        if flag:\n"
               + "            saved = copy(p)\n"
               + "            p.x = 99\n"
               + "    return saved.x\n"
               + "def main() -> None:\n"
               + "    print(f(True))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "= Point(p));" in cpp
        _assert_byte_identical(src)

    def test_opt_slot_copy_reseat(self):
        src = (self._P
               + "def f(flag: bool) -> Int32:\n"
               + "    saved: Point | None = None\n"
               + "    for i in range(3):\n"
               + "        p = Point(i)\n"
               + "        if saved is None and flag:\n"
               + "            saved = copy(p)\n"
               + "    if saved is not None:\n"
               + "        return saved.x\n"
               + "    return -1\n"
               + "def main() -> None:\n"
               + "    print(f(True))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("reseat.opt_rvalue")
        _assert_byte_identical(src)

    def test_branch_rvalue_copy_reseat(self):
        # The genuinely BRANCH-HOISTED flavor (first decl inside one
        # if-branch, rvalue-only): the lazy-slot reseat
        # (`p = &*(__slot_N = Point(base));`) with the copy row.
        src = (self._P
               + "def f(c: bool, base: Point) -> Int32:\n"
               + "    if c:\n"
               + "        p = copy(base)\n"
               + "    else:\n"
               + "        p = base\n"
               + "    return p.x\n"
               + "def main() -> None:\n"
               + "    print(f(True, Point(3)))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("reseat.branch_rvalue")
        _assert_byte_identical(src)

    def test_opt_storage_copy_reseat(self):
        # The OPTIONAL_STORAGE single-bind flavor: the engaging assign
        # (`saved = Point(p);`) with the copy row.
        src = (self._P
               + "def f(c: bool, p: Point) -> Int32:\n"
               + "    if c:\n"
               + "        saved = copy(p)\n"
               + "    else:\n"
               + "        return -1\n"
               + "    return saved.x\n"
               + "def main() -> None:\n"
               + "    print(f(True, Point(4)))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("reseat.opt_storage")
        _assert_byte_identical(src)

    def test_subscript_bound_source_copy_reseat_routes(self):
        # A subscript-bound (non-reassigned) record source is NOT a pointer
        # local -- the copy renders bare (`Point(best)`) inside the lazy
        # slot reseat. (The genuine pointer-source boundary is pinned on
        # the free-call side, where the source IS reassigned:
        # TestCopyOwnArgFreeCall.test_pointer_source_copy_arg_defers.)
        src = (self._P
               + "def f(items: list[Point], flag: bool) -> Int32:\n"
               + "    best = items[0]\n"
               + "    saved = Point(-1)\n"
               + "    for it in items:\n"
               + "        if flag:\n"
               + "            saved = copy(best)\n"
               + "    return saved.x\n"
               + "def main() -> None:\n"
               + "    print(f([Point(1)], True))\n"
               + "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "= Point(best));" in cpp
        _assert_byte_identical(src)

    def test_subtype_copy_at_opt_slot_defers(self):
        # BOUNDARY (dualgen-probed): copy() of a SUBTYPE at the optional
        # slot fails the exact-type check and falls back byte-identically.
        src = (self._P
               + "class Sub(Point):\n"
               + "    def __init__(self, x: Int32) -> None:\n"
               + "        super().__init__(x)\n"
               + "def f(flag: bool) -> Int32:\n"
               + "    saved: Point | None = None\n"
               + "    for i in range(2):\n"
               + "        b = Sub(i)\n"
               + "        if flag:\n"
               + "            saved = copy(b)\n"
               + "    if saved is not None:\n"
               + "        return saved.x\n"
               + "    return -1\n"
               + "def main() -> None:\n"
               + "    print(f(True))\n"
               + "main()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestOptSlotStorageReseatSource:
    """The `reseat.opt_rvalue` source lowers at STORAGE (the slot IS the
    storage sink, `&*(__slot_N = <rvalue>)`) like its `opt_storage_field`
    sibling -- a record-returning marker call rejects at the default VALUE
    use. Corpus witness: argparse/custom_type's `Tag.from_arg(tok)`."""

    _T = ("from tpy import Int32, Own\n"
          "class Tag:\n"
          "    n: Int32\n"
          "    def __init__(self, n: Int32) -> None:\n"
          "        self.n = n\n"
          "    @staticmethod\n"
          "    def from_arg(n: Int32) -> Own[Tag]:\n"
          "        return Tag(n)\n")

    _SRC = (_T
            + "def pick(flag: bool, n: Int32) -> Int32:\n"
            + "    t: Tag | None = None\n"
            + "    if flag:\n"
            + "        t = Tag.from_arg(n)\n"
            + "    else:\n"
            + "        t = Tag(n + 1)\n"
            + "    if t is None:\n        return 0\n"
            + "    return t.n\n"
            + "def main() -> None:\n"
            + "    print(pick(True, 3))\n"
            + "main()\n")

    def test_own_static_call_reseat_routes(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "pick") is not None
        assert faces.get("reseat.opt_rvalue")
        out = "".join(_assert_routes_byte_identical(self._SRC))
        assert "= Tag::from_arg(n));" in out

    def test_none_reseat_beside_it_still_routes(self):
        # BLAST-SWEEP neighbour: the None reseat of the same slot shares
        # the arm's predecl but takes the OPT_NONE rebind, unaffected by
        # the source-use change.
        src = (self._T
               + "def clear(flag: bool, n: Int32) -> Int32:\n"
               + "    t: Tag | None = Tag(n)\n"
               + "    if flag:\n        t = None\n"
               + "    if t is None:\n        return -1\n"
               + "    return t.n\n"
               + "def main() -> None:\n"
               + "    print(clear(True, 5))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "clear") is not None
        assert faces.get("reseat.opt_none")
        _assert_routes_byte_identical(src)


class TestCopyPointerLocalRecord:
    """`copy(p)` over a POINTER-LOCAL record source: the general
    `{arg_type}(gen_expr_deref(arg))` tail spells the deref
    (`Point((*saved))`), which `_lower_copy_record`'s bare `T(x)` row
    excludes by construction. Corpus witness: argparse/custom_type's
    `Tag input = Tag((*__tpy_argparse_acc_input));`."""

    _P = ("import tpy\n"
          "from tpy import Int32\n"
          "class Point:\n"
          "    x: Int32\n"
          "    def __init__(self, x: Int32) -> None:\n"
          "        self.x = x\n")

    _HOISTED = (_P
                + "def hoisted_copy() -> Int32:\n"
                + "    saved: Point = Point(0)\n"
                + "    for i in range(3):\n"
                + "        p: Point = Point(i)\n"
                + "        saved = p\n"
                + "    dup = tpy.copy(saved)\n"
                + "    return dup.x\n"
                + "def main() -> None:\n"
                + "    print(hoisted_copy())\n"
                + "main()\n")

    def test_pointer_local_record_copy_routes(self):
        thir, faces = _lower_ctx_witnessed(self._HOISTED)
        assert _fn(thir, "hoisted_copy") is not None
        assert faces.get("call.copy_record_ptr")
        out = "".join(_assert_routes_byte_identical(self._HOISTED))
        assert "Point dup = Point((*saved));" in out

    def test_plain_record_name_copy_at_the_qualified_tail_defers(self):
        # BOUNDARY (dualgen-probed): a NON-pointer record NAME reaches the
        # same qualified tail with no deref, and the row is deliberately
        # keyed on the pointer-local flavor -- `_lower_copy_record`'s
        # `T(x)` row owns that shape at the sinks that intercept it, and
        # this decl sink is not one of them.
        src = (self._P
               + "def plain_copy(src: Point) -> Int32:\n"
               + "    dup = tpy.copy(src)\n"
               + "    return dup.x\n"
               + "def main() -> None:\n"
               + "    print(plain_copy(Point(5)))\n"
               + "main()\n")
        assert _fn(_lower_ctx(src), "plain_copy") is None
        assert "Point dup = Point(src);" in "".join(
            _assert_byte_identical(src))


class TestPtrValueHoistAndTernary:
    """`Ptr[T]` as a first-class T* VALUE at the if-hoist predecl and the
    ternary result -- never the pointer-local deref machinery."""

    _P = (
        "from tpy import Int32, Ptr, readonly\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "g: Point = Point(1)\n"
        "h: Point = Point(2)\n"
        "def addr_g() -> Ptr[Point]:\n"
        "    return g\n"
        "def addr_h() -> Ptr[Point]:\n"
        "    return h\n"
    )

    def test_ptr_branch_hoist_routes_plain_predecl(self):
        # The branch-first Ptr decl hoists as the plain-value tail
        # (`Point* p;`), branch assigns render bare -- no pointer-local
        # registration, no deref on reads.
        src = (self._P
               + "def f(cond: bool) -> Ptr[Point]:\n"
               + "    if cond:\n"
               + "        p: Ptr[Point] = addr_g()\n"
               + "    else:\n"
               + "        p = addr_h()\n"
               + "    return p\n"
               + "def main() -> None:\n"
               + "    print(f(True).x)\n"
               + "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("if.hoist_decl", 0) >= 1
        cpp = _assert_byte_identical(src)
        assert "Point* p;\n    if (cond)" in cpp[1]

    def test_ptr_ternary_call_arms_route_bare(self):
        # A Ptr[T]-result ternary renders each call arm bare -- the
        # scalar-style value render.
        src = (self._P
               + "def f(cond: bool) -> Ptr[Point]:\n"
               + "    p: Ptr[Point] = addr_g() if cond else addr_h()\n"
               + "    return p\n"
               + "def main() -> None:\n"
               + "    print(f(False).x)\n"
               + "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "Point* p = ((cond) ? (addr_g()) : (addr_h()));" in cpp[1]

    def test_readonly_ptr_hoist_routes_const_pointee(self):
        # `Ptr[readonly[T]]` rides the same value row (`const T*` spelling
        # comes from the one PtrType render on both paths).
        src = (self._P
               + "def f(cond: bool) -> Ptr[readonly[Point]]:\n"
               + "    if cond:\n"
               + "        p: Ptr[readonly[Point]] = addr_g()\n"
               + "    else:\n"
               + "        p = addr_h()\n"
               + "    return p\n"
               + "def main() -> None:\n"
               + "    print(f(True).x)\n"
               + "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "const Point* p;\n    if (cond)" in cpp[1]

    def test_mixed_own_tuple_hoist_takes_its_own_row(self):
        # The adjacent mixed owned+borrow-element TUPLE hoist
        # (`tuple[Own[Box], Box]`) is NOT the Ptr value row: it predecls the
        # hybrid `std::tuple<Box, Box*> t;` and each branch plain-assigns
        # the call result (the borrow-tuple hoist's mixed_call row).
        src = ("from tpy import Int32, Own\n"
               "class Box:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def make(b: Box) -> tuple[Own[Box], Box]:\n"
               "    return (Box(1), b)\n"
               "def f(b: Box, c: Box, cond: bool) -> Int32:\n"
               "    if cond:\n"
               "        t = make(b)\n"
               "    else:\n"
               "        t = make(c)\n"
               "    t[1].n = 33\n"
               "    return t[0].n\n"
               "def main() -> None:\n"
               "    print(f(Box(1), Box(2), True))\n"
               "main()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "std::tuple<Box, Box*> t;" in out
        assert "t = make(b);" in out
        # The predecl keeps the per-element arrow the decl arm would set.
        assert "std::get<1>(t)->n = 33;" in out
        assert "return std::get<0>(t).n;" in out


class TestNarrowedFieldPtrOptWiden:
    """A sema-NARROWED storage-Optional FIELD widened back to a ptr-opt
    slot keys the DECLARED field type: the arg and return positions take
    the same `::tpy::optional_to_ptr(t.o)` lift over the RAW member read
    (never `&(field)`, never the narrowed `(*t.o)` unwrap)."""

    _POD = (
        "class Pod:\n"
        "    x: int\n"
        "    def __init__(self) -> None:\n"
        "        self.x = 0\n"
        "class T:\n"
        "    o: Pod | None\n"
        "    def __init__(self) -> None:\n"
        "        self.o = None\n"
    )

    def test_narrowed_field_arg_and_return_lift(self):
        src = self._POD + (
            "def is_def(o: Pod | None) -> bool:\n"
            "    return o is not None\n"
            "def take(t: T) -> Pod | None:\n"
            "    if t.o is None:\n"
            "        return None\n"
            "    return t.o\n"
            "def main() -> None:\n"
            "    t = T()\n"
            "    t.o = Pod()\n"
            "    assert t.o is not None\n"
            "    print(is_def(t.o))\n"
            "    p = take(t)\n"
            "    print(p is not None)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return ::tpy::optional_to_ptr(t.o);" in cpp
        assert "is_def(::tpy::optional_to_ptr(t.o))" in cpp
        assert "optional_to_ptr((*" not in cpp
        _, w = _lower_ctx_witnessed(src)
        assert w.get("ret.ptr_opt_field_narrowed", 0) >= 1

    def test_pointee_typed_field_takes_the_addr_of_instead(self):
        # BOUNDARY: the neighbouring arm. A field DECLARED as the pointee
        # (never Optional) stores a plain `Pod` member, so the ptr-opt
        # return is the address-of lift -- the optional_to_ptr row must not
        # claim it (that would read a `std::optional` member that is not
        # there).
        src = (
            "class Pod:\n"
            "    x: int\n"
            "    def __init__(self) -> None:\n"
            "        self.x = 0\n"
            "class T2:\n"
            "    o: Pod\n"
            "    def __init__(self) -> None:\n"
            "        self.o = Pod()\n"
            "def take(t: T2) -> Pod | None:\n"
            "    return t.o\n"
            "def main() -> None:\n"
            "    t = T2()\n"
            "    p = take(t)\n"
            "    print(p is not None)\n"
            "main()\n"
        )
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return &(t.o);" in cpp
        assert "optional_to_ptr" not in cpp
        _, w = _lower_ctx_witnessed(src)
        assert w.get("ret.ptr_opt_field_addr", 0) >= 1
        assert not w.get("ret.ptr_opt_field_narrowed")
        assert not w.get("ret.ptr_opt_field")


class TestPtrOptionalCollapse:
    """`Ptr[T]` IS `T*`, the same C++ shape as a ptr-repr `T | None` --
    the collapse rows: a Ptr name passes bare into a ptr-opt slot (incl.
    the readonly-inner widening), None renders `nullptr` at a collapsed
    Ptr slot, a storage-Optional field lifts via optional_to_ptr at a Ptr
    slot, a Ptr local returns bare at a ptr-opt return (never `&(p)`),
    and a Ptr element passes bare into a ptr-opt tuple-literal slot."""

    _PRE = (
        "from tpy import Ptr, readonly\n"
        "class Node:\n"
        "    val: int\n"
        "    def __init__(self, v: int) -> None:\n"
        "        self.val = v\n"
    )

    def test_ptr_name_into_ptr_opt_slot_passes_bare(self):
        src = self._PRE + (
            "def consume(n: Node | None) -> int:\n"
            "    if n is not None:\n"
            "        return n.val\n"
            "    return -1\n"
            "def consume_ro(n: readonly[Node] | None) -> int:\n"
            "    if n is not None:\n"
            "        return n.val\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    items: list[Node] = [Node(1)]\n"
            "    p: Ptr[Node] = items[0]\n"
            "    print(consume(p))\n"
            "    print(consume_ro(p))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "consume(p)" in cpp
        assert "consume_ro(p)" in cpp
        assert "&(p)" not in cpp

    def test_none_and_field_lift_at_ptr_slot(self):
        src = self._PRE + (
            "class Holder:\n"
            "    opt: Node | None\n"
            "    def __init__(self, v: Node) -> None:\n"
            "        self.opt = v\n"
            "def take_ptr_node(p: Ptr[Node]) -> int:\n"
            "    if p is not None:\n"
            "        return p.val\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    h = Holder(Node(2))\n"
            "    print(take_ptr_node(h.opt))\n"
            "    print(take_ptr_node(None))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "take_ptr_node(::tpy::optional_to_ptr(h.opt))" in cpp
        assert "take_ptr_node(nullptr)" in cpp
        _, w = _lower_ctx_witnessed(src)
        assert w.get("optptr.ptr_slot_lift", 0) >= 1
        assert w.get("arg.ptr_none", 0) >= 1

    def test_ptr_local_return_and_tuple_elem_pass_bare(self):
        src = self._PRE + (
            "def find(items: list[Node], target: int) -> Node | None:\n"
            "    for it in items:\n"
            "        if it.val == target:\n"
            "            p: Ptr[Node] = it\n"
            "            return p\n"
            "    return None\n"
            "def first_pair(items: list[Node]) -> tuple[Node | None, int]:\n"
            "    if len(items) > 0:\n"
            "        p: Ptr[Node] = items[0]\n"
            "        return (p, items[0].val)\n"
            "    return (None, 0)\n"
            "def main() -> None:\n"
            "    items: list[Node] = [Node(1), Node(2)]\n"
            "    pair = first_pair(items)\n"
            "    print(pair[1])\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "            return p;" in cpp or "        return p;" in cpp
        assert "&(p)" not in cpp
        assert "{p, ::tpy::__getitem__(items, 0).val}" in cpp
        _, w = _lower_ctx_witnessed(src)
        assert w.get("ret.ptr_opt_ptr_name", 0) >= 1
        assert w.get("btuple.elem_optptr", 0) >= 1

    def test_ptr_opt_call_result_lands_bare_at_ptr_and_whole_sinks(self):
        # `Ptr[T]` IS `T*`, so a BORROW-returning ptr-repr Optional result
        # binds a Ptr local bare and tests `== nullptr` bare -- the two
        # `_call_use_supported` rows the collapse needs.
        src = self._PRE + (
            "def first(items: list[Node]) -> Node | None:\n"
            "    if len(items) == 0:\n"
            "        return None\n"
            "    return items[0]\n"
            "def ptr_local(items: list[Node]) -> int:\n"
            "    p: Ptr[Node] = first(items)\n"
            "    if p is not None:\n"
            "        return p.val\n"
            "    return -1\n"
            "def none_test(items: list[Node]) -> bool:\n"
            "    return first(items) is None\n"
            "def main() -> None:\n"
            "    items: list[Node] = [Node(1)]\n"
            "    print(ptr_local(items), none_test(items))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "Node* p = first(items);" in out
        assert "return (first(items) == nullptr);" in out
        _, w = _lower_ctx_witnessed(src)
        assert w.get("call.ptr_opt_whole", 0) >= 1

    def test_optional_local_slot_keeps_its_own_row(self):
        # BOUNDARY: an `Optional`-TYPED local is the OPT_PTR_SLOT binding,
        # not the collapsed Ptr slot -- it keeps the passthrough decl row
        # (same render here, but a different arm, and only that arm carries
        # the slot machinery a non-borrow callee would need).
        src = self._PRE + (
            "def first(items: list[Node]) -> Node | None:\n"
            "    if len(items) == 0:\n"
            "        return None\n"
            "    return items[0]\n"
            "def probe(items: list[Node]) -> int:\n"
            "    x: Node | None = first(items)\n"
            "    if x is not None:\n"
            "        return x.val\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(probe([Node(1)]))\n"
            "main()\n"
        )
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "Node* x = first(items);" in cpp
        _, w = _lower_ctx_witnessed(src)
        assert w.get("decl.opt_call_passthrough", 0) >= 1
        assert w.get("call.ptr_opt_whole", 0) == 0


class TestGenericOptionalPtrSlot:
    _SRC = (
        "class Pod:\n"
        "    x: int\n"
        "    def __init__(self) -> None:\n        self.x = 0\n"
        "class T:\n"
        "    o: Pod | None\n"
        "    def __init__(self) -> None:\n        self.o = None\n"
        "def is_def_gen[U](o: U | None) -> bool:\n"
        "    return o is not None\n"
        "def main() -> None:\n"
        "    t = T()\n"
        "    print(is_def_gen(t.o))\n"
        "    t.o = Pod()\n"
        "    print(is_def_gen(t.o))\n"
        "main()\n"
    )

    def test_storage_opt_field_lifts_at_a_substituted_slot(self):
        # A `U | None` slot substituted to `Pod | None` takes the SAME
        # `optional_to_ptr` field lift the concrete ladder gives -- the
        # generic arg gate delegates to the concrete optional-ptr faces
        # once the slot resolves.
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        assert "is_def_gen<Pod>(::tpy::optional_to_ptr(t.o))" in out
        assert "&(t.o)" not in out

    def test_pointee_typed_field_at_the_generic_slot_keeps_rejecting(self):
        # BOUNDARY: the neighbouring source shape. A field DECLARED as the
        # pointee needs the ADDRESS-OF lift at the substituted `U | None`
        # slot (`is_def_gen<Pod>(&(t.o))`), which the generic arg gate does
        # not admit -- only the storage-optional field's optional_to_ptr
        # does. Identity is the claim here: the body falls back.
        src = (
            "class Pod:\n"
            "    x: int\n"
            "    def __init__(self) -> None:\n        self.x = 0\n"
            "class T2:\n"
            "    o: Pod\n"
            "    def __init__(self) -> None:\n        self.o = Pod()\n"
            "def is_def_gen[U](o: U | None) -> bool:\n"
            "    return o is not None\n"
            "def main() -> None:\n"
            "    t = T2()\n"
            "    print(is_def_gen(t.o))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        assert fell == {"body:stmt.expr_stmt:call.generic_arg_shape": 1}, fell
        assert "is_def_gen<Pod>(&(t.o))" in "".join(
            _assert_byte_identical(src))
