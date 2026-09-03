"""The VALUE-record return slot's method-call rvalue row.

`-> Stamp` on a `ValueType` record is spelled like a borrow return
(`prescan.ret_record_borrow` is set) but the C++ slot returns BY VALUE, so a
method-call rvalue source renders the bare call through the generic tail --
the `Own[Rec]` storage arm's render, not the `T&` passthrough the borrow block
emits. The stdlib witnesses are datetime's `now` / `today` / `utcnow` /
`fromtimestamp` / `utcfromtimestamp`.

Also pins the two adjacent rows the widening exposed: the raise-terminated
post-if narrow alias (which had mirrored the AST's return-arm only) and
`self` at a value-union arg slot.
"""
from ..codegen_cpp import CodeGenOptions
from .testutil import (
    _assert_byte_identical, _assert_rejects_at,
    _assert_routes_byte_identical, _compile, _entry, _fn,
    _lower_ctx_witnessed, _thir_ctx,
)

_STAMP = (
    "from tpy import Int32, ValueType\n"
    "class Stamp(ValueType):\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "    @staticmethod\n"
    "    def make(n: Int32) -> \"Stamp\":\n"
    "        return Stamp(n)\n"
)


class TestValueRecordMethodCallReturn:
    def test_class_qualified_static_routes(self):
        # The drilled shape: `return datetime.now()` -- a class-qualified
        # static method call at the value-record slot.
        src = _STAMP + (
            "    @staticmethod\n"
            "    def zero() -> \"Stamp\":\n"
            "        return Stamp.make(0)\n"
            "def free_zero() -> Stamp:\n"
            "    return Stamp.zero()\n"
            "def use() -> None:\n"
            "    print(Stamp.zero().n)\n"
            "    print(free_zero().n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return Stamp::make(0);" in hpp + cpp
        assert "return Stamp::zero();" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_methodcall_value"] >= 1

    def test_instance_receiver_and_chain_route(self):
        # The neighbour receivers sharing the row: an instance receiver, a
        # chained call, a field receiver, and the @readonly twin.
        src = (
            "from tpy import Int32, ValueType, readonly\n"
            "class Stamp(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def plus(self, k: Int32) -> \"Stamp\":\n"
            "        return Stamp(self.n + k)\n"
            "    def twice(self) -> \"Stamp\":\n"
            "        return self.plus(self.n)\n"
            "    def chained(self) -> \"Stamp\":\n"
            "        return self.plus(1).plus(2)\n"
            "class Holder(ValueType):\n"
            "    s: Stamp\n"
            "    def __init__(self, s: Stamp) -> None:\n"
            "        self.s = s\n"
            "    def bump(self) -> Stamp:\n"
            "        return self.s.plus(1)\n"
            "    @readonly\n"
            "    def bump_ro(self) -> Stamp:\n"
            "        return self.s.plus(2)\n"
            "def use() -> None:\n"
            "    print(Stamp(1).twice().n)\n"
            "    print(Stamp(1).chained().n)\n"
            "    h = Holder(Stamp(5))\n"
            "    print(h.bump().n)\n"
            "    print(h.bump_ro().n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return this->plus(this->n);" in hpp + cpp
        assert "return this->s.plus(1);" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_methodcall_value"] >= 1

    def test_arg_temp_flushes_at_the_return(self):
        # The astimezone shape, and the reason the row rides the generic tail
        # rather than the borrow block: a member-valued union arg hoists a
        # `__tmp_N` that only a flushable value position can place.
        src = (
            "from tpy import Int32, ValueType\n"
            "class Zone(ValueType):\n"
            "    off: Int32\n"
            "    def __init__(self, off: Int32) -> None:\n"
            "        self.off = off\n"
            "class Fixed(ValueType):\n"
            "    k: Int32\n"
            "    def __init__(self, k: Int32) -> None:\n"
            "        self.k = k\n"
            "class Stamp(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    @staticmethod\n"
            "    def at(n: Int32, tz: Zone | Fixed | None = None) -> \"Stamp\":\n"
            "        return Stamp(n)\n"
            "    @staticmethod\n"
            "    def local(n: Int32) -> \"Stamp\":\n"
            "        return Stamp.at(n, Zone(60))\n"
            "def use() -> None:\n"
            "    print(Stamp.local(4).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        both = hpp + cpp
        assert "__tmp_1 = Zone(60);" in both
        assert "return Stamp::at(n, __tmp_1);" in both
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_methodcall_value"] >= 1

    def test_borrow_returning_call_keeps_its_own_row(self):
        # BOUNDARY: a NON-value record's `T&` slot is not value-typed, so the
        # new arm declines it -- the `T&`-returning method call keeps riding
        # `ret.record_call_borrow` (a call rvalue could never bind there).
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class Bag:\n"
            "    b: Box\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.b = Box(n)\n"
            "    def get(self) -> Box:\n"
            "        return self.b\n"
            "    def relay(self) -> Box:\n"
            "        return self.get()\n"
            "    def make(self) -> Own[Box]:\n"
            "        return Box(9)\n"
            "def use() -> None:\n"
            "    bag = Bag(3)\n"
            "    print(bag.relay().n)\n"
            "    print(bag.make().n)\n"
            "use()\n"
        )
        _assert_routes_byte_identical(src)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("ret.record_methodcall_value", 0) == 0

    def test_own_storage_slot_keeps_its_own_row(self):
        # BOUNDARY: the `Own[Rec]` STORAGE direction stays on
        # `ret.record_methodcall`; the value-record row must not shadow it.
        src = (
            "from tpy import Int32, ValueType, Own\n"
            "class Pair(ValueType):\n"
            "    a: Int32\n"
            "    b: Int32\n"
            "    def __init__(self, a: Int32, b: Int32) -> None:\n"
            "        self.a = a\n"
            "        self.b = b\n"
            "    @staticmethod\n"
            "    def zero() -> \"Pair\":\n"
            "        return Pair(0, 0)\n"
            "class Node:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "    @staticmethod\n"
            "    def make(v: Int32) -> Own[\"Node\"]:\n"
            "        return Node(v)\n"
            "def own_slot() -> Own[Node]:\n"
            "    return Node.make(4)\n"
            "def gen_slot() -> Pair:\n"
            "    return Pair.zero()\n"
            "def use() -> None:\n"
            "    print(own_slot().v)\n"
            "    print(gen_slot().a)\n"
            "use()\n"
        )
        _assert_routes_byte_identical(src)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_methodcall"] >= 1
        assert faces["ret.record_methodcall_value"] >= 1


class TestRaiseGuardPostIfAlias:
    def test_raise_terminated_guard_emits_the_alias(self):
        # The post-if fact reader had mirrored the AST's return-terminated arm
        # only, while `_gen_if` (and `_poly_post_if_fact`) accept a raise too:
        # the guard leaves the subject narrowed for the rest of the body, so
        # the persistent extraction alias is emitted at the enclosing scope.
        src = (
            "from tpy import Int32, ValueType\n"
            "class B(ValueType):\n"
            "    m: Int32\n"
            "    def __init__(self, m: Int32) -> None:\n"
            "        self.m = m\n"
            "class A(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def take(v: A | B) -> Int32:\n"
            "    if not isinstance(v, A):\n"
            "        raise ValueError(\"nope\")\n"
            "    return v.n + 1\n"
            "def use() -> None:\n"
            "    print(take(A(3)))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto& __v = std::get<A>(v);" in hpp + cpp
        thir, _faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "take") is not None


class TestSelfAtValueUnionArg:
    """`self` at a value-union arg slot: the receiver read in a value
    position is already the DEREF'd `(*this)`, so the value-union arg temp
    is initialized from the object exactly as for any other member-valued
    source. The free-call and qualified-call gates read one verdict, so both
    admit together."""

    def test_self_into_a_value_union_slot_routes(self):
        src = (
            "from tpy import Int32, ValueType\n"
            "class B(ValueType):\n"
            "    m: Int32\n"
            "    def __init__(self, m: Int32) -> None:\n"
            "        self.m = m\n"
            "class A(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def pass_self(self) -> Int32:\n"
            "        return sink(self)\n"
            "def sink(v: A | B | None) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        return v.n\n"
            "    return 0\n"
            "def use() -> None:\n"
            "    print(A(3).pass_self())\n"
            "use()\n"
        )
        _assert_routes_byte_identical(src)
        compiler, modules = _compile(src)
        hpp, _ = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(emit_source_comments=False))
        assert "__tmp_1 = (*this);" in hpp
        assert "return sink(__tmp_1);" in hpp

    def test_self_into_a_method_value_union_slot_routes(self):
        # The METHOD-call flavor of the same admission (datetime's
        # `ZoneInfo.fromutc` -> `datetime._from_epoch_us(.., self)`).
        src = (
            "from tpy import Int32, ValueType\n"
            "class B(ValueType):\n"
            "    m: Int32\n"
            "    def __init__(self, m: Int32) -> None:\n"
            "        self.m = m\n"
            "class Sink(ValueType):\n"
            "    k: Int32\n"
            "    def __init__(self, k: Int32) -> None:\n"
            "        self.k = k\n"
            "    @staticmethod\n"
            "    def of(v: \"B | Sink | None\") -> Int32:\n"
            "        if isinstance(v, B):\n"
            "            return v.m\n"
            "        return 0\n"
            "    def pass_self(self) -> Int32:\n"
            "        return Sink.of(self)\n"
            "def use() -> None:\n"
            "    print(Sink(3).pass_self())\n"
            "use()\n"
        )
        _assert_routes_byte_identical(src)
        compiler, modules = _compile(src)
        hpp, _ = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(emit_source_comments=False))
        assert "__tmp_1 = (*this);" in hpp
        assert "return Sink::of(__tmp_1);" in hpp

    def test_member_named_arg_still_takes_the_temp(self):
        # The row the reject must NOT swallow: an ordinary member-typed NAME
        # (and a member ctor rvalue) at the same value-union slot still hoists
        # the temp and routes.
        src = (
            "from tpy import Int32, ValueType\n"
            "class B(ValueType):\n"
            "    m: Int32\n"
            "    def __init__(self, m: Int32) -> None:\n"
            "        self.m = m\n"
            "class A(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def sink(v: A | B | None) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        return v.n\n"
            "    return 0\n"
            "def use() -> None:\n"
            "    a = A(3)\n"
            "    print(sink(a))\n"
            "    print(sink(B(4)))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        both = hpp + cpp
        assert "__tmp_1 = a;" in both
        assert "__tmp_2 = B(4);" in both
