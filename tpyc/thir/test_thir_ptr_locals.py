"""Slot-hoist pointer-repr locals (OPT_PTR_SLOT + the union slot kinds).

Routes/ineligible pairs for the gate + targeted byte-identical tests (the
byte-diff in miniature) for each emit arm: the None init (`T* x = nullptr;`
with the rebind-slot pre-decl), the rvalue init slot (`T __slot_N = ...;`),
the None / rvalue reseats, and the ptr-variant union rvalue / address kinds.
"""

from .testutil import _compile, _entry, _lower_ctx, _fn, _F1_RECORDS
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

    def test_lvalue_reseat_is_ineligible(self):
        # Reseating from a field lvalue is a later rung; the body falls back.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> bool:\n"
            + "    p: Inner | None = None\n"
            + "    p = b.inner\n"
            + "    return p is None\n")
        assert _fn(thir, "f") is None

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
