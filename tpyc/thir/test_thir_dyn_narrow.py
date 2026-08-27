"""Polymorphic-isinstance narrowing (dyn-protocol / inheritance subjects):
if-init cast condition, spelled reads, and the excluded-face fallbacks."""
from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_byte_identical, _compile, _entry, _fn, _lower_ctx,
    _lower_ctx_witnessed,
)

_PRELUDE = (
    "from typing import Protocol\n"
    "from tpy import dynamic\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def name(self) -> str: ...\n"
    "class Dog(Pet):\n"
    "    def name(self) -> str:\n        return \"dog\"\n"
    "class Cat:\n"
    "    label: str\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "    def name(self) -> str:\n        return self.label\n"
    "    def purr(self) -> str:\n        return self.label + \"-purr\"\n"
)


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestDynNarrowLowering:
    SRC = (
        _PRELUDE
        + "def structural(p: Pet) -> str:\n"
        + "    if isinstance(p, Cat):\n"
        + "        return \"cat:\" + p.purr()\n"
        + "    return \"other:\" + p.name()\n"
        + "def inherits(p: Pet) -> str:\n"
        + "    if isinstance(p, Dog):\n"
        + "        return \"dog:\" + p.name()\n"
        + "    return \"other\"\n"
    )

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("structural", "inherits"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        # Structural conformer: adapter cast through the @dynamic base.
        assert ("if (Cat* __p_ptr = ::tpy::dyn_adapter_cast<Pet, Cat>(&p); "
                "(__p_ptr != nullptr)) {") in out
        # Inheritance conformer: plain dynamic_cast.
        assert ("if (Dog* __p_ptr = dynamic_cast<Dog*>(&p); "
                "(__p_ptr != nullptr)) {") in out
        # Branch reads render the pre-bound pointer's deref.
        assert "(*__p_ptr).purr()" in out

    def test_elif_link_routes(self):
        # A dyn condition in an elif link: each chain link gates
        # independently through the shared skeleton.
        src = (
            _PRELUDE
            + "from tpy import Int32\n"
            + "def f(p: Pet, k: Int32) -> str:\n"
            + "    if k > 0:\n        return \"k\"\n"
            + "    elif isinstance(p, Cat):\n"
            + "        return p.purr()\n"
            + "    return p.name()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert ("} else if (Cat* __p_ptr = "
                "::tpy::dyn_adapter_cast<Pet, Cat>(&p); "
                "(__p_ptr != nullptr)) {") in out

    def test_while_head_falls_back(self):
        # The while-isinstance position is an excluded rung for the dyn arm
        # (the fresh-reference-local face, not the if-init form).
        src = (
            _PRELUDE
            + "from tpy import Int32\n"
            + "def f(p: Pet) -> Int32:\n"
            + "    n = 0\n"
            + "    while isinstance(p, Cat):\n"
            + "        n += 1\n"
            + "        if n > 3:\n            break\n"
            + "    return n\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_optional_subject_falls_back(self):
        # Pointer-repr Optional subjects spell the cast input differently
        # (bare name, not &name) -- excluded from the slice.
        src = (
            _PRELUDE
            + "def f(p: Pet | None) -> str:\n"
            + "    if p is not None:\n"
            + "        if isinstance(p, Cat):\n"
            + "            return p.purr()\n"
            + "    return \"no\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_tuple_check_routes_or_chain(self):
        # A tuple check yields a union fact (no alias); the condition
        # routes as the no-init dynamic_cast OR-chain
        # (THIRDynIsinstanceMulti) -- dualgen-verified identical, the
        # structural-conformer cast riding narrow_cast_rhs.
        src = (
            _PRELUDE
            + "def f(p: Pet) -> str:\n"
            + "    if isinstance(p, (Cat, Dog)):\n"
            + "        return \"pet\"\n"
            + "    return \"no\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestDerefViewNarrowIf:
    """The deref-view isinstance if (wave 10): `isinstance(b, Dog)` through
    a Deref wrapper (Box[Pet]) lowers the C++17 if-init cast of the deref
    PAYLOAD pointer; branch member calls carrying deref_narrowed_to read
    `(*__b_ptr)`; post-branch calls revert to the deref chain. The wrapper
    var is never retyped (sema keys the fact under deref_view_key). A
    same-module @dynamic-protocol type-arg is in the F1 slice (both paths
    spell the bare name); imported ones keep rejecting."""

    _SRC = (
        "from typing import Protocol\n"
        "from tpy import Int32, dynamic\n"
        "from tplib.box import Box\n"
        "@dynamic\n"
        "class Pet(Protocol):\n"
        "    def name(self) -> str: ...\n"
        "class Dog(Pet):\n"
        "    def name(self) -> str:\n"
        "        return \"dog\"\n"
        "    def bark(self) -> str:\n"
        "        return \"woof\"\n"
        "class Cat(Pet):\n"
        "    def name(self) -> str:\n"
        "        return \"cat\"\n"
        "def describe(b: Box[Pet]) -> str:\n"
        "    if isinstance(b, Dog):\n"
        "        return \"dog:\" + b.bark()\n"
        "    return \"other:\" + b.name()\n"
        "def main() -> None:\n"
        "    print(describe(Box(Dog())))\n"
        "    print(describe(Box(Cat())))\n"
        "main()\n"
    )

    def test_deref_view_if_routes(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "describe") is not None
        assert faces.get("if.deref_view_narrow", 0) >= 1
        assert faces.get("method.deref_view_narrowed", 0) >= 1
        _assert_byte_identical(self._SRC)

    def test_emit_shapes(self):
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        out = hpp + cpp
        assert "__b_ptr = dynamic_cast<Dog*>(&(b.__deref__()));" in out
        assert "(*__b_ptr).bark()" in out
        assert "b.__deref__().name()" in out

    def test_narrowed_call_with_args_defers(self):
        # The witnessed slice is zero-arg methods; an ARG-carrying narrowed
        # call keeps the whole-body fallback.
        src = self._SRC.replace(
            "    def bark(self) -> str:\n        return \"woof\"\n",
            "    def bark(self, n: Int32) -> str:\n"
            "        return \"woof\" * n\n").replace(
            "b.bark()", "b.bark(2)")
        thir, _f = _lower_ctx_witnessed(src)
        assert _fn(thir, "describe") is None
        _assert_byte_identical(src)

    def test_branch_rebind_of_wrapper_routes(self):
        # Reassigning the wrapper LOCAL inside the narrowed branch (the
        # deref_view_rebind_invalidates shape) now routes: the rebind-slot
        # pointer local is admitted, sema's marker invalidation drops the
        # narrow at the reseat (the post-rebind read carries no
        # deref_narrowed_to), and the plain deref chain re-renders.
        from .testutil import _assert_routes_byte_identical
        src = self._SRC.replace(
            "def describe(b: Box[Pet]) -> str:\n"
            "    if isinstance(b, Dog):\n"
            "        return \"dog:\" + b.bark()\n"
            "    return \"other:\" + b.name()\n",
            "def describe() -> str:\n"
            "    b: Box[Pet] = Box(Dog())\n"
            "    if isinstance(b, Dog):\n"
            "        b = Box(Cat())\n"
            "        return \"rebound:\" + b.name()\n"
            "    return \"other:\" + b.name()\n").replace(
            "    print(describe(Box(Dog())))\n"
            "    print(describe(Box(Cat())))\n",
            "    print(describe())\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "b->__deref__().name()" in cpp

    def test_structural_conformer_adapter_cast(self):
        # The STRUCTURAL flavor (Cat conforms without inheriting): the
        # if-init spells dyn_adapter_cast over the same deref payload.
        src = self._SRC.replace(
            "    if isinstance(b, Dog):\n"
            "        return \"dog:\" + b.bark()\n",
            "    if isinstance(b, Cat):\n"
            "        return \"cat:\" + b.name()\n").replace(
            "class Cat(Pet):\n", "class Cat:\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "describe") is not None
        assert faces.get("if.deref_view_narrow", 0) >= 1
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        out = hpp + cpp
        assert "::tpy::dyn_adapter_cast<Pet, Cat>(&(b.__deref__()))" in out
        assert "(*__b_ptr).name()" in out
        _assert_byte_identical(src)


class TestDerefViewPointerLocal:
    """A deref-view isinstance over a REBIND-SLOT pointer local: the cast
    arg derefs (`&((*b).__deref__())`), the narrow-consuming read renders
    the payload alias, and an in-branch reseat invalidates through sema's
    marker (the post-rebind read re-renders the plain deref chain)."""

    _PRE = (
        "from typing import Protocol\n"
        "from tpy import dynamic\n"
        "from tplib.box import Box\n"
        "@dynamic\n"
        "class Pet(Protocol):\n"
        "    def name(self) -> str: ...\n"
        "class Dog(Pet):\n"
        "    def name(self) -> str:\n"
        "        return \"dog\"\n"
        "class Cat(Pet):\n"
        "    def name(self) -> str:\n"
        "        return \"cat\"\n")

    def test_pointer_local_deref_view_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            self._PRE
            + "def f() -> str:\n"
            + "    b: Box[Pet] = Box(Dog())\n"
            + "    if isinstance(b, Dog):\n"
            + "        b = Box(Cat())\n"
            + "        return b.name()\n"
            + "    return \"unreachable\"\n"
            + "def main() -> None:\n"
            + "    print(f())\n"
            + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert ("dynamic_cast<Dog*>(&((*b).__deref__()))") in cpp
        assert "return b->__deref__().name();" in cpp

    def test_narrow_consuming_read_routes(self):
        # The reassigned-then-narrowed flavor: the branch read consumes the
        # payload alias (`(*__b_ptr)`), no rebind inside.
        from .testutil import _assert_routes_byte_identical
        src = (
            self._PRE
            + "def g() -> str:\n"
            + "    b: Box[Pet] = Box(Dog())\n"
            + "    b = Box(Cat())\n"
            + "    if isinstance(b, Cat):\n"
            + "        return b.name()\n"
            + "    return \"no\"\n"
            + "def main() -> None:\n"
            + "    print(g())\n"
            + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "(*__b_ptr)" in cpp

    def test_non_rebind_pointer_local_still_defers(self):
        # The gate admits rebind-slot pointer locals only; other pointer
        # flavors (an Optional-ptr param wrapper var) keep the AST path.
        from .testutil import _fn, _lower_ctx
        from typing import Optional
        src = (
            self._PRE
            + "from typing import Optional\n"
            + "def h(ob: Optional[Box[Pet]]) -> str:\n"
            + "    if ob is None:\n"
            + "        return \"none\"\n"
            + "    if isinstance(ob, Dog):\n"
            + "        return ob.name()\n"
            + "    return \"other\"\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "h") is None


class TestDerefMethodFieldReceiver:
    """A user-Deref method call off a narrowed OPTIONAL FIELD receiver
    (`self.val.speak()` on `val: Optional[Box[Pet]]` proven non-None):
    the field render carries the storage-optional unwrap
    (`(*this->val).__deref__().speak()`)."""

    _PRE = (
        "from typing import Optional, Protocol\n"
        "from tpy import dynamic\n"
        "from tplib.box import Box\n"
        "@dynamic\n"
        "class Pet(Protocol):\n"
        "    def speak(self) -> str: ...\n"
        "class Dog(Pet):\n"
        "    def speak(self) -> str:\n"
        "        return \"woof\"\n"
        "class Holder:\n"
        "    val: Optional[Box[Pet]]\n"
        "    def __init__(self) -> None:\n"
        "        self.val = None\n"
        "    def emit(self) -> None:\n"
        "        if self.val is not None:\n"
        "            print(self.val.speak())\n"
        "        else:\n"
        "            print(\"(empty)\")\n")

    def test_narrowed_optional_field_receiver_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            self._PRE
            + "def main() -> None:\n"
            + "    h = Holder()\n"
            + "    h.emit()\n"
            + "    h.val = Box(Dog())\n"
            + "    h.emit()\n"
            + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "(*this->val).__deref__().speak()" in (_hpp + cpp)

    def test_unproven_optional_field_receiver_still_defers(self):
        # No narrowing guard: the read carries needs_optional_runtime_check
        # and fails the markers gate -- the body keeps the AST path.
        from .testutil import _fn, _lower_ctx
        src = self._PRE.replace(
            "    def emit(self) -> None:\n"
            "        if self.val is not None:\n"
            "            print(self.val.speak())\n"
            "        else:\n"
            "            print(\"(empty)\")\n",
            "    def emit(self) -> None:\n"
            "        print(self.val.speak())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "emit") is None

    def test_field_read_twin_routes(self):
        # The shared receiver resolver also serves the deref FIELD-READ
        # twin: `self.val.n` off a narrowed Optional[Box[Payload]] field
        # renders `(*this->val).__deref__().n`.
        from .testutil import _assert_routes_byte_identical
        src = (
            "from typing import Optional\n"
            "from tpy import Int32\n"
            "from tplib.box import Box\n"
            "class Payload:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class Holder:\n"
            "    val: Optional[Box[Payload]]\n"
            "    def __init__(self) -> None:\n"
            "        self.val = None\n"
            "    def peek(self) -> Int32:\n"
            "        if self.val is not None:\n"
            "            return self.val.n\n"
            "        return -1\n"
            "def main() -> None:\n"
            "    h = Holder()\n"
            "    print(h.peek())\n"
            "    h.val = Box(Payload(9))\n"
            "    print(h.peek())\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "(*this->val).__deref__().n" in (_hpp + cpp)


class TestPolyInlineNarrowUnderAnd:
    """Inline poly isinstance under `&&` in expression position: the LHS
    lowers via the shared cast chokepoint and the RHS reads the SPELLED
    static_cast (well-defined after the LHS dynamic_cast validated).
    INHERIT conformers only; `||` spines install nothing and fall back."""

    _SRC = (
        "from typing import Protocol\n"
        "from tpy import dynamic, Int32\n"
        "@dynamic\n"
        "class Tagged(Protocol):\n"
        "    pass\n"
        "class Pet(Tagged):\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "    def name(self) -> str:\n"
        "        return \"pet\"\n"
        "class Dog(Pet):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__()\n"
        "    def bark(self) -> str:\n"
        "        return \"woof\"\n"
        "def check_and(p: Pet, threshold: Int32) -> bool:\n"
        "    return isinstance(p, Dog) and len(p.bark()) > threshold\n"
        "def main() -> None:\n"
        "    print(check_and(Dog(), 2))\n"
        "main()\n")

    def test_and_spine_routes_spelled_rhs(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("binop.poly_inline_narrow", 0) >= 1
        joined = _hpp + cpp
        assert "(dynamic_cast<Dog*>(&p) != nullptr)" in joined
        assert "(*static_cast<Dog*>(&p)).bark()" in joined

    def test_or_spine_still_defers(self):
        # BOUNDARY: `||` has no complement fact -- the isinstance leaf
        # falls to the generic call gate and the body keeps the fence.
        src = self._SRC.replace(
            "    return isinstance(p, Dog) and len(p.bark()) > threshold\n",
            "    return isinstance(p, Dog) or len(p.name()) > threshold\n")
        from .testutil import _fn as _fn_l, _lower_ctx
        thir = _lower_ctx(src)
        assert _fn_l(thir, "check_and") is None


class TestErasedDynOwnDecl:
    """The already-erased Own[dyn] call decl (`c = make(41)` -- the
    'forward' verdict) and the return-position async-factory erasure
    (`return add_one(n)` at Own[Cancellable[T]] -> the make_adapter wrap).
    The decl is a VALUE local (unique_ptr spelled, NOT a Base* pointer
    registration); reads move at last use."""

    _SRC = (
        "import asyncio\n"
        "from tpy import Own\n"
        "from tpy.coro import Cancellable\n\n"
        "async def add_one(n: int) -> int:\n"
        "    return n + 1\n\n"
        "def make(n: int) -> Own[Cancellable[int]]:\n"
        "    return add_one(n)\n\n"
        "def main() -> None:\n"
        "    c = make(41)\n"
        "    print(asyncio.run(c))\n"
        "main()\n")

    def test_erased_decl_and_factory_return_route(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("decl.dyn_own_erased_call", 0) >= 1
        assert wit.get("ret.dyn_own_factory", 0) >= 1
        joined = _hpp + cpp
        assert ("return ::tpy::make_adapter<::tpystd::coro::Cancellable<"
                "::tpy::BigInt>>(add_one(n));") in joined
        assert ("std::unique_ptr<::tpystd::coro::Cancellable<::tpy::BigInt>>"
                " c = make(::tpy::BigInt(41));") in joined
        assert "std::move(c)" in joined
        assert "std::move((*c))" not in joined

    def test_reassigned_erased_handle_still_defers(self):
        # BOUNDARY: a reassigned erased handle keeps the body falling back
        # (the unique_ptr re-assign render is its own unwitnessed flavor).
        src = self._SRC.replace(
            "    c = make(41)\n",
            "    c = make(41)\n"
            "    c = make(1)\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert any(k.startswith("body:") for k in compiler._thir_fallback)


class TestBuiltinValueRecordFamily:
    """A non-native BUILTIN ValueType record (tpy.coro.Waker): plain
    spelled decl copy, bare STORAGE return, plain member receiver, and
    bare pass-through arg -- the F1 family's builtin sibling. TypeDef-only
    value types (str/bytes) and @native value records (Span) stay out via
    the non-native record-info guard."""

    _SRC = (
        "from asyncio._executor import Executor, _make_waker\n"
        "def main() -> None:\n"
        "    e = Executor()\n"
        "    w = _make_waker(e, 0, 0)\n"
        "    e.register_timer(0.5, w)\n"
        "    w.wake()\n"
        "main()\n")

    def test_all_four_legs_route(self):
        # decl slot + call ret, receiver (`w.wake()`) and the bare
        # pass-through arg (`register_timer(0.5, w)`).
        #
        # The faces are the F1 family's, not this family's dedicated pair:
        # a RECORD-category TypeDef is an F1 record whatever its
        # `cpp_formatter`, so the F1 decl / call-ret / receiver arms claim
        # these legs first. `_builtin_value_record`'s own arms sit behind
        # them in their or-chains and no longer decide this type.
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("decl.owned_record", 0) >= 1
        assert wit.get("call.imported", 0) >= 1
        assert wit.get("recv.builtin_record", 0) >= 1
        joined = _hpp + cpp
        assert "::tpystd::coro::Waker w = " in joined
        assert "w.wake();" in joined
        assert ", w);" in joined

    def test_span_decl_still_defers(self):
        # BOUNDARY: a @native value record (Span projection off a record
        # element) keeps its own gated rows -- the native guard. Pinned in
        # depth by TestSpanLocalDecl; this asserts the guard predicate
        # itself never witnesses the builtin faces for a span decl.
        from .testutil import _lower_ctx_witnessed
        src = (
            "from tpy import Int32, Span\n"
            "def f(sp: Span[Int32]) -> Int32:\n"
            "    s = sp\n"
            "    return s[0]\n"
            "def main() -> None:\n"
            "    pass\n"
            "main()\n")
        _thir, wit = _lower_ctx_witnessed(src)
        assert not wit.get("decl.builtin_value_record_slot")


class TestGenericAsyncMethodFactory:
    """A generic async METHOD at a factory position spells inline (member
    call + method targs + the consumer's make_adapter wrap) -- the sync
    generic-method render. The positions that would spell the coro FRAME
    type keep gating generics themselves (decl arm / await gate)."""

    _SRC = (
        "import asyncio\n"
        "class Box[T]:\n"
        "    val: T\n"
        "    def __init__(self, v: T) -> None:\n"
        "        self.val = v\n"
        "    async def with_label[U](self, label: U) -> U:\n"
        "        return label\n"
        "def main() -> None:\n"
        "    b = Box(42)\n"
        "    print(asyncio.run(b.with_label(\"hey\")))\n"
        "main()\n")

    def test_generic_async_method_consumer_arg_routes(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        joined = _hpp + cpp
        assert "b.with_label<std::string>(" in joined
        assert "::tpy::make_adapter<" in joined

    def test_generic_factory_bind_still_defers(self):
        # BOUNDARY: binding the generic factory to a LOCAL would spell the
        # templated frame type -- the decl arm's coro_inferred_type_args
        # gate keeps the body falling back.
        src = self._SRC.replace(
            "    print(asyncio.run(b.with_label(\"hey\")))\n",
            "    c = b.with_label(\"hey\")\n"
            "    print(asyncio.run(c))\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert any(k.startswith("body:") for k in compiler._thir_fallback)


class TestResumableSelfDynNarrow:
    """`isinstance(self, Sub)` in a RESUMABLE method body: the single-fact
    then-only slice is the AST's if-INIT form (`__self_ptr = dynamic_cast<
    ...>(&__self)`), which never collides with the `__self` frame field --
    only the ALIAS emissions (assert / early-return, `__self_narrowed`)
    stay gated. A @readonly method's capture is `const T&`, so the cast
    targets `const Sub*` (const_locals seeded like the sync twin)."""

    _SRC = (
        "from typing import Protocol\n"
        "from tpy import dynamic, readonly\n"
        "import asyncio\n"
        "@dynamic\n"
        "class Tagged(Protocol):\n"
        "    pass\n"
        "class Pet(Tagged):\n"
        "    _name: str\n"
        "    def __init__(self, n: str) -> None:\n"
        "        self._name = n\n"
        "    @readonly\n"
        "    async def describe(self) -> str:\n"
        "        await asyncio.sleep(0)\n"
        "        if isinstance(self, Dog):\n"
        "            return \"dog: \" + self.bark()\n"
        "        return \"pet: \" + self._name\n"
        "    async def rename(self) -> str:\n"
        "        await asyncio.sleep(0)\n"
        "        self._name = self._name + \"!\"\n"
        "        if isinstance(self, Dog):\n"
        "            return \"dog \" + self.bark()\n"
        "        return \"pet \" + self._name\n"
        "class Dog(Pet):\n"
        "    def __init__(self, n: str) -> None:\n"
        "        super().__init__(n)\n"
        "    @readonly\n"
        "    def bark(self) -> str:\n"
        "        return \"woof \" + self._name\n"
        "async def amain() -> None:\n"
        "    d = Dog(\"rex\")\n"
        "    print(await d.describe())\n"
        "    print(await d.rename())\n"
        "def main() -> None:\n"
        "    asyncio.run(amain())\n"
        "main()\n")

    def test_readonly_and_mutable_flavors_route(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        # @readonly capture is `const T&` -> const cast; plain is mutable.
        assert ("const Dog* __self_ptr = "
                "dynamic_cast<const Dog*>(&__self)") in cpp
        assert "Dog* __self_ptr = dynamic_cast<Dog*>(&__self)" in cpp
        assert "(*__self_ptr).bark()" in cpp

    def test_assert_narrow_self_still_defers(self):
        # BOUNDARY: the assert-narrow ALIAS emission (`__self_narrowed`)
        # stays gated -- the resumable body must fall back.
        src = self._SRC.replace(
            "        if isinstance(self, Dog):\n"
            "            return \"dog: \" + self.bark()\n"
            "        return \"pet: \" + self._name\n",
            "        assert isinstance(self, Dog)\n"
            "        return \"dog: \" + self.bark()\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert any(k.startswith("resumable:")
                   for k in compiler._thir_fallback)


class TestCoroFrameLocal:
    """An async-factory call bound to a local defers erasure: the decl
    holds the CONCRETE frame in optional storage
    (`std::optional<__coro_add_one> c = add_one(41);`) and the Own[dyn]
    consumer arg erases (`run(make_adapter<...>(std::move(*(c))))`)."""

    _SRC = (
        "import asyncio\n"
        "from tpy import Int32\n"
        "async def add_one(n: int) -> int:\n"
        "    return n + 1\n"
        "def main() -> None:\n"
        "    c = add_one(41)\n"
        "    print(asyncio.run(c))\n"
        "main()\n")

    def test_coro_frame_local_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("decl.coro_frame_local", 0) >= 1
        assert "std::optional<__coro_add_one> c = add_one(::tpy::BigInt(41));" in cpp
        assert "std::move(*(c))" in cpp

    def test_reassigned_coro_local_rebind_routes(self):
        # The REBIND flavor: the decl admits a reassigned handle and every
        # rebind re-emplaces into the optional slot (a call source constructs
        # in place; a NAME source move-constructs + resets -- optional's
        # move-assign is deleted when the frame holds reference members).
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = self._SRC.replace(
            "    c = add_one(41)\n",
            "    c = add_one(41)\n"
            "    c = add_one(1)\n"
            "    d = add_one(7)\n"
            "    c = d\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("decl.coro_frame_rebind", 0) >= 2
        assert "c.emplace(add_one(::tpy::BigInt(1)));" in cpp
        assert "c.emplace(std::move(*d));" in cpp
        assert "d.reset();" in cpp

    def test_hoisted_coro_local_still_defers(self):
        # BOUNDARY: an escape-HOISTED handle keeps rejecting -- its decl
        # placement is the hoist machinery, not this direct-init arm. Full
        # pipeline (not _lower_ctx): the unit shim's render crashes on
        # ConcreteCoroType, which the real fallback path never renders.
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        src = (
            "import asyncio\n"
            "from tpy import Int32\n"
            "async def add_one(n: int) -> int:\n"
            "    return n + 1\n"
            "def main() -> None:\n"
            "    flag = True\n"
            "    if flag:\n"
            "        c = add_one(41)\n"
            "    else:\n"
            "        c = add_one(1)\n"
            "    print(asyncio.run(c))\n"
            "main()\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert any(k.startswith("body:") for k in compiler._thir_fallback)

    def test_erased_own_binding_still_defers(self):
        # A protocol-param callee erases at BIND time (sema types the local
        # Own[Cancellable[T]], the AST renders make_adapter/unique_ptr) --
        # the ConcreteCoroType gate keeps it out (reviewer-reproduced
        # Critical: the un-gated arm dropped the adapter entirely).
        from .testutil import _fn as _fn_l, _lower_ctx
        src = (
            "import asyncio\n"
            "from typing import Protocol\n"
            "from tpy import Int32\n"
            "class Measurable(Protocol):\n"
            "    def size(self) -> Int32: ...\n"
            "class Widget:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def size(self) -> Int32:\n"
            "        return self.n\n"
            "async def add_one(w: Measurable) -> Int32:\n"
            "    return w.size() + 1\n"
            "def main() -> None:\n"
            "    w = Widget(4)\n"
            "    c = add_one(w)\n"
            "    print(asyncio.run(c))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn_l(thir, "main") is None
