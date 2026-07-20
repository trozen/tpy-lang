"""Slot-hoist pointer-repr locals (OPT_PTR_SLOT + the union slot kinds).

Routes/ineligible pairs for the gate + targeted byte-identical tests (the
byte-diff in miniature) for each emit arm: the None init (`T* x = nullptr;`
with the rebind-slot pre-decl), the rvalue init slot (`T __slot_N = ...;`),
the None / rvalue reseats, and the ptr-variant union rvalue / address kinds.
"""

from .testutil import (_compile, _entry, _lower_ctx, _fn, _F1_RECORDS,
                       _assert_byte_identical)
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

    def test_own_optional_call_init_is_ineligible(self):
        # An `Own[Inner | None]`-returning call init needs the
        # `optional_to_ptr` slot-lift arm (a later rung) -- the whole body
        # stays AST (the classifier says OPT_PTR_SLOT, the source gate's
        # type-equality check rejects: the expr type is the Optional, never
        # the inner).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def make() -> Own[Inner | None]:\n    return Inner(1)\n"
            + "def f() -> bool:\n"
            + "    p: Inner | None = make()\n"
            + "    p = None\n"
            + "    return p is None\n")
        assert _fn(thir, "f") is None

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

    def test_whole_union_call_rvalue_gate(self):
        # An `Own[Dog | Cat]`-returning call classifies UNION_RVALUE at the
        # decl; the union-returning call EXPRESSION is a later call-track
        # rung, so the body still falls back -- pin the fallback (not a
        # divergence) so the interlock is explicit.
        thir = _lower_ctx(
            _UNION_RECORDS
            + "def mk() -> Own[Dog | Cat]:\n    return Dog(1)\n"
            + "def f() -> None:\n"
            + "    d = mk()\n")
        assert _fn(thir, "f") is None

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

    def test_array_literal_span_arg_rejects(self):
        # The make_array-prefixed literal render stays AST (wrap is None).
        thir = _lower_ctx(
            "from tpy import Int32, Span, readonly\n"
            "def take_ro(values: Span[readonly[Int32]]) -> Int32:\n"
            "    return len(values)\n"
            "def f() -> None:\n"
            "    print(take_ro([5, 5]))\n")
        assert _fn(thir, "f") is None


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

    def test_record_name_alias_decl_rejects(self):
        # `x = a; ...; x = b` (both plain record locals): the POINTER decl's
        # bare-name init is the same-type BORROW read whose convert is the
        # no-op node the validator hard-rejects, so the decl itself rejects
        # (decl.ptr_alias_borrow). Whole body stays AST, byte-identical.
        src = (_F1_RECORDS
               + "def f() -> Int32:\n"
               + "    a: Inner = Inner(1)\n"
               + "    b: Inner = Inner(2)\n"
               + "    x = a\n"
               + "    x = b\n"
               + "    return x.value\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
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
        # The decl.ptr_alias_borrow guard exempts resumable leaves (they
        # never run whole-function validation); today the async name-alias
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
