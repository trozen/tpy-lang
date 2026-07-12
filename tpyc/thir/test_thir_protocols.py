"""THIR protocol boundary: bare protocol params (structural + @dynamic) and
method calls dispatched on a protocol receiver."""

from __future__ import annotations

from .nodes import THIRExprStmt, THIRMethodCall, THIRName, THIRReturn
from .testutil import _emit_expr as _emit, _fn, _lower_ctx, _lower_ctx_witnessed

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

    def test_record_returning_protocol_method_stays_ast(self):
        # A record result is outside the value-position set the arm admits;
        # discarded in statement position so the RECEIVER is the bare protocol
        # name (not a field chain, which would reject earlier).
        thir = _lower_ctx(
            self._P
            + "class Node:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "@dynamic\n"
            "class Source(Protocol):\n"
            "    def head(self) -> Node: ...\n"
            "def run(s: Source) -> None:\n    s.head()\n")
        assert _fn(thir, "run") is None

    def test_container_returning_protocol_method_stays_ast(self):
        thir = _lower_ctx(
            self._P
            + "@dynamic\n"
            "class Source(Protocol):\n"
            "    def items(self) -> list[Int32]: ...\n"
            "def run(s: Source) -> None:\n    s.items()\n")
        assert _fn(thir, "run") is None


class TestProtocolParamRejects:
    def test_own_protocol_param_stays_ast(self):
        # `Own[P]` is a `T_p&&` / unique_ptr slot: its reads move and its
        # method calls render `->`. Not a protocol binding.
        thir = _lower_ctx(_src(
            "from tpy import Own\n"
            "def adopt(pet: Own[Pet]) -> None:\n    print(pet.make_noise())\n"))
        assert _fn(thir, "adopt") is None

    def test_optional_protocol_param_stays_ast(self):
        thir = _lower_ctx(_src(
            "def maybe(pet: Pet | None) -> None:\n"
            "    if pet is not None:\n        print(pet.make_noise())\n"))
        assert _fn(thir, "maybe") is None

    def test_protocol_return_stays_ast(self):
        thir = _lower_ctx(_src(
            "def pick(pet: Pet) -> Pet:\n    return pet\n"))
        assert _fn(thir, "pick") is None
