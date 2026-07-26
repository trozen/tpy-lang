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

    def test_own_optional_param_fenced(self):
        # `Own[A | None]` -> codegen registers pointer_locals + optional_locals
        # and rebinds var_types to the bare Optional; `_optional_ptr_borrow`
        # excludes the Own inner, so lc has neither. Fence: the None-test arm.
        src = (_RECORD
               + "def f(a: Own[A | None]) -> Int32:\n"
               + "    if a is None:\n        return -1\n"
               + "    return a.v\n")
        _assert_body_fenced(src, "f")

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

    def test_optional_call_return_local_fenced(self):
        # `y = find(...)` on an `A | None` return: the local is an owned
        # optional SLOT, not the `T*` borrow its declared type suggests.
        src = (_RECORD
               + "def find(xs: list[A]) -> A | None:\n"
               + "    for x in xs:\n"
               + "        if x.v == 1:\n            return x\n"
               + "    return None\n"
               + "def f(xs: list[A]) -> Int32:\n"
               + "    y = find(xs)\n"
               + "    if y is not None:\n        return y.v\n"
               + "    return -1\n")
        _assert_body_fenced(src, "f")


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
