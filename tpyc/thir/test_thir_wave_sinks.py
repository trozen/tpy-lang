"""Print/compare-sink wave arms: user-record `__contains__` membership, the
str-call membership receiver, the target-less both-literal BigInt fold (and
its slot-position boundary), the value-opt str-literal method arg, the
user-record setitem str-literal value, protocol field-read args, and the
user-iterator field-read for-head."""

from __future__ import annotations

from .nodes import THIRMembership, THIRPrint
from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_JAR = (
    "from tpy import Int32\n"
    "class Jar:\n"
    "    n: Int32\n"
    "    def __init__(self):\n        self.n = 3\n"
    "    def __contains__(self, key: str) -> bool:\n        return self.n > 0\n"
)


class TestUserContainsMembership:
    def test_name_receiver_routes(self):
        src = (_JAR
               + "def use() -> None:\n"
               + "    j = Jar()\n"
               + "    print(\"sid\" in j)\n")
        thir, w = _lower_ctx_witnessed(src)
        fn = _fn(thir, "use")
        assert fn is not None
        assert w.get("binop.user_membership", 0) >= 1
        _assert_byte_identical(src)

    def test_not_in_negates(self):
        src = (_JAR
               + "def use() -> None:\n"
               + "    j = Jar()\n"
               + "    print(\"sid\" not in j)\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "use")
        assert fn is not None
        stmt = fn.body[1]
        assert isinstance(stmt, THIRPrint)
        m = stmt.args[0].expr
        assert isinstance(m, THIRMembership) and m.negate
        assert m.method_cpp == "__contains__"
        _assert_byte_identical(src)

    def test_field_receiver_routes(self):
        src = (_JAR
               + "class Holder:\n"
               + "    j: Jar\n"
               + "    def __init__(self):\n        self.j = Jar()\n"
               + "def use(h: Holder) -> None:\n"
               + "    print(\"sid\" in h.j)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestStrCallMembershipReceiver:
    def test_str_conv_receiver_routes(self):
        # `"x" in str(j)` -> the owned `std::string(::tpy::__str__(j))`
        # receiver through the `.find()` arm.
        src = (_JAR.replace(
                   "    def __contains__",
                   "    def __str__(self) -> str:\n        return \"jar\"\n"
                   "    def __contains__")
               + "def use() -> None:\n"
               + "    j = Jar()\n"
               + "    print(\"a\" in str(j))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestLiteralFold:
    def test_print_arg_folds(self):
        # Target-less print arg: the AST folds `2**40 + 1`; the THIR arm
        # mirrors the folded BigInt-targeted literal.
        src = "def use() -> None:\n    print(2**40 + 1)\n"
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("binop.literal_fold", 0) >= 1
        _assert_byte_identical(src)

    def test_compare_operand_folds(self):
        src = ("def use(n: int) -> None:\n"
               "    print(n < 2**40 - 1)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_slot_positions_render_the_operator(self):
        # Slot-threaded positions (decl init / arg / return) do NOT fold on
        # the AST path (a threaded target skips `_gen_binop`'s pure-literal
        # arm) -- they render the FULL operator chain, nested operands
        # included (`_ExprUse.slot_threaded` threads the operand slots and
        # the return value).
        src_decl = ("def use() -> None:\n"
                    "    n: int = 2**40 - 1\n"
                    "    print(n)\n")
        _assert_routes_byte_identical(src_decl)
        src_ret = "def use() -> int:\n    return 2**40 + 1\n"
        _assert_routes_byte_identical(src_ret)

    def test_overflowing_fold_folds_in_print(self):
        # A fold value OVERFLOWING int64 folds to `BigInt::from_str` on the
        # AST path in a target-less position; the fold arm mirrors it (the
        # pre-wave operator route diverged here, unwitnessed).
        src = "def use() -> None:\n    print(2**64 + 1)\n"
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("binop.literal_fold", 0) >= 1
        _assert_byte_identical(src)


class TestValueOptStrLiteralMethodArg:
    def test_get_with_str_default_routes(self):
        # A str literal into a value-repr `str | None` method slot renders
        # bare (`j.get("k", "d")`), the TypedDict-ctor face's row shared into
        # the record-method arg loop.
        src = (_JAR.replace(
                   "    def __contains__",
                   "    def get(self, key: str, default: str | None = None)"
                   " -> str | None:\n        return default\n"
                   "    def __contains__")
               + "def use() -> None:\n"
               + "    j = Jar()\n"
               + "    print(j.get(\"missing\", \"fallback\"))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


_SETITEM_JAR = _JAR.replace(
    "    def __contains__",
    "    def __getitem__(self, key: str) -> str:\n        return \"x\"\n"
    "    def __setitem__(self, key: str, value: str) -> None:\n"
    "        self.n += 1\n"
    "    def __contains__")


class TestUserSetitemStrLiteralValue:
    def test_str_literal_value_routes(self):
        src = (_SETITEM_JAR
               + "def use() -> None:\n"
               + "    j = Jar()\n"
               + "    j[\"k\"] = \"v\"\n"
               + "    print(j.n)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_str_name_value_stays_ast(self):
        # A NON-literal str value keeps its view->owned machinery on the AST
        # path -- the boundary of the literal-only row.
        src = (_SETITEM_JAR
               + "def use(v: str) -> None:\n"
               + "    j = Jar()\n"
               + "    j[\"k\"] = v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestProtocolFieldArg:
    def test_dyn_protocol_field_arg_stays_ast(self):
        # The @dynamic boundary of the field-read protocol-slot arm: a
        # field conformer into a @dynamic slot needs the adapter temp, so
        # the body must keep falling back.
        src = ("from typing import Protocol\n"
               "from tpy import Int32, dynamic\n"
               "@dynamic\n"
               "class Noisy(Protocol):\n"
               "    def speak(self) -> Int32: ...\n"
               "class Dog:\n"
               "    def __init__(self) -> None:\n        pass\n"
               "    def speak(self) -> Int32:\n        return 1\n"
               "class Holder:\n"
               "    d: Dog\n"
               "    def __init__(self):\n        self.d = Dog()\n"
               "def hear(n: Noisy) -> Int32:\n    return n.speak()\n"
               "def use(h: Holder) -> None:\n"
               "    print(hear(h.d))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_len_over_record_field_routes(self):
        # An F1-record FIELD read into a structural protocol slot
        # (`len(h.j)` -> `::tpy::__len__(h.j)`) renders bare like a
        # conformer name.
        src = (_JAR.replace(
                   "    def __contains__",
                   "    def __len__(self) -> Int32:\n        return self.n\n"
                   "    def __contains__")
               + "class Holder:\n"
               + "    j: Jar\n"
               + "    def __init__(self):\n        self.j = Jar()\n"
               + "def use(h: Holder) -> None:\n"
               + "    print(len(h.j))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestUserIteratorFieldForHead:
    def test_field_iterable_routes(self):
        src = ("from tpy import Int32\n"
               "class It:\n"
               "    i: Int32\n"
               "    def __init__(self):\n        self.i = 0\n"
               "    def __iter__(self) -> It:\n        return self\n"
               "    def __next__(self) -> Int32 | None:\n"
               "        if self.i >= 3:\n            return None\n"
               "        self.i += 1\n        return self.i\n"
               "class Holder:\n"
               "    it: It\n"
               "    def __init__(self):\n        self.it = It()\n"
               "def use(h: Holder) -> None:\n"
               "    for x in h.it:\n        print(x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestOwnContainerParamMove:
    _SRC = ("from tpy import Int32, Own\n"
            "def drop(xs: Own[list[Int32]]) -> Int32:\n"
            "    store: list[list[Int32]] = []\n"
            "    store.append(xs)\n"
            "    return len(store)\n")

    def test_last_use_move_routes(self):
        # An `Own[list]` PARAM name (an unrouted binding kind for general
        # reads) is consumed whole by the Own-slot move arm
        # (`push_back(std::move(xs))`) -- allow_unrouted_name opts in.
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "drop") is not None
        _assert_byte_identical(self._SRC)

    def test_plain_read_before_the_move_routes(self):
        # A general (non-consuming) read of the same binding renders bare and
        # does NOT consume it, so the later insert still takes the move -- the
        # last-use verdict decides which read moves, not the binding kind.
        src = ("from tpy import Int32, Own\n"
               "def peek(xs: Own[list[Int32]]) -> Int32:\n"
               "    n = len(xs)\n"
               "    store: list[list[Int32]] = []\n"
               "    store.append(xs)\n"
               "    return n\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "peek") is not None
        _hpp, cpp = _assert_byte_identical(src)
        assert "int32_t n = ::tpy::__len__(xs);" in cpp
        assert "store.push_back(std::move(xs));" in cpp


class TestRecordKeyedContainers:
    _POINT = ("from dataclasses import dataclass\n"
              "from tpy import Int32\n"
              "@dataclass(frozen=True)\n"
              "class Point:\n"
              "    x: Int32\n"
              "    y: Int32\n")

    def test_record_key_dict_literal_routes(self):
        src = (self._POINT
               + "def use() -> None:\n"
               + "    d: dict[Point, str] = {Point(1, 2): \"a\"}\n"
               + "    print(d[Point(1, 2)])\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_set_literal_receiver_record_needle_routes(self):
        # `p in {Point(..), Point(..)}`: the spelled set-literal ctor rvalue
        # takes `.contains(p)` with the record NAME needle bare.
        src = (self._POINT
               + "def use(p: Point) -> None:\n"
               + "    print(p in {Point(1, 2), Point(3, 4)})\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_record_key_setitem_write_routes(self):
        # The WRITE path over the same shared key slice:
        # `d[Point(1, 2)] = "a"` -> `::tpy::__setitem__(d, Point(1, 2), "a")`.
        src = (self._POINT
               + "def use() -> None:\n"
               + "    d: dict[Point, str] = {}\n"
               + "    d[Point(1, 2)] = \"a\"\n"
               + "    print(d[Point(1, 2)])\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


_DYN = ("from tpy import Int32\n"
        "class H:\n"
        "    host: str\n"
        "    def __init__(self, host: str) -> None:\n        self.host = host\n"
        "    def __getattr__(self, name: str) -> str:\n"
        "        if name == \"alias\":\n            return self.host\n"
        "        raise AttributeError(name)\n")


class TestDynAttrProbes:
    def test_hasattr_runtime_probe_routes(self):
        src = (_DYN
               + "def use(h: H) -> None:\n"
               + "    print(hasattr(h, \"zzz\"))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("call.dyn_hasattr", 0) >= 1
        _assert_byte_identical(src)

    def test_hasattr_declared_member_folds(self):
        # A declared member folds to a compile-time bool via macro_expansion
        # (the existing expansion arm), no probe emitted.
        src = (_DYN
               + "def use(h: H) -> None:\n"
               + "    print(hasattr(h, \"host\"))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_getattr_default_routes(self):
        src = (_DYN
               + "def use(h: H) -> None:\n"
               + "    print(getattr(h, \"absent\", \"fallback\"))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("call.dyn_getattr_default", 0) >= 1
        _assert_byte_identical(src)

    def test_getattr_default_runtime_name_routes(self):
        # A str-NAME attr key routes through the same probe
        # (`h.__getattr__(name)` -- the runtime-name form).
        src = (_DYN
               + "def use(h: H, name: str) -> None:\n"
               + "    print(getattr(h, name, \"fallback\"))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestBytesMethodChainReceiver:
    def test_decode_on_bytes_method_result_routes(self):
        # `b.strip().decode()`-style chain: the inner bytes-returning method
        # feeds the outer bytes method positionally
        # (`::tpy::bytes_decode(::tpy::bytes_strip(b))`).
        src = ("def use(b: bytes) -> None:\n"
               "    print(b.strip().decode())\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("method.recv.bytes_method", 0) >= 1
        _assert_byte_identical(src)


class TestProtocolParamForHead:
    def test_structural_iterator_param_routes(self):
        # `for x in it:` over a STRUCTURAL Iterator[T] param: the deduced
        # `T_it&` is a plain lvalue, same loop render as a user-iterator
        # record name.
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n"
               "class Counter:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "    def __iter__(self) -> Counter:\n        return self\n"
               "    def __next__(self) -> Int32 | None:\n"
               "        if self.n <= 0:\n            return None\n"
               "        self.n -= 1\n        return self.n\n"
               "def total(it: Iterator[Int32]) -> Int32:\n"
               "    t: Int32 = 0\n"
               "    for x in it:\n        t += x\n"
               "    return t\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "total") is not None
        _assert_byte_identical(src)


class TestRecordFieldCompareOperand:
    def test_field_operand_routes(self):
        src = (_JAR.replace(
                   "    def __contains__",
                   "    def __eq__(self, other: Jar) -> bool:\n"
                   "        return self.n == other.n\n"
                   "    def __contains__")
               + "class Holder:\n"
               + "    j: Jar\n"
               + "    def __init__(self):\n        self.j = Jar()\n"
               + "def use(h: Holder, other: Jar) -> None:\n"
               + "    print(h.j == other)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)
