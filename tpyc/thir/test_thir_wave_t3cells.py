"""Chained call-lane cells: the static type-param isinstance
disjunction at a value position, top-level generic ref-slot temps (the
module-init body is a flush position), and the generic-static
wrap_optional chain (qualcall storage-opt return + Own[value]-optional
member arg + the Optional[Own[record]] print wrap)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical, _top_level,
)


class TestStaticIsinstanceValuePosition:
    _HDR = (
        "from tpy import Int32\n"
        "class Animal:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        self.n = n\n"
        "class Dog(Animal): ...\n"
        "class Cat(Animal): ...\n"
    )

    def test_tuple_form_returns_disjunction(self):
        src = (self._HDR +
               "def is_dog_or_cat[T: Animal](x: T) -> bool:\n"
               "    return isinstance(x, (Dog, Cat))\n"
               "def main() -> None:\n"
               "    print(is_dog_or_cat(Dog(1)))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "is_dog_or_cat") is not None
        assert faces.get("call.isinstance_static_value", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        joined = cpp[0] + cpp[1]
        assert joined.count("::tpy::isinstance_static<") >= 2
        assert " || " in joined

    def test_non_tparam_isinstance_value_stays_out(self):
        # A union-subject isinstance belongs to the narrowing machinery --
        # the value-position arm must not capture it; the body falls back.
        src = (self._HDR +
               "def check(x: Dog | Cat) -> bool:\n"
               "    return isinstance(x, Dog)\n"
               "def main() -> None:\n"
               "    print(check(Dog(1)))\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.isinstance_static_value", 0) == 0
        _assert_byte_identical(src)


class TestTopLevelGenericRefSlotTemps:
    def test_literal_temps_flush_before_global_slot(self):
        src = ("from tpy import Int32, Own\n"
               "class Pair[A, B]:\n"
               "    first: A\n"
               "    second: B\n"
               "    def __init__(self, a: A, b: B):\n"
               "        self.first = a\n"
               "        self.second = b\n"
               "def create_pair[A, B](a: A, b: B) -> Own[Pair[A, B]]:\n"
               "    return Pair[A, B](a, b)\n"
               "p1 = create_pair(10, \"hello\")\n"
               "print(p1.first)\n")
        top, faces, fallback = _top_level(src)
        assert top is not None
        assert faces.get("argtemp.generic_ref_slot", 0) >= 1
        assert not fallback
        cpp = _assert_byte_identical(src)
        assert "int32_t __tmp_1 = 10;" in cpp[1]
        assert ("static Pair<int32_t, std::string> __global_slot_1 = "
                "create_pair<int32_t, std::string>(__tmp_1, __tmp_2);"
                in cpp[1])

    def test_subclass_rvalue_global_still_rejects(self):
        # The widened NominalType branch keeps its shape guard: a SUBCLASS
        # rvalue retypes the slot (the polymorphic arm) and stays AST.
        src = ("from tpy import Int32\n"
               "class B:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32):\n"
               "        self.v = v\n"
               "class D(B): ...\n"
               "g: B = D(7)\n"
               "print(g.v)\n")
        top, _, fallback = _top_level(src)
        assert top is None
        assert any("top_level" in k for k in fallback)
        _assert_byte_identical(src)


class TestGenericStaticMethodChain:
    _HDR = (
        "from tpy import Int32, Own\n"
        "class Container[T]:\n"
        "    value: T\n"
        "    def __init__(self, value: Own[T]):\n"
        "        self.value = value\n"
        "    @staticmethod\n"
        "    def wrap_optional(v: Own[T] | None) -> Own[Container[T]] | None:\n"
        "        if v is not None:\n"
        "            return Container(v)\n"
        "        return None\n"
    )

    def test_storage_opt_ret_and_member_arg_and_print(self):
        src = (self._HDR +
               "def main() -> None:\n"
               "    c3 = Container.wrap_optional(99)\n"
               "    print(c3 is not None)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("method.qualcall.storage_opt_ret", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert ("std::optional<Container<int32_t>> c3 = "
                "Container<int32_t>::wrap_optional(99);") in cpp[1]

    def test_optional_own_record_name_print_wraps_whole(self):
        src = (self._HDR +
               "    def __repr__(self) -> str:\n"
               "        return \"C\"\n"
               "def main() -> None:\n"
               "    c3 = Container.wrap_optional(99)\n"
               "    print(\"c3:\", c3)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("print.optval", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "::tpy::print_optional_val(c3)" in cpp[1]

    def test_own_nonvalue_payload_member_arg_stays_out(self):
        # _value_opt_member_arg's Own peel is VALUE-payload only: a record
        # rvalue at a static-method `Own[record] | None` slot (the same
        # marker-ladder row the routing pin exercises) keeps gen_call_arg's
        # Own cascade -> the calling body falls back whole.
        src = ("from tpy import Int32, Own\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32):\n"
               "        self.x = x\n"
               "class Helper:\n"
               "    @staticmethod\n"
               "    def probe(p: Own[P] | None) -> Int32:\n"
               "        return 0 if p is None else 1\n"
               "def main() -> None:\n"
               "    print(Helper.probe(P(3)))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        assert faces.get("call.optval_member", 0) == 0
        _assert_byte_identical(src)

    def test_storage_opt_ret_non_storage_sink_stays_out(self):
        # The marker escape is STORAGE-gated: the same Own-optional return
        # consumed as a plain call ARG (a non-storage sink) keeps rejecting.
        src = (self._HDR +
               "def check(c: Container[Int32] | None) -> bool:\n"
               "    return c is not None\n"
               "def main() -> None:\n"
               "    print(check(Container.wrap_optional(99)))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        assert faces.get("method.qualcall.storage_opt_ret", 0) == 0
        _assert_byte_identical(src)

    def test_narrowed_optional_own_record_print_stays_out(self):
        # The print row keys on the RESOLVED type: a narrowed read arrives
        # member-typed, so the whole-optional wrap must not fire.
        src = (self._HDR +
               "def main() -> None:\n"
               "    c3 = Container.wrap_optional(99)\n"
               "    if c3 is not None:\n"
               "        print(\"x:\", c3)\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("print.optval", 0) == 0
        _assert_byte_identical(src)
