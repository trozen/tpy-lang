"""THIR protocol boundary: bare protocol params (structural + @dynamic) and
method calls dispatched on a protocol receiver."""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .nodes import (
    PtrSlotKind,
    THIRExprStmt,
    THIRMethodCall,
    THIRName,
    THIRPtrLocalDecl,
    THIRPtrLocalRebind,
    THIRReturn,
)
from .testutil import (
    _assert_byte_identical,
    _emit_expr as _emit,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

# A structural protocol (monomorphized -- `template<Measurable T_x> ... const T_x&`)
# and a @dynamic one (a vtable `Pet&`). Both bind as a C++ reference, so their
# bodies render identically; only the AST-emitted signature differs.
_PROTOCOLS = (
    "from typing import Protocol\n"
    "from tpy import Int32, dynamic, readonly\n"
    "class Measurable(Protocol):\n"
    "    @readonly\n"
    "    def length(self) -> Int32: ...\n"
    "    @readonly\n"
    "    def scaled(self, k: Int32) -> Int32: ...\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def make_noise(self) -> str: ...\n"
    "class Dog(Pet):\n"
    "    def make_noise(self) -> str:\n        return \"Woof\"\n"
    "class Ruler:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "    @readonly\n"
    "    def length(self) -> Int32:\n        return self.n\n"
    "    @readonly\n"
    "    def scaled(self, k: Int32) -> Int32:\n        return self.n * k\n"
)


def _src(body: str) -> str:
    return _PROTOCOLS + body


class TestProtocolParams:
    def test_structural_param_routes_and_calls_method(self):
        thir, faces = _lower_ctx_witnessed(_src(
            "def size(m: Measurable) -> Int32:\n    return m.length()\n"))
        fn = _fn(thir, "size")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        call = ret.value
        assert isinstance(call, THIRMethodCall)
        assert call.method_cpp == "length"
        # A protocol binding is a reference, never a pointer: `.`, not `->`.
        assert not call.is_arrow and not call.deref_check
        assert isinstance(call.receiver, THIRName)
        assert faces.get("method.protocol")

    def test_dynamic_param_routes_with_dot_accessor(self):
        thir = _lower_ctx(_src(
            "def greet(pet: Pet) -> None:\n    print(pet.make_noise())\n"))
        fn = _fn(thir, "greet")
        assert fn is not None

    def test_readonly_protocol_param_routes(self):
        thir = _lower_ctx(_src(
            "from tpy import readonly as ro\n"
            "def size(m: ro[Measurable]) -> Int32:\n    return m.length()\n"))
        assert _fn(thir, "size") is not None

    def test_protocol_method_arg_takes_free_call_literal_rules(self):
        # A protocol receiver misses `_gen_method_call`'s user-record arg loop,
        # so its literal args render against the slot (the free-call rule): an
        # int literal into a BigInt slot takes the ctor wrap, where the SAME
        # call on a record receiver leaves it bare.
        thir = _lower_ctx(
            "from typing import Protocol\n"
            "from tpy import readonly\n"
            "class Adder(Protocol):\n"
            "    @readonly\n"
            "    def add(self, x: int) -> int: ...\n"
            "class R:\n"
            "    n: int\n"
            "    def __init__(self, n: int) -> None:\n        self.n = n\n"
            "    @readonly\n"
            "    def add(self, x: int) -> int:\n        return self.n + x\n"
            "def via_protocol(a: Adder) -> int:\n    return a.add(2)\n"
            "def via_record(a: R) -> int:\n    return a.add(2)\n")
        assert _emit(_fn(thir, "via_protocol").body[0].value) \
            == "a.add(::tpy::BigInt(2))"
        assert _emit(_fn(thir, "via_record").body[0].value) == "a.add(2)"

    def test_len_of_protocol_binding(self):
        thir, faces = _lower_ctx_witnessed(
            "from typing import Protocol\n"
            "from tpy import Int32\n"
            "class Measurable(Protocol):\n"
            "    def __len__(self) -> Int32: ...\n"
            "def count(items: Measurable) -> Int32:\n    return len(items)\n")
        assert _emit(_fn(thir, "count").body[0].value) == "::tpy::__len__(items)"
        assert faces.get("len.protocol")

    def test_void_protocol_method_in_statement_position(self):
        thir = _lower_ctx(
            "from typing import Protocol\n"
            "from tpy import dynamic\n"
            "@dynamic\n"
            "class Sink(Protocol):\n"
            "    def emit(self) -> None: ...\n"
            "def run(s: Sink) -> None:\n    s.emit()\n")
        fn = _fn(thir, "run")
        assert fn is not None
        stmt = fn.body[0]
        assert isinstance(stmt, THIRExprStmt)
        assert isinstance(stmt.expr, THIRMethodCall)


class TestProtocolArgSlots:
    # `Parrot` conforms to Pet structurally (no C++ base) -> Adapter/RefAdapter;
    # `Dog` inherits it -> the concrete struct binds `Pet&` directly.
    _CONFORMERS = (
        "from typing import Protocol\n"
        "from tpy import Int32, dynamic\n"
        "@dynamic\n"
        "class Pet(Protocol):\n"
        "    def make_noise(self) -> str: ...\n"
        "class Dog(Pet):\n"
        "    def make_noise(self) -> str:\n        return \"Woof\"\n"
        "class Parrot:\n"
        "    def make_noise(self) -> str:\n        return \"Squawk\"\n"
        "def greet(pet: Pet) -> None:\n    print(pet.make_noise())\n"
    )

    def test_inheritance_conformer_rvalue_materializes_concrete_temp(self):
        thir, faces = _lower_ctx_witnessed(
            self._CONFORMERS + "def go() -> None:\n    greet(Dog())\n")
        temp = _fn(thir, "go").body[0].expr.args[0]
        assert temp.cpp_type == "Dog" and temp.brace_init
        assert faces.get("argtemp.protocol")

    def test_inheritance_conformer_lvalue_passes_bare(self):
        thir, faces = _lower_ctx_witnessed(
            self._CONFORMERS
            + "def go() -> None:\n    d = Dog()\n    greet(d)\n")
        assert _emit(_fn(thir, "go").body[1].expr) == "greet(d)"
        assert faces.get("protoarg.bare")

    def test_structural_conformer_rvalue_wraps_in_owning_adapter(self):
        thir = _lower_ctx(
            self._CONFORMERS + "def go() -> None:\n    greet(Parrot())\n")
        temp = _fn(thir, "go").body[0].expr.args[0]
        assert temp.cpp_type == "::tpy::Adapter<Pet, Parrot>" and temp.brace_init

    def test_structural_conformer_lvalue_wraps_in_ref_adapter(self):
        # Zero-copy, but still a temp -- so it needs a flushable position.
        thir = _lower_ctx(
            self._CONFORMERS
            + "def go() -> None:\n    p = Parrot()\n    greet(p)\n")
        temp = _fn(thir, "go").body[1].expr.args[0]
        assert temp.cpp_type == "::tpy::RefAdapter<Pet, Parrot>"
        assert temp.brace_init

    def test_static_protocol_rvalue_hoists_auto_temp(self):
        # A braced-init-list cannot deduce `T_c`, so the rvalue is named first.
        thir = _lower_ctx(
            "from typing import Protocol\n"
            "from tpy import Int32, readonly\n"
            "class Sized(Protocol):\n"
            "    @readonly\n"
            "    def length(self) -> Int32: ...\n"
            "class R:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    @readonly\n"
            "    def length(self) -> Int32:\n        return self.n\n"
            "def use(s: Sized) -> None:\n    print(s.length())\n"
            "def go() -> None:\n    use(R(2))\n")
        temp = _fn(thir, "go").body[0].expr.args[0]
        assert temp.cpp_type is None and not temp.brace_init  # `auto __tmp_N = ...`

    def test_own_iterable_slot_stays_ast(self):
        # `Iterable[Own[T]]` is a forwarding-ref slot whose last-use arg
        # rewrites to `::tpy::own_iter(std::move(x))` inside gen_call_arg --
        # not a protocol pre-arm, and not reproduced here.
        thir = _lower_ctx(
            "from typing import Iterable\n"
            "from tpy import Int32, Own\n"
            "def total(xs: Iterable[Own[Int32]]) -> Int32:\n"
            "    t = 0\n"
            "    for x in xs:\n        t += x\n"
            "    return t\n"
            "def go() -> None:\n"
            "    nums: list[Int32] = [1, 2]\n    print(total(nums))\n")
        # Both sides fall back: the `Iterable[Own[T]]` PARAM is a forwarding-ref
        # slot (`total`), and its call site takes the consuming own_iter (`go`).
        assert _fn(thir, "total") is None
        assert _fn(thir, "go") is None


class TestProtocolMethodCallRejects:
    # `_protocol_method_call_supported`'s own reject exits -- each must fall the
    # whole body back to AST. A routed body that mis-emits one of these would
    # be a silent divergence, so the fallback is pinned rather than left
    # incidental. (These reach the protocol arm; the receiver-shape /
    # arity / fi-kind exits are guarded earlier by `_method_call_receiver_ok`, so
    # a non-name or native-method protocol receiver never reaches this arm --
    # not a gap, just not this arm's job.)
    _P = (
        "from typing import Protocol\n"
        "from tpy import Int32, dynamic\n"
    )

    def test_record_returning_protocol_method_discard_routes(self):
        # A record result is outside the VALUE-position set the arm admits --
        # but discarded in statement position nothing consumes it, so the call
        # renders bare whatever its type. Same reason the record-receiver
        # ladder admits `method.record_discard`.
        src = (
            self._P
            + "class Node:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "@dynamic\n"
            "class Source(Protocol):\n"
            "    def head(self) -> Node: ...\n"
            "def run(s: Source) -> None:\n    s.head()\n")
        assert _fn(_lower_ctx(src), "run") is not None
        _assert_byte_identical(src)

    def test_container_returning_protocol_method_discard_routes(self):
        # The container sibling of the discard row above.
        src = (
            self._P
            + "@dynamic\n"
            "class Source(Protocol):\n"
            "    def items(self) -> list[Int32]: ...\n"
            "def run(s: Source) -> None:\n    s.items()\n")
        assert _fn(_lower_ctx(src), "run") is not None
        _assert_byte_identical(src)


class TestProtocolParamRejects:
    def test_own_protocol_param_routes_arrow(self):
        # `Own[P]` is a unique_ptr slot: not a protocol binding, but the
        # own-dyn receiver family routes it and the member access arrows
        # (`pet->make_noise()`; the dynret wave -- renders pinned in
        # test_thir_wave_dynret).
        src = _src(
            "from tpy import Own\n"
            "def adopt(pet: Own[Pet]) -> None:\n    print(pet.make_noise())\n")
        assert _fn(_lower_ctx(src), "adopt") is not None
        _assert_byte_identical(src)

    def test_optional_protocol_param_stays_ast(self):
        thir = _lower_ctx(_src(
            "def maybe(pet: Pet | None) -> None:\n"
            "    if pet is not None:\n        print(pet.make_noise())\n"))
        assert _fn(thir, "maybe") is None

    def test_protocol_return_routes_bare_param(self):
        # `-> P` is the borrow `P&` slot; a borrow param returns bare
        # (the dynret wave's ret_dyn_borrow row).
        src = _src("def pick(pet: Pet) -> Pet:\n    return pet\n")
        assert _fn(_lower_ctx(src), "pick") is not None
        _assert_byte_identical(src)


class TestDynProtocolDecl:
    def test_dynamic_local_decl_routes_slot_plus_base_pointer(self):
        # `pet: Pet = Dog()` -> `Dog __slot_N{Dog()}; Pet* pet = &__slot_N;`
        # (a DYN_PROTOCOL ptr-slot decl; Dog inherits Pet -> plain concrete slot).
        src = _src(
            "def go() -> None:\n"
            "    pet: Pet = Dog()\n    print(pet.make_noise())\n"
            "def main() -> None:\n    go()\nmain()\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "go")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.DYN_PROTOCOL
        assert decl.base_cpp == "Pet"      # the protocol base pointer type
        assert decl.cpp_type == "Dog"      # direct-inheritance slot (no adapter)
        _assert_byte_identical(src)

    def test_dynamic_reassigned_local_rebinds_via_hoisted_slot(self):
        # `pet: Pet = Dog(); pet = Cat()` -- the first decl keeps the direct
        # init slot; the reseat draws a FRESH function-top-hoisted
        # `std::optional<Cat> __slot_N;` reseated by `__slot_N.emplace(Cat());
        # pet = &*__slot_N;` (the AST's _gen_dynamic_protocol_rebind).
        src = _src(
            "class Cat(Pet):\n"
            "    def make_noise(self) -> str:\n        return \"Meow\"\n"
            "def go() -> None:\n"
            "    pet: Pet = Dog()\n    pet = Cat()\n"
            "    print(pet.make_noise())\n"
            "def main() -> None:\n    go()\nmain()\n")
        fn = _fn(_lower_ctx(src), "go")
        assert fn is not None
        reseat = fn.body[1]
        assert isinstance(reseat, THIRPtrLocalRebind)
        assert reseat.kind is PtrSlotKind.DYN_PROTOCOL
        assert reseat.val_cpp == "Cat"      # direct conformer -> concrete slot
        _assert_byte_identical(src)

    def test_dynamic_structural_conformer_uses_adapter_slot(self):
        # A STRUCTURAL conformer (satisfies Pet without inheriting it) slots
        # into an owning `::tpy::Adapter<Base, Concrete>`, not a bare concrete.
        src = _src(
            "class Cat:\n"
            "    def make_noise(self) -> str:\n        return \"Meow\"\n"
            "def go() -> None:\n"
            "    pet: Pet = Cat()\n    print(pet.make_noise())\n"
            "def main() -> None:\n    go()\nmain()\n")
        decl = _fn(_lower_ctx(src), "go").body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.DYN_PROTOCOL
        assert decl.base_cpp == "Pet"
        assert decl.cpp_type == "::tpy::Adapter<Pet, Cat>"
        _assert_byte_identical(src)

    def test_dynamic_erased_name_source_aliases(self):
        # `p2: Pet = p1` (p1 an erased protocol pointer local) -> `Pet* p2 =
        # &(*p1);` -- alias the same object, no slot.
        src = _src(
            "def go() -> None:\n"
            "    p1: Pet = Dog()\n    p2: Pet = p1\n"
            "    print(p1.make_noise())\n    print(p2.make_noise())\n"
            "def main() -> None:\n    go()\nmain()\n")
        decl = _fn(_lower_ctx(src), "go").body[1]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.DYN_PROTOCOL_ERASED
        _assert_byte_identical(src)

    def test_dynamic_erased_param_source_aliases_bare(self):
        # `q: Pet = pet` where pet is a `Pet&` PARAM (not a pointer local) ->
        # `Pet* q = &pet;` -- no deref (the reference, not a pointer, is aliased).
        src = _src(
            "def keep(pet: Pet) -> None:\n"
            "    q: Pet = pet\n    print(q.make_noise())\n"
            "def go() -> None:\n    keep(Dog())\n"
            "def main() -> None:\n    go()\nmain()\n")
        decl = _fn(_lower_ctx(src), "keep").body[0]
        assert isinstance(decl, THIRPtrLocalDecl)
        assert decl.kind is PtrSlotKind.DYN_PROTOCOL_ERASED
        assert not decl.init.deref      # a `Pet&` param aliases bare (&pet)
        _assert_byte_identical(src)

    def test_dynamic_structural_conformer_reseat_uses_adapter_slot(self):
        # A STRUCTURAL-conformer reseat (`pet = Cat()`, Cat does not inherit Pet)
        # draws an `Adapter<Base, Concrete>` hoisted slot, like the structural decl.
        src = _src(
            "class Cat:\n"
            "    def make_noise(self) -> str:\n        return \"Meow\"\n"
            "def go() -> None:\n"
            "    pet: Pet = Dog()\n    pet = Cat()\n"
            "    print(pet.make_noise())\n"
            "def main() -> None:\n    go()\nmain()\n")
        reseat = _fn(_lower_ctx(src), "go").body[1]
        assert isinstance(reseat, THIRPtrLocalRebind)
        assert reseat.kind is PtrSlotKind.DYN_PROTOCOL
        assert reseat.val_cpp == "::tpy::Adapter<Pet, Cat>"
        _assert_byte_identical(src)


    def test_own_dynamic_protocol_local_routes_erased_decl(self):
        # An inferred `Own[P]` local for a @dynamic P (`x = owning_call()`)
        # takes the heap-owned VALUE decl (`std::unique_ptr<Base> pet =
        # make();` -- the erased-call arm, NOT the Base* stack-slot alias),
        # and the unique_ptr receiver read arrows through the existing
        # own-dyn receiver machinery.
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = _src(
            "from tpy import Own\n"
            "def make() -> Own[Pet]:\n    return Dog()\n"
            "def go() -> None:\n"
            "    pet = make()\n    print(pet.make_noise())\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("decl.dyn_own_erased_call", 0) >= 1


class TestDynProtocolContainerDecl:
    # A @dynamic protocol used as a container type-arg: `Box[Pet]` is F1 (same-
    # module protocol spells its bare name on both paths), and `box.get()`
    # returns a `Pet&` borrow whose `.name()` composes like an F1-record borrow.
    _BOXPROTO = (
        "from typing import Protocol\n"
        "from tpy import dynamic\n"
        "from tplib import Box\n"
        "@dynamic\n"
        "class Pet(Protocol):\n"
        "    def name(self) -> str: ...\n"
        "class Dog(Pet):\n"
        "    def name(self) -> str:\n        return \"Rex\"\n"
    )

    def test_box_of_dynamic_protocol_decl_and_chained_call_route(self):
        # `Box[Pet] b = Box<Dog>(...)` (covariant, F1 protocol type-arg) plus the
        # chained `b.get().name()` -- a protocol method call on a method-call
        # receiver returning a protocol borrow.
        src = (self._BOXPROTO
               + "def go() -> None:\n"
               + "    b: Box[Pet] = Box(Dog())\n    print(b.get().name())\n"
               + "def main() -> None:\n    go()\nmain()\n")
        assert _fn(_lower_ctx(src), "go") is not None
        _assert_byte_identical(src)


class TestDynProtocolBranchHoist:
    def test_branch_declared_dynamic_local_routes(self):
        # A @dynamic local first-declared in both branches predecls the bare
        # protocol base pointer (`Pet* pet;`); each branch assign is a
        # DYN_PROTOCOL rebind through its own fresh function-top slot.
        src = _src(
            "class Cat:\n"
            "    def make_noise(self) -> str:\n        return \"Meow\"\n"
            "def go(c: bool) -> None:\n"
            "    if c:\n        pet: Pet = Dog()\n"
            "    else:\n        pet = Cat()\n"
            "    print(pet.make_noise())\n"
            "def main() -> None:\n    go(True)\nmain()\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "go")
        assert fn is not None
        assert fn.body[0].hoist_decls == (("pet", "Pet*"),)
        reseat = fn.body[0].then_body[0]
        assert isinstance(reseat, THIRPtrLocalRebind)
        assert reseat.kind is PtrSlotKind.DYN_PROTOCOL
        _assert_byte_identical(src)

    def test_branch_declared_dynamic_local_in_generator_rejects(self):
        # The per-reseat slots hoist to function top; resumable leaves have
        # no drain point -- the body stays AST.
        thir = _lower_ctx(_src(
            "from typing import Iterator\n"
            "class Cat:\n"
            "    def make_noise(self) -> str:\n        return \"Meow\"\n"
            "def g(c: bool) -> Iterator[Int32]:\n"
            "    if c:\n        pet: Pet = Dog()\n"
            "    else:\n        pet = Cat()\n"
            "    yield 1\n"))
        assert _fn(thir, "g") is None


class TestBoundedTypeParamFieldRead:
    _SRC = (
        "from tpy import Int32\n"
        "from typing import Protocol\n"
        "class HasValue(Protocol):\n"
        "    value: Int32\n"
        "class Point:\n"
        "    value: Int32\n"
        "    def __init__(self, v: Int32):\n        self.value = v\n"
        "class Wrap[T: HasValue]:\n"
        "    inner: T\n"
        "    def __init__(self, val: T):\n        self.inner = val\n"
        "    def get(self) -> Int32:\n        return self.inner.value\n"
        "def get_value[T: HasValue](item: T) -> Int32:\n"
        "    return item.value\n"
        "def main() -> None:\n"
        "    print(get_value(Point(1)))\n"
        "    print(Wrap(Point(2)).get())\n"
        "main()\n"
    )

    def test_bound_receiver_and_chain_route(self):
        # Inside the template the receiver is `param_val_or_ref_t<T>` -- a
        # value or reference, never a pointer -- and the concept requires the
        # member, so both the bare-name and the through-field receiver spell
        # the plain `.` access.
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "get_value") is not None
        # The through-field receiver (`self.inner.value`) is a SEPARATE
        # predicate, and a fallback there emits byte-identical AST -- so the
        # method has to be looked up by name, not inferred from the diff.
        assert _fn(thir, "get") is not None
        _assert_byte_identical(self._SRC)

    def test_row_requires_a_protocol_bound(self):
        # The row keys on the BOUND, not on "is a type param": an unbounded
        # T has no concept requiring the member (sema rejects the read), so
        # the predicate must not admit one.
        from .lower.predicates import _bounded_tparam_protocol
        from ..typesys import TypeParamRef
        assert _bounded_tparam_protocol(TypeParamRef("T"), {}) is None


class TestCopyIterOwnElemArg:
    """A CopyIter[T] value at the stub's Iterable[Own[T]] slot binds the
    monomorphized template param bare -- a NAME as an lvalue, a
    copy_iter(..) rvalue inline through its special-builtin arm."""

    _SRC = (
        "from tpy import Int32, copy_iter\n"
        "class Node:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n        self.val = val\n"
    )

    def test_copy_iter_name_and_rvalue_route(self):
        src = self._SRC + (
            "def f() -> None:\n"
            "    a: list[Node] = []\n"
            "    b: list[Node] = [Node(1)]\n"
            "    ci = copy_iter(b)\n"
            "    a.extend(ci)\n"
            "    a.extend(copy_iter(b))\n"
            "    print(len(a))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("protoarg.copy_iter", 0) >= 2
        _assert_byte_identical(src)

    def test_own_iter_name_at_own_elem_slot_still_defers(self):
        # Boundary: the SIBLING adapter -- an own_iter-bound NAME at the
        # same slot must not be swept in by the CopyIter qname check (the
        # OwnIter flavor moves via the consuming rewrite, never binds bare).
        src = (
            "from tpy import Int32, own_iter\n"
            "class Node:\n"
            "    val: Int32\n"
            "    def __init__(self, val: Int32) -> None:\n        self.val = val\n"
            "def f() -> None:\n"
            "    a: list[Node] = []\n"
            "    b: list[Node] = [Node(1)]\n"
            "    oi = own_iter(b)\n"
            "    a.extend(oi)\n"
            "    print(len(a))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_user_conformer_at_own_elem_slot_still_defers(self):
        # Boundary: a USER iterator record at the same slot is not the
        # CopyIter adapter -- the body keeps its existing fallback.
        src = (
            "from tpy import Int32\n"
            "from typing import Iterator\n"
            "class Gen3:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n        self.n = 0\n"
            "    def __iter__(self) -> Iterator[Int32]:\n"
            "        i = 0\n"
            "        while i < 3:\n"
            "            yield i\n"
            "            i += 1\n"
            "def f() -> None:\n"
            "    a: list[Int32] = []\n"
            "    g = Gen3()\n"
            "    a.extend(g)\n"
            "    print(len(a))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)


class TestAssertIsinstanceSelf:
    """`assert isinstance(self, Dog)` -- the fresh-cast reference-local path:
    the negated null-check + a persistent `const Dog& __self = *dynamic_cast
    <const Dog*>(this);` alias, self reads renaming through the spelled map
    (`__self.bark()`)."""

    _SRC = (
        "from typing import Protocol\n"
        "from tpy import Int32, dynamic, readonly\n"
        "@dynamic\n"
        "class Tagged(Protocol):\n    pass\n"
        "class Pet(Tagged):\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "    @readonly\n"
        "    def assert_dog(self) -> Int32:\n"
        "        assert isinstance(self, Dog)\n"
        "        return self.bark()\n"
        "class Dog(Pet):\n"
        "    def __init__(self, n: Int32) -> None:\n        super().__init__(n)\n"
        "    @readonly\n"
        "    def bark(self) -> Int32:\n        return self.n + 100\n"
        "def main() -> None:\n"
        "    print(Dog(1).assert_dog())\n"
        "main()\n")

    def test_assert_self_narrows_with_spelled_alias(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        f = _fn(thir, "assert_dog")
        assert f is not None
        buf = io.StringIO()
        emit_thir_body(buf, f)
        body = buf.getvalue()
        assert ("const Dog& __self = *dynamic_cast<const Dog*>(this);"
                in body)
        assert "__self.bark()" in body
        assert faces.get("narrow.dyn_assert", 0) >= 1
        _assert_byte_identical(self._SRC)

    def test_assert_self_in_generator_still_defers(self):
        # Boundary: the resumable flavor stays out (the leaf-mode guard --
        # a frame's alias would need the frame-field rename); the assert
        # body falls back whole.
        src = (
            "from typing import Iterator, Protocol\n"
            "from tpy import Int32, dynamic, readonly\n"
            "@dynamic\n"
            "class Tagged(Protocol):\n    pass\n"
            "class Pet(Tagged):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "    def gen_assert(self) -> Iterator[Int32]:\n"
            "        assert isinstance(self, Dog)\n"
            "        yield self.bark()\n"
            "class Dog(Pet):\n"
            "    def __init__(self, n: Int32) -> None:\n        super().__init__(n)\n"
            "    @readonly\n"
            "    def bark(self) -> Int32:\n        return self.n + 100\n"
            "def main() -> None:\n"
            "    for v in Dog(1).gen_assert():\n        print(v)\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "gen_assert") is None
        _assert_byte_identical(src)
