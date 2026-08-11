"""Pins for the value-repr Optional[Callable] arg rows
(`_value_opt_callable_pass_arg` + the marker-ladder func-ref row).

The routed slice: a WHOLE `Callable[..] | None` binding passed bare into a
matching value-opt slot (free / record-method / marker ladders), and a
func-ref name at a marker call's slot. The narrowed read (`cb(x)` under
`cb is not None`) derefs on the AST path and must keep falling back."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_PRELUDE = (
    "from typing import Callable\n"
    "from tpy import Int32\n"
)


class TestValueOptCallablePassArg:
    def test_whole_opt_name_free_arg_routes(self):
        src = _PRELUDE + (
            "def takes_opt(cb: Callable[[Int32], None] | None) -> bool:\n"
            "    return cb is None\n"
            "def forward(cb: Callable[[Int32], None] | None) -> bool:\n"
            "    return takes_opt(cb)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "forward") is not None
        assert w.get("arg.value_opt_callable", 0) >= 1
        _assert_byte_identical(src)

    def test_whole_opt_name_method_arg_routes(self):
        src = _PRELUDE + (
            "class Sink:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 0\n"
            "    def set(self, cb: Callable[[Int32], None] | None) -> None:\n"
            "        self.n += 1\n"
            "def forward(s: Sink, cb: Callable[[Int32], None] | None"
            ") -> None:\n"
            "    s.set(cb)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "forward") is not None
        assert w.get("arg.value_opt_callable", 0) >= 1
        _assert_byte_identical(src)

    def test_marker_funcref_and_whole_opt_route(self):
        # The marker-ladder rows: os.walk's `Callable | None` slot taking a
        # func-ref (`boom`) and a whole-opt param (`cb`) -- the
        # os_walk_onerror corpus shape reduced.
        src = (
            "import os\n"
            "from typing import Callable\n"
            "from tpy import Int32, readonly\n"
            "def report(e: readonly[OSError]) -> None:\n"
            "    print(\"x\")\n"
            "def walk_n(top: str,"
            " cb: Callable[[readonly[OSError]], None] | None) -> Int32:\n"
            "    n = 0\n"
            "    for dirpath, dirnames, filenames in"
            " os.walk(top, onerror=cb):\n"
            "        n += 1\n"
            "    return n\n"
            "def walk_ref(top: str) -> Int32:\n"
            "    n = 0\n"
            "    for dirpath, dirnames, filenames in"
            " os.walk(top, onerror=report):\n"
            "        n += 1\n"
            "    return n\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "walk_n") is not None
        assert _fn(thir, "walk_ref") is not None
        assert w.get("arg.value_opt_callable", 0) >= 1
        _assert_byte_identical(src)


class TestValueOptViewWholeMethodArg:
    _SINK = (
        "class Sink:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 0\n"
        "    def put(self, body: bytes | None) -> None:\n"
        "        self.n += 1\n"
        "    def tag(self, label: str | None) -> None:\n"
        "        self.n += 1\n")

    def test_whole_opt_view_method_args_route(self):
        src = _PRELUDE + self._SINK + (
            "def fwd(s: Sink, body: bytes | None, label: str | None"
            ") -> None:\n"
            "    s.put(body)\n"
            "    s.tag(label)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "fwd") is not None
        assert w.get("method.optview_whole_arg", 0) >= 2
        _assert_byte_identical(src)

    def test_free_call_position_keeps_the_shim(self):
        # The free-call slot threading takes `_maybe_convert_opt_view_param`'s
        # ARG split -- the method row must not capture it (byte-identity
        # proves the shim still renders).
        src = _PRELUDE + (
            "def take(body: bytes | None) -> bool:\n"
            "    return body is None\n"
            "def fwd(body: bytes | None) -> bool:\n"
            "    return take(body)\n")
        _assert_byte_identical(src)


class TestMarkerContainerFieldAndOwnTparamArgs:
    _HOLDER = (
        "import heapq\n"
        "from tpy import Int32, ValueType\n"
        "class Entry(ValueType):\n"
        "    k: Int32\n"
        "    def __init__(self, k: Int32) -> None:\n"
        "        self.k = k\n"
        "    def __lt__(self, o: 'Entry') -> bool:\n"
        "        return self.k < o.k\n"
        "class Holder:\n"
        "    heap: list[Entry]\n"
        "    def __init__(self) -> None:\n"
        "        self.heap = []\n")

    def test_container_field_and_ctor_rvalue_route(self):
        # The item slot arrives SUBSTITUTED (`Own[Entry]`) from sema, so the
        # ctor rvalue rides the existing own.record_rvalue row; the new
        # admission is the container FIELD at the heap slot.
        src = self._HOLDER + (
            "    def push(self, k: Int32) -> None:\n"
            "        heapq.heappush(self.heap, Entry(k))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "push") is not None
        assert w.get("arg.container_field_marker", 0) >= 1
        assert w.get("own.record_rvalue", 0) >= 1
        _assert_byte_identical(src)

    def test_narrowed_optional_container_field_still_defers(self):
        # The declared-type guard: a narrowed Optional[list] FIELD types its
        # occurrence as a plain container, but the AST unwraps the read --
        # the bare-field row must stay off it (byte-identity would break if
        # it routed bare).
        src = (
            "import heapq\n"
            "from tpy import Int32\n"
            "class Holder:\n"
            "    maybe: list[Int32] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.maybe = None\n"
            "    def fix(self) -> None:\n"
            "        if self.maybe is not None:\n"
            "            heapq.heapify(self.maybe)\n")
        _assert_byte_identical(src)


class TestOwnTparamMethodRvalueArg:
    def test_open_own_slot_same_t_method_rvalue_routes(self):
        # `self._st.init(ui, other._st.take(ui))` inside a generic record
        # body: the Own[T] prvalue binds the open Own[T] slot bare.
        src = (
            "from __future__ import annotations\n"
            "from tpy import UInt32, Own\n"
            "from tpy.mem import UninitArrayStorage\n"
            "class Rack[T, N: int]:\n"
            "    _st: UninitArrayStorage[T, N]\n"
            "    _n: UInt32\n"
            "    def __init__(self) -> None:\n"
            "        self._st = UninitArrayStorage[T, N]()\n"
            "        self._n = UInt32(0)\n"
            "    def __del__(self) -> None:\n"
            "        self._st.drop_n(UInt32(0), self._n)\n"
            "    def __move__(self, other: Own[Rack[T, N]]) -> None:\n"
            "        for ui in range(other._n):\n"
            "            self._st.init(ui, other._st.take(ui))\n"
            "        self._n = other._n\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "__move__") is not None
        assert w.get("arg.own_tparam_method_rvalue", 0) >= 1
        _assert_byte_identical(src)

    def test_by_value_open_t_return_adjacent_shape(self):
        # The adjacent shape at the same slot: a T-returning (by-value)
        # method result forwarded into the open Own[T] slot. It classifies
        # as an rvalue source too and renders bare on both paths -- pinned
        # byte-identical so a future is_rvalue_source change here surfaces.
        # The true negatives (a mismatched T, a genuine C++-ref return at a
        # same-T Own slot) are sema-rejected and cannot be pinned.
        src = (
            "from __future__ import annotations\n"
            "from tpy import UInt32, Own\n"
            "from tpy.mem import UninitArrayStorage\n"
            "class Rack[T, N: int]:\n"
            "    _st: UninitArrayStorage[T, N]\n"
            "    _n: UInt32\n"
            "    def __init__(self) -> None:\n"
            "        self._st = UninitArrayStorage[T, N]()\n"
            "        self._n = UInt32(0)\n"
            "    def __del__(self) -> None:\n"
            "        self._st.drop_n(UInt32(0), self._n)\n"
            "    def peek(self, i: UInt32) -> T:\n"
            "        return self._st.load(i)\n"
            "    def dup_first(self) -> None:\n"
            "        self._st.init(self._n, self.peek(UInt32(0)))\n"
            "        self._n += 1\n")
        _assert_byte_identical(src)


class TestBuiltinSetattrStatement:
    _BAG = (
        "from typing import Any\n"
        "class Bag:\n"
        "    _data: dict[str, Any]\n"
        "    def __init__(self) -> None:\n"
        "        d: dict[str, Any] = {}\n"
        "        self._data = d\n"
        "    def __setattr__(self, name: str, value: Any) -> None:\n"
        "        self._data[name] = value\n")

    def test_builtin_setattr_literal_values_route(self):
        # `setattr(b, "name", "alice")` delegates its synthesized
        # `__setattr__` call to the dynamic-attr write mirror.
        src = self._BAG + (
            "def use(b: Bag) -> None:\n"
            "    setattr(b, \"name\", \"alice\")\n"
            "    setattr(b, \"age\", 30)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("method.dyn_setattr", 0) >= 2
        _assert_byte_identical(src)

    def test_runtime_name_setattr_keeps_the_general_arm(self):
        # `setattr(h, name, value)` with a RUNTIME name and an uncoerced str
        # value routes through the GENERAL method arm -- the delegation's
        # literal-shape key must not capture it (the mirror rejects runtime
        # names, so capturing this shape turns a routed body into fallback).
        src = (
            "class Headers:\n"
            "    _n: str\n"
            "    _v: str\n"
            "    def __init__(self) -> None:\n"
            "        self._n = \"\"\n"
            "        self._v = \"\"\n"
            "    def __setattr__(self, name: str, value: str) -> None:\n"
            "        self._n = name\n"
            "        self._v = value\n"
            "def set_it(h: Headers, name: str, value: str) -> None:\n"
            "    setattr(h, name, value)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "set_it") is not None
        _assert_byte_identical(src)

    def test_builtin_getattr_routes_via_the_dyn_mirror(self):
        # 2-arg getattr: literal and runtime names both delegate to the
        # result-blind dyn-attr read mirror (the general arm's result gate
        # rejects the owned-str / Any dunder returns).
        src = (
            "class Headers:\n"
            "    def __getattr__(self, name: str) -> str:\n"
            "        if name == \"host\":\n"
            "            return \"example.com\"\n"
            "        raise AttributeError(name)\n"
            "def lookup(h: Headers, name: str) -> str:\n"
            "    return getattr(h, name)\n"
            "def lit(h: Headers) -> str:\n"
            "    return getattr(h, \"host\")\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "lookup") is not None
        assert _fn(thir, "lit") is not None
        assert w.get("call.dyn_getattr_builtin", 0) >= 2
        _assert_byte_identical(src)

    def test_builtin_setattr_nonliteral_value_still_defers(self):
        # A non-literal value takes a value-dependent make_any render -- the
        # mirror's literal pin must keep rejecting it.
        src = self._BAG + (
            "def use(b: Bag, v: str) -> None:\n"
            "    setattr(b, \"who\", v)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestOwnedElementLiteralAppends:
    def test_bytes_and_value_tuple_literal_appends_route(self):
        src = (
            "from tpy import Int32\n"
            "def use() -> None:\n"
            "    bs: list[bytes] = []\n"
            "    bs.append(b\"xyz\")\n"
            "    ps: list[tuple[str, Int32]] = []\n"
            "    ps.append((\"k\", 9))\n"
            "    print(len(bs) + len(ps))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("arg.bytes_owned_literal", 0) >= 1
        assert w.get("arg.own_value_tuple_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_view_form_bytes_name_takes_s6_convert(self):
        # The S6 witness arrived: a VIEW-resolved bytes local (a
        # literal-seeded span binding) at the Own[bytes] element slot takes
        # the `::tpy::bytes_copy(v)` materialize convert.
        src = (
            "def use() -> None:\n"
            "    bs: list[bytes] = []\n"
            "    v = b\"name\"\n"
            "    bs.append(v)\n"
            "    print(len(bs))\n"
            "def main() -> None:\n"
            "    use()\n"
            "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("arg.own_bytes_slot", 0) == 1
        cpp = _assert_byte_identical(src)
        assert "bs.push_back(::tpy::bytes_copy(v));" in cpp[1]

    def test_tuple_name_at_own_element_slot_still_defers(self):
        # A tuple NAME still needs the move cascade -- the literal-only row
        # must not capture it.
        src = (
            "from tpy import Int32\n"
            "def use2() -> None:\n"
            "    ps: list[tuple[str, Int32]] = []\n"
            "    t = (\"m\", 3)\n"
            "    ps.append(t)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use2") is None
        _assert_byte_identical(src)

    def test_pointer_repr_tuple_literal_append_routes(self):
        # The pointer-repr element tuple takes the tuple_to_storage_move
        # lift -- the consuming borrow-tuple row renders it (never the
        # value arm; the witness keys the distinction).
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def use() -> None:\n"
            "    pairs: list[tuple[P | None, P | None]] = []\n"
            "    pairs.append((P(5), None))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("arg.own_btuple_literal", 0) >= 1
        assert w.get("arg.own_value_tuple_literal", 0) == 0
        _assert_byte_identical(src)


class TestRecordSetReceiver:
    def test_record_element_set_insert_routes(self):
        src = (
            "from tpy import Int32, UInt64\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "    def __hash__(self) -> UInt64:\n"
            "        return UInt64(self.x)\n"
            "    def __eq__(self, other: \"Point\") -> bool:\n"
            "        return self.x == other.x\n"
            "def use() -> None:\n"
            "    s: set[Point] = set()\n"
            "    s.add(Point(1))\n"
            "    print(len(s))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestProtocolOwnStorageReturn:
    _SRC = (
        "from typing import Protocol\n"
        "from tpy import Int32, Own\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class Factory(Protocol):\n"
        "    def create(self, x: Int32) -> Own[Point]: ...\n"
        "class DefaultFactory:\n"
        "    def create(self, x: Int32) -> Own[Point]:\n"
        "        return Point(x)\n")

    def test_own_record_return_routes_at_return_slot(self):
        src = self._SRC + (
            "def ret_pos[T: Factory](f: T, x: Int32) -> Own[Point]:\n"
            "    return f.create(x)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "ret_pos") is not None
        assert w.get("method.protocol_own_storage_ret", 0) >= 1
        _assert_byte_identical(src)

    def test_value_position_still_defers(self):
        # A postfix read off the rvalue is a VALUE position -- the storage
        # admission must not capture it.
        src = self._SRC + (
            "def value_pos[T: Factory](f: T, x: Int32) -> Int32:\n"
            "    return f.create(x).x\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "value_pos") is None
        _assert_byte_identical(src)


class TestScalarReceiverStubs:
    def test_as_integer_ratio_unpack_and_print_route(self):
        src = (
            "def use() -> None:\n"
            "    x = 12.5\n"
            "    num, den = x.as_integer_ratio()\n"
            "    print(num, den)\n"
            "    n = int(7)\n"
            "    print(n.as_integer_ratio())\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("method.scalar_tuple_ret", 0) >= 1
        _assert_byte_identical(src)

    def test_ptr_tuple_call_print_still_defers(self):
        # The kind-keyed TuplePrinter wrap is VALUE tuples only -- a
        # pointer-repr tuple call result keeps its borrow machinery and the
        # body defers at its own gates.
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def ptr_pair(a: P, b: P) -> tuple[P, P]:\n"
            "    return (a, b)\n"
            "def show(a: P, b: P) -> None:\n"
            "    print(ptr_pair(a, b)[0].v)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "show") is None
        _assert_byte_identical(src)


class TestOpenTStubResult:
    def test_generic_record_container_pop_return_routes(self):
        src = (
            "class Stack[T]:\n"
            "    items: list[T]\n"
            "    def __init__(self) -> None:\n"
            "        self.items = list[T]()\n"
            "    def pop(self) -> T:\n"
            "        return self.items.pop()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "pop") is not None
        _assert_byte_identical(src)


class TestNativePropertyContainerArg:
    _SRC = (
        "from tpy import Int32, readonly\n"
        "class Foo:\n"
        "    _items: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self._items = [1, 2, 3]\n"
        "    @property\n"
        "    def items(self) -> list[Int32]:\n"
        "        return self._items\n")

    def test_len_of_container_property_routes(self):
        src = self._SRC + (
            "@readonly\n"
            "def count(f: Foo) -> Int32:\n"
            "    return len(f.items)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "count") is not None
        assert w.get("arg.native_property_container", 0) >= 1
        _assert_byte_identical(src)

    def test_alias_decl_of_property_still_defers(self):
        # `xs = f.items` binds a REF_ALIAS on the AST path -- the len-arg
        # arm is native-slot-scoped and must not touch the decl sink, where
        # a bare copy would break aliasing.
        src = self._SRC + (
            "def alias_mutate(f: Foo) -> None:\n"
            "    xs = f.items\n"
            "    xs.append(9)\n"
            "    print(len(f.items))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "alias_mutate") is None
        _assert_byte_identical(src)


class TestBytesSplitIterableAndValueOptResults:
    def test_bytes_split_for_head_routes(self):
        src = (
            "def split_iter(sent: bytes) -> None:\n"
            "    for line in sent.split(b\"|\"):\n"
            "        if line.startswith(b\"k\"):\n"
            "            print(line)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "split_iter") is not None
        _assert_byte_identical(src)

    def test_value_opt_method_results_at_decl_route(self):
        # `host = full.hostname` / `port = full.port` reduced: value-opt
        # owned-view and scalar results land bare in their decl slots.
        src = (
            "from tpy import Int32\n"
            "class Rec:\n"
            "    _h: str | None\n"
            "    _p: Int32 | None\n"
            "    def __init__(self) -> None:\n"
            "        self._h = None\n"
            "        self._p = None\n"
            "    @property\n"
            "    def hostname(self) -> str | None:\n"
            "        return self._h\n"
            "    @property\n"
            "    def port(self) -> Int32 | None:\n"
            "        return self._p\n"
            "def use(r: Rec) -> None:\n"
            "    host = r.hostname\n"
            "    port = r.port\n"
            "    print(host is None, port is None)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("method.value_opt_view_ret", 0) >= 1
        _assert_byte_identical(src)


class TestPtrTemplateSpanMethod:
    def test_ptr_span_routes(self):
        src = (
            "from tpy import Int32, Ptr, readonly\n"
            "def read_span(p: Ptr[readonly[Int32]], n: Int32) -> Int32:\n"
            "    s = p.span(n)\n"
            "    return len(s)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "read_span") is not None
        assert w.get("method.ptr_template_span", 0) >= 1
        _assert_byte_identical(src)

    def test_nonscalar_template_arg_still_defers(self):
        # The scalar-arg admission's boundary: a str arg at a @cpp_template
        # native-record method keeps deferring.
        src = (
            "from tpy import Int32\n"
            "from tpy.extern import native, cpp_template\n"
            "@native(\"std::vector\")\n"
            "class Vec[T]:\n"
            "    @cpp_template(\"{self}.emplace_back({0})\")\n"
            "    def addn(self, s: str) -> None: ...\n"
            "def use() -> None:\n"
            "    v: Vec[str] = Vec[str]()\n"
            "    v.addn(\"x\")\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestNativeTemplateAndRetCast:
    _VEC = (
        "from tpy import Int32, UInt64, readonly, pure\n"
        "from tpy.extern import native, cpp_template\n"
        "@native(\"std::vector\")\n"
        "class Vec[T]:\n"
        "    @native(\"push_back\")\n"
        "    def add(self, value: T) -> None: ...\n"
        "    @property\n"
        "    @cpp_template(\"static_cast<int32_t>({self}.size())\")\n"
        "    @pure\n"
        "    @readonly\n"
        "    def count(self) -> Int32: ...\n"
        "    @property\n"
        "    @native(\"capacity\", cpp_return_type=UInt64)\n"
        "    @pure\n"
        "    @readonly\n"
        "    def cap(self) -> Int32: ...\n")

    def test_cpp_template_property_and_ret_cast_route(self):
        src = self._VEC + (
            "def use() -> None:\n"
            "    v: Vec[Int32] = Vec[Int32]()\n"
            "    v.add(10)\n"
            "    n = v.count\n"
            "    c = v.cap\n"
            "    print(n, c)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("method.ptr_template", 0) >= 1
        assert w.get("method.native_ret_cast", 0) >= 1
        _assert_byte_identical(src)


class TestGenericSuperCalls:
    def test_generic_super_method_routes(self):
        src = (
            "from tpy import Int32\n"
            "class Base[T]:\n"
            "    val: T\n"
            "    def __init__(self, val: T):\n"
            "        self.val = val\n"
            "    def transform[U](self, other: U) -> U:\n"
            "        return other\n"
            "class Child[T](Base[T]):\n"
            "    def __init__(self, val: T):\n"
            "        super().__init__(val)\n"
            "    def wrap[U](self, other: U) -> U:\n"
            "        return super().transform(other)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "wrap") is not None
        assert w.get("call.super_generic", 0) >= 1
        assert w.get("arg.same_tparam_name", 0) >= 1
        _assert_byte_identical(src)

    def test_class_tparam_arg_at_method_slot_routes_identically(self):
        # The adjacent shape at the same-T row: an arg declared as the CLASS
        # param T at the method's own U slot. Sema substitutes the call
        # site's slot to T, so the name-equality pin holds and the shape
        # ROUTES -- a genuinely mismatched name cannot reach the gate.
        # Pinned byte-identical so a substitution change here surfaces.
        src = (
            "from tpy import Int32\n"
            "class Base[T]:\n"
            "    val: T\n"
            "    def __init__(self, val: T):\n"
            "        self.val = val\n"
            "    def transform[U](self, other: U) -> U:\n"
            "        return other\n"
            "class Child[T](Base[T]):\n"
            "    def __init__(self, val: T):\n"
            "        super().__init__(val)\n"
            "    def wrap_t(self, other: T) -> T:\n"
            "        return super().transform(other)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "wrap_t") is not None
        _assert_byte_identical(src)


class TestModuleGenericExplicitTargs:
    def test_folded_explicit_targs_route(self, tmp_path):
        # `helpers.identity[Int32](x)`: sema folds the explicit spelling
        # into inferred_type_args and the AST renders from inferred alone,
        # so the equality carve-out admits the module-qualified form.
        (tmp_path / "helpers.py").write_text(
            "def identity[T](x: T) -> T:\n"
            "    return x\n")
        src = (
            "import helpers\n"
            "from tpy import Int32\n"
            "def use() -> Int32:\n"
            "    return helpers.identity[Int32](Int32(7))\n")
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "::tpyapp::helpers::identity<int32_t>(7)" in cpp
        _assert_byte_identical(src, extra_lib_dirs=[tmp_path])


class TestTypedDictGetAndMembership:
    _CFG = (
        "from typing import TypedDict, Unpack\n"
        "from tpy import Int32\n"
        "class Config(TypedDict, total=False):\n"
        "    host: str\n"
        "    port: Int32\n")

    def test_get_and_membership_route(self):
        src = self._CFG + (
            "def probe(**kwargs: Unpack[Config]) -> None:\n"
            "    if \"host\" not in kwargs:\n"
            "        print(\"no host\")\n"
            "    h = kwargs.get(\"host\", \"d\")\n"
            "    p = kwargs.get(\"port\")\n"
            "    print(h, p is None)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert w.get("method.typed_dict_get", 0) >= 2
        assert w.get("binop.typed_dict_in", 0) >= 1
        _assert_byte_identical(src)

    def test_always_true_fold_routes_operand_effect(self):
        # A total=True field folds the membership to a constant with the
        # operand-effect comma wrapper (`(static_cast<void>(kwargs), true)`),
        # keeping the receiver evaluated.
        src = (
            "from typing import TypedDict, Unpack\n"
            "class Fixed(TypedDict):\n"
            "    name: str\n"
            "def folded(**kwargs: Unpack[Fixed]) -> None:\n"
            "    if \"name\" in kwargs:\n"
            "        print(kwargs.get(\"name\", \"x\"))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "folded") is not None
        assert w.get("binop.typed_dict_in_total", 0) >= 1
        _assert_byte_identical(src)

    def test_always_true_not_in_folds_false(self):
        # The `not in` flavor renders the operand-effect FALSE constant.
        src = (
            "from typing import TypedDict, Unpack\n"
            "class Fixed(TypedDict):\n"
            "    name: str\n"
            "def folded(**kwargs: Unpack[Fixed]) -> None:\n"
            "    print(\"name\" not in kwargs)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "folded") is not None
        assert w.get("binop.typed_dict_in_total", 0) >= 1
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "(static_cast<void>(kwargs), false)" in cpp
        _assert_byte_identical(src)


class TestBytesLiteralAndSliceReceivers:
    def test_bytes_literal_and_slice_receivers_route(self):
        src = (
            "def use(data: bytes) -> None:\n"
            "    parts = b\"a,b,c\".split(b\",\")\n"
            "    for p in parts:\n"
            "        print(p)\n"
            "    vparts = data[0:5].split(b\" \")\n"
            "    for vp in vparts:\n"
            "        print(vp)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("method.recv.bytes_literal", 0) >= 1
        assert w.get("method.recv.subscript", 0) >= 1
        _assert_byte_identical(src)


class TestProtocolFieldReceivers:
    def test_bounded_tparam_field_receiver_routes(self):
        src = (
            "from typing import Protocol\n"
            "from tpy import Int32, Own\n"
            "class Foo:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "class FooMaker(Protocol):\n"
            "    def make(self) -> Own[Foo]: ...\n"
            "class Bar[T: FooMaker]:\n"
            "    factory: T\n"
            "    def __init__(self, factory: T) -> None:\n"
            "        self.factory = factory\n"
            "    def create_foo(self) -> Own[Foo]:\n"
            "        return self.factory.make()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "create_foo") is not None
        assert w.get("method.protocol_field_recv", 0) >= 1
        _assert_byte_identical(src)


class TestModuleVarReceivers:
    def test_environ_method_len_and_membership_route(self):
        src = (
            "import os\n"
            "def use() -> None:\n"
            "    os.environ.update({\"TPY_X_A\": \"1\"})\n"
            "    print(os.environ.get(\"TPY_X_A\"))\n"
            "    print(len(os.environ), \"TPY_X_A\" in os.environ)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("arg.native_module_var", 0) >= 1
        _assert_byte_identical(src)

    def test_module_var_decl_alias_still_defers(self):
        # Binding the module var to a LOCAL is an unpinned consumer -- the
        # pointer-slot read must keep deferring outside receiver/native/print
        # positions.
        src = (
            "import os\n"
            "def use() -> None:\n"
            "    env = os.environ\n"
            "    print(len(env))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestSelfCallableField:
    def test_self_callable_field_invocation_routes(self):
        src = (
            "from typing import Callable\n"
            "from tpy import Int32\n"
            "class H:\n"
            "    on_event: Callable[[Int32], None]\n"
            "    def __init__(self, cb: Callable[[Int32], None]) -> None:\n"
            "        self.on_event = cb\n"
            "    def trigger(self, value: Int32) -> None:\n"
            "        self.on_event(value)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "trigger") is not None
        _assert_byte_identical(src)


class TestViewFamilyReceiverWidenings:
    def test_bytes_field_call_and_property_receivers_route(self):
        src = (
            "from tpy import StrView\n"
            "class Msg:\n"
            "    payload: bytes\n"
            "    def __init__(self, payload: bytes) -> None:\n"
            "        self.payload = payload\n"
            "    def m_text(self) -> str:\n"
            "        return self.payload.decode()\n"
            "    @property\n"
            "    def text(self) -> str:\n"
            "        return self.payload.decode()\n"
            "    @property\n"
            "    def raw(self) -> bytes:\n"
            "        return self.text.encode()\n"
            "def make_bytes() -> bytes:\n"
            "    return b\"  ab  \"\n"
            "def use() -> None:\n"
            "    b2 = make_bytes().strip()\n"
            "    print(len(b2))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "m_text") is not None
        assert _fn(thir, "raw") is not None
        assert _fn(thir, "use") is not None
        assert w.get("method.recv.view_field", 0) >= 1
        _assert_byte_identical(src)


class TestValueOptCallableBoundaries:
    def test_narrowed_invocation_routes_value_unwrap(self):
        # The invocation unwraps the declared Optional[Callable] binding
        # (`cb.value()(1)` -- call.opt_callable_unwrap), and the None-test
        # reads has_value.
        src = _PRELUDE + (
            "def invoke(cb: Callable[[Int32], None] | None) -> None:\n"
            "    if cb is not None:\n"
            "        cb(1)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "invoke") is not None
        cpp = _assert_byte_identical(src)
        assert "cb.value()(1);" in cpp[1]
        assert "if ((cb.has_value()))" in cpp[1]

    def test_mismatched_signature_slot_still_defers(self):
        # Exact-slot pin: a differing inner signature is a different
        # std::function type -- the bare pass would rely on a conversion the
        # AST never spells, so the row must not admit it.
        src = _PRELUDE + (
            "def takes_opt(cb: Callable[[Int32], None] | None) -> bool:\n"
            "    return cb is None\n"
            "def forward(cb: Callable[[str], None] | None) -> bool:\n"
            "    return takes_opt(None) and (cb is None)\n")
        thir = _lower_ctx(src)
        # `forward` still defers (its own None-test on a callable optional
        # has no cond arm) -- the pin is that admission did not widen past
        # the exact slot; byte-identity holds either way.
        _assert_byte_identical(src)


class TestTypedDictMembershipCallReceiver:
    """A CALL receiver at the TypedDict membership (`"a" in make()`): the
    fold keeps the operand evaluated (`(static_cast<void>(make()), true)`)
    and the non-total flavor tests has_value over the call render."""

    def test_total_fold_call_receiver_routes(self):
        src = (
            "from typing import TypedDict\n"
            "from tpy import Own\n"
            "class TD(TypedDict):\n"
            "    a: int\n"
            "def make() -> Own[TD]:\n"
            "    return TD(a=1)\n"
            "def f() -> None:\n"
            "    if \"a\" in make():\n"
            "        print(\"in\")\n"
            "    if \"a\" not in make():\n"
            "        print(\"unreachable\")\n"
            "f()\n")
        _assert_routes_byte_identical(src)

    def test_non_total_call_receiver_routes(self):
        src = (
            "from typing import TypedDict\n"
            "from tpy import Own\n"
            "class TD(TypedDict, total=False):\n"
            "    a: int\n"
            "def make() -> Own[TD]:\n"
            "    return TD(a=1)\n"
            "def f() -> None:\n"
            "    if \"a\" in make():\n"
            "        print(\"present\")\n"
            "f()\n")
        _assert_routes_byte_identical(src)
