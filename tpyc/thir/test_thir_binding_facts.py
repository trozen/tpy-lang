"""Binding-fact fences: shapes codegen classifies through a `*_locals` binding
SET that `_LowerCtx` does not mirror.

Every render keyed on such a set is a divergence risk when THIR re-derives the
fact from the name's declared TYPE instead -- a ptr-variant-typed union bound as
a value variant, a pointer-repr `Optional` bound as storage, an owned tuple param
bound in storage form. Each case below is fenced today, but by a gate that exists
for another reason; these pin the reject so a widening of that gate cannot
silently un-fence the binding, and name the mirror that must land first.
"""

from .testutil import _compile, _entry, _lower_ctx, _fn
from ..codegen_cpp import CodeGenOptions

_RECORD = (
    "from typing import Iterator, Protocol\n"
    "from tpy import Int32, Own, readonly\n"
    "class A:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
)


def _gen(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return compiler, hpp + cpp


def _assert_identical(src: str) -> dict:
    """Byte-compare both paths; return THIR's fallback ledger."""
    _, ast_out = _gen(src, thir=False)
    compiler, thir_out = _gen(src, thir=True)
    assert ast_out == thir_out
    return dict(compiler._thir_fallback)


def _assert_body_fenced(src: str, name: str) -> None:
    """A sync body that must stay on the AST path, byte-identically."""
    thir = _lower_ctx(src)
    assert _fn(thir, name) is None
    fallback = _assert_identical(src)
    assert any(k.startswith("body:") for k in fallback), fallback


def _assert_resumable_fenced(src: str) -> None:
    """A resumable body that must stay on the AST path. Routed resumables never
    appear in `thir.functions`, so the fallback ledger is the only witness."""
    fallback = _assert_identical(src)
    assert any(k.startswith("resumable:") for k in fallback), fallback


class TestUnmirroredParamSeeds:
    """`seed_param_locals` classifies four param shapes into pointer /
    optional / ptr-variant / storage-tuple membership. `_LowerCtx` mirrors the
    pointer-repr `Optional[F1-record]` and ptr-variant-union arms only."""

    def test_optional_own_param_routes_record_kind(self):
        # `Own[A] | None` (sema's Optional[Own[A]], a VALUE-repr
        # `std::optional<A>` by-value param) -> the lc param seed registers
        # the RECORD-kind value-opt binding: the None-test reads has_value
        # and a narrowed read derefs `(*a)` -- position-blind (the AST
        # unwrap keys on `not uses_pointer_repr()`), so the fallthrough
        # read routes too.
        src = (_RECORD
               + "def f(a: Own[A] | None) -> Int32:\n"
               + "    if a is None:\n        return -1\n"
               + "    return a.v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _, ast_out = _gen(src, thir=False)
        compiler, thir_out = _gen(src, thir=True)
        assert ast_out == thir_out
        assert not any(k.startswith("body:") for k in compiler._thir_fallback)
        assert "if ((!a.has_value()))" in thir_out
        assert "return (*a).v;" in thir_out

    def test_own_optional_param_routes(self):
        # `Own[A | None]` (Own[Optional[A]], `std::optional<A>&&`): the
        # pointers + optional_locals seeds route the body -- the None test
        # reads has_value over the bare name, member reads spell `a->v`
        # (optional<A>::operator->).
        src = (_RECORD
               + "def f(a: Own[A | None]) -> Int32:\n"
               + "    if a is None:\n        return -1\n"
               + "    return a.v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _, ast_out = _gen(src, thir=False)
        compiler, thir_out = _gen(src, thir=True)
        assert ast_out == thir_out
        assert not any(k.startswith("body:") for k in compiler._thir_fallback)
        assert "if ((!a.has_value()))" in thir_out
        assert "return a->v;" in thir_out

    def test_own_optional_scalar_param_still_fenced(self):
        # The VALUE-payload twin (`Own[Int32] | None`) is outside the
        # record-kind seed (Own on a value type is a no-op spelling) and
        # outside `_value_opt_scalar`'s inner family -- keeps falling back.
        src = ("from tpy import Int32, Own\n"
               "def f(v: Own[Int32] | None) -> Int32:\n"
               "    if v is not None:\n        return v\n"
               "    return -1\n")
        _assert_body_fenced(src, "f")

    def test_own_optional_param_field_write_routes(self):
        # A WRITE through the narrowed binding: `a->v = 5` -- the same
        # arrow spelling as the read side (_field_receiver_ok's Own[Opt]
        # leg serves both positions).
        src = (_RECORD
               + "def f(a: Own[A | None]) -> Int32:\n"
               + "    if a is not None:\n"
               + "        a.v = 5\n"
               + "        return a.v\n"
               + "    return -1\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _, ast_out = _gen(src, thir=False)
        compiler, thir_out = _gen(src, thir=True)
        assert ast_out == thir_out
        assert not any(k.startswith("body:") for k in compiler._thir_fallback)
        assert "a->v = 5;" in thir_out

    def test_own_optional_param_own_slot_forward_fenced(self):
        # An Own[A|None] param name forwarded into ANOTHER Own[A|None] slot
        # (`return take(a)`): the whole-optional move-forward arg row is
        # unwitnessed -- the body keeps falling back (dualgen-probed
        # byte-identical via fallback).
        src = (_RECORD
               + "def take(x: Own[A | None]) -> Int32:\n"
               + "    if x is None:\n        return -1\n"
               + "    return x.v\n"
               + "def f(a: Own[A | None]) -> Int32:\n"
               + "    return take(a)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        fallback = _assert_identical(src)
        assert any(k.startswith("body:") for k in fallback), fallback

    def test_nullable_protocol_param_fenced(self):
        # A nullable static-protocol param -> codegen registers pointer_locals
        # + const_indirect_locals off the protocol-param classifier; lc mirrors
        # neither (`_optional_ptr_borrow` requires an F1-record inner).
        src = (_RECORD
               + "class Shape(Protocol):\n"
               + "    def area(self) -> Int32: ...\n"
               + "def f(s: Shape | None) -> Int32:\n"
               + "    if s is None:\n        return 0\n"
               + "    return s.area()\n")
        _assert_body_fenced(src, "f")

    def test_owned_tuple_param_fenced(self):
        # An owned-movable tuple param -> codegen registers
        # storage_form_tuple_locals + movable_locals (STORAGE_TUPLE, so a read
        # is STORAGE form); lc seeds neither, and `_is_borrow_form_name` would
        # tag the same type BORROW. Fence: the tuple-subscript / call arms.
        src = (_RECORD
               + "def sink(t: tuple[Own[A], Int32]) -> Int32:\n"
               + "    return t[1]\n"
               + "def f(t: tuple[Own[A], Int32]) -> Int32:\n"
               + "    return sink(t)\n")
        _assert_body_fenced(src, "f")


class TestOwnOptionalStorageBundle:
    """The Own[P | None] sync piece set beyond the param reads: return move,
    OPT_STORAGE_CALL slot reuse, the pure-lift decl/reseat, and the
    per-element-own tuple return + unpack (spec cases
    auto_move/scalar_own_optional + tuple/own_tuple_unpack_optional carry the
    corpus witnesses; these pin routing on the isolated shapes)."""

    def test_own_optional_passthrough_return_moves(self):
        # `return x` at the Own[A|None] slot moves the whole storage
        # optional out -- never the ptr_to_optional_move lift.
        src = (_RECORD
               + "def f(x: Own[A | None]) -> Own[A | None]:\n"
               + "    return x\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _, ast_out = _gen(src, thir=False)
        compiler, thir_out = _gen(src, thir=True)
        assert ast_out == thir_out
        assert not any(k.startswith("body:") for k in compiler._thir_fallback)
        assert "return std::move(x);" in thir_out

    def test_own_optional_call_decl_slot_reuse(self):
        # A REASSIGNED Own[A|None]-call local keeps ONE slot: the decl
        # materializes it, each reseat re-fills and re-lifts.
        src = (_RECORD
               + "def make(v: Int32) -> Own[A | None]:\n"
               + "    if v > 0:\n        return A(v)\n"
               + "    return None\n"
               + "def f() -> Int32:\n"
               + "    z = make(1)\n"
               + "    z = make(2)\n"
               + "    if z is not None:\n        return z.v\n"
               + "    return -1\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _, ast_out = _gen(src, thir=False)
        compiler, thir_out = _gen(src, thir=True)
        assert ast_out == thir_out
        assert not any(k.startswith("body:") for k in compiler._thir_fallback)
        assert "std::optional<A> __slot_1 = make(1);" in thir_out
        assert "__slot_1 = make(2);" in thir_out
        assert thir_out.count("::tpy::optional_to_ptr(__slot_1)") == 2

    def test_own_optional_param_pointer_local_lift(self):
        # First-decl and reseat of a pointer local from the param: the pure
        # optional_to_ptr lift, no slot.
        src = (_RECORD
               + "def f(x: Own[A | None]) -> Int32:\n"
               + "    y = x\n"
               + "    if y is None:\n        return -1\n"
               + "    return y.v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _, ast_out = _gen(src, thir=False)
        compiler, thir_out = _gen(src, thir=True)
        assert ast_out == thir_out
        assert not any(k.startswith("body:") for k in compiler._thir_fallback)
        assert "A* y = ::tpy::optional_to_ptr(x);" in thir_out

    def test_own_optional_tuple_elem_return_and_unpack(self):
        # `-> tuple[Own[A | None], Int32]` returns the spelled storage
        # brace-init; the standalone unpack lifts the capture whole via
        # tuple_to_pointer and binds the opt_ptr target bare.
        src = (_RECORD
               + "def pair() -> tuple[Own[A | None], Int32]:\n"
               + "    return (A(42), Int32(99))\n"
               + "def f() -> Int32:\n"
               + "    p, n = pair()\n"
               + "    if p is None:\n        return n\n"
               + "    return p.v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "pair") is not None
        assert _fn(thir, "f") is not None
        _, ast_out = _gen(src, thir=False)
        compiler, thir_out = _gen(src, thir=True)
        assert ast_out == thir_out
        assert not any(k.startswith("body:") for k in compiler._thir_fallback)
        assert ("return std::tuple<std::optional<A>, int32_t>{A(42), 99};"
                in thir_out)
        assert ("::tpy::tuple_to_pointer<std::tuple<A*, int32_t>>(pair())"
                in thir_out)


class TestUnmirroredLocalBindings:
    """Bindings a statement arm creates whose form is NOT the declared type's
    primary repr -- the shape class behind the union-element divergence."""

    def test_optional_element_loop_var_fenced(self):
        # The Optional twin of the union-element divergence: iterating
        # `list[A | None]` binds the STORAGE form (`std::optional<A>`, which
        # `register_loop_var_storage_form` records in
        # storage_form_optional_locals), while the declared element type reads
        # as a pointer-repr Optional. Fence: the for-each element family.
        src = (_RECORD
               + "def f(xs: list[A | None]) -> Int32:\n"
               + "    total = 0\n"
               + "    for x in xs:\n"
               + "        if x is not None:\n            total += x.v\n"
               + "    return total\n")
        _assert_body_fenced(src, "f")

    def test_ptr_variant_unpack_target_fenced(self):
        # `_gen_tuple_unpack` lifts a union element to the POINTER variant and
        # registers the target in ptr_variant_locals -- the one producer of
        # that set `_LowerCtx` does not mirror. Fence: the unpack target
        # classifier admits scalar / str / record / opt-ptr shapes only, so a
        # union-typed target never becomes a binding.
        src = ("from tpy import Int32\n"
               "class Dog:\n    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
               "class Cat:\n    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
               "def pick(d: Dog, c: Cat, flag: bool) -> tuple[Dog | Cat, Int32]:\n"
               "    if flag:\n        return (d, 1)\n"
               "    return (c, 2)\n"
               "def f(d: Dog, c: Cat) -> Int32:\n"
               "    who, k = pick(d, c, True)\n"
               "    if isinstance(who, Dog):\n        return who.n + k\n"
               "    return k\n")
        _assert_body_fenced(src, "f")

    def test_optional_call_return_local_routes_bare_bind(self):
        # `y = find(...)` on an `A | None` return: the callee's `A*` borrow
        # return IS the binding's shape, so the decl binds it bare
        # (`A* y = find(xs);` -- the decl.opt_call_passthrough row). The
        # former fence claimed an owned optional slot; the oracle renders
        # the bare bind (byte-verified at conversion), so the fence
        # converted per the un-defer rule. An OWN-declared callee keeps the
        # slot lane (the row's `_own_declared_call_ret` guard).
        src = (_RECORD
               + "def find(xs: list[A]) -> A | None:\n"
               + "    for x in xs:\n"
               + "        if x.v == 1:\n            return x\n"
               + "    return None\n"
               + "def f(xs: list[A]) -> Int32:\n"
               + "    y = find(xs)\n"
               + "    if y is not None:\n        return y.v\n"
               + "    return -1\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        fallback = _assert_identical(src)
        assert not fallback, fallback


class TestResumableFrameBindings:
    """`setup_resumable_frame_locals` registers frame locals into
    const_indirect_locals / storage_form_tuple_locals; the resumable lowering
    mirrors the pointer families only."""

    def test_owning_tuple_frame_local_at_borrow_sink_fenced(self):
        # An owning pointer-repr tuple frame local is STORAGE_TUPLE in codegen
        # (`storage_form_tuple_locals`), so passing it whole to a borrow-form
        # tuple param lifts via tuple_to_pointer. lc.storage_tuple_locals is
        # untouched in the resumable lane -- the lift would be dropped.
        src = (_RECORD
               + "def use_pair(p: tuple[A, Int32]) -> Int32:\n"
               + "    return p[1]\n"
               + "def g() -> Iterator[Int32]:\n"
               + "    t = (A(1), 2)\n"
               + "    yield use_pair(t)\n"
               + "    yield 0\n"
               + "def main() -> None:\n"
               + "    for n in g():\n        print(n)\n"
               + "main()\n")
        _assert_resumable_fenced(src)

    def test_const_pointer_alias_frame_local_routes(self):
        # The routed half of the same producer: a statement-level borrow alias
        # in a generator lands in codegen's generator_pointer_alias_locals (+
        # the const variant into const_indirect_locals). THIR derives the const
        # from the source instead of mirroring the set -- byte-identical today,
        # so this pins the derivation rather than the reject.
        src = (_RECORD
               + "def g(items: readonly[list[A]]) -> Iterator[Int32]:\n"
               + "    a = items[0]\n"
               + "    yield a.v\n"
               + "    yield a.v + 1\n"
               + "def main() -> None:\n"
               + "    xs = [A(7)]\n"
               + "    for n in g(xs):\n        print(n)\n"
               + "main()\n")
        fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback), fallback


class TestOverloadStubPrologueProducer:
    """`gen_body`'s short-@overload-stub prologue is the fourth producer of
    codegen's `ptr_variant_locals` (it emits `T x = <default>;` for a param the
    stub omits, then registers it). `_LowerCtx` does not mirror it, and no
    lowering arm consults the set for such a name -- the narrowing arms would
    read the binding as a VALUE variant. What keeps it unreachable is
    admission: a stub whose omitted param needs a live prologue local is
    rejected outright."""

    _SRC = ("from typing import overload\n"
            "from tpy import Int32\n"
            "class Dog:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "@overload\n"
            "def pick(k: Int32) -> Int32: ...\n"
            "@overload\n"
            "def pick(k: Int32, pet: Int32 | Dog) -> Int32: ...\n"
            "def pick(k: Int32, pet: Int32 | Dog = Int32(5)) -> Int32:\n"
            "    if isinstance(pet, Dog):\n"
            "        return k + pet.n\n"
            "    return k\n"
            "def main() -> None:\n"
            "    print(pick(1))\n"
            "main()\n")

    def test_short_stub_with_union_prologue_param_is_fenced(self):
        thir = _lower_ctx(self._SRC)
        # The SHORT stub omits `pet`, so its body would read a name codegen
        # registers as a pointer variant and lc does not track at all.
        assert _fn(thir, "pick") is None
        fallback = _assert_identical(self._SRC)
        # The arity gate (`_short_stub_missing_ok`) is the specific fence: it
        # admits a short stub only when every omitted param narrows to NoneType
        # and is never reassigned, which is exactly the case needing no
        # prologue local -- and therefore no registration to mirror.
        assert fallback.get("body:sig.overload_set.arity") == 1, fallback
