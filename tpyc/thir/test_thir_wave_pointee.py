"""The scoped pointee-accessor waves (queue items 2D / 3-G1 / G2 / G3).

Routing pins for the arms that previously rode corpus witnesses only, and
COMMITTED boundary pins for the three divergences the remote byte-diff
caught (the probe-becomes-pin rule):

  * print_optional over a CONTAINER pointee spells kind-keyed template
    args (the Formatter is a pure function of the pointee type).
  * A None-NARROWED Optional FIELD at the ptr-opt return lifts via
    optional_to_ptr (declared-type keyed), never the pointee addr row.
  * An `Own[Box]`-returning call coerced into an Optional local takes the
    OPT_RVALUE slot (`Box __slot_1 = ..; Box* t = &__slot_1;`), never the
    OPT_STORAGE_CALL lift (which requires a storage-optional callee).

Plus the sema unit for substitute_method_type_params' accessor-flag
propagation (the inherited-generic-property fact drop).
"""

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
)

_BOX = (
    "from tpy import Int32, Own\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.val = v\n"
)


class TestGenericOptionalPointer:
    SRC = (
        "from tpy import Int32\n"
        "def first[T](xs: list[T]) -> T | None:\n"
        "    for x in xs:\n"
        "        return x\n"
        "    return None\n"
        "def via_ternary(xs: list[Int32], c: bool) -> Int32:\n"
        "    y = first(xs) if c else None\n"
        "    if y is not None:\n"
        "        return y\n"
        "    return -1\n"
        "def main() -> None:\n"
        "    xs = [Int32(10), Int32(20)]\n"
        "    print(via_ternary(xs, True))\n"
        "    print(via_ternary(xs, False))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The T-element addr lift and the forced-ptr None return.
        assert "return &(x);" in hpp
        # The ternary decl binds the lowered ternary bare; the narrowed
        # value return derefs and moves at last use.
        assert "int32_t* y = ((c) ? (first<int32_t>(xs)) : (nullptr));" \
            in cpp
        assert "return std::move((*y));" in cpp


class TestOwnStorageCallDecl:
    SRC = (
        "from typing import Optional\n"
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "def build() -> Own[Tree[Int32]]:\n"
        "    return 7\n"
        "def make_some() -> Own[Optional[Tree[Int32]]]:\n"
        "    return build()\n"
        "def main() -> None:\n"
        "    s = make_some()\n"
        "    print(s is not None)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The storage optional materializes; the binding lifts the pointer.
        assert ("std::optional<Tree<int32_t>> __slot_1 = make_some();"
                in cpp)
        assert "Tree<int32_t>* s = ::tpy::optional_to_ptr(__slot_1);" in cpp
        assert "return (s != nullptr);" not in cpp  # print form, not return


class TestPointeeBoundaries:
    def test_own_plain_call_keeps_opt_rvalue_slot(self):
        # BOUNDARY (fixed divergence): an `Own[Box]`-returning call
        # coerced into the Optional local materializes the PLAIN slot
        # (`Box __slot_1 = make_box(..); Box* t = &__slot_1;`), never the
        # OPT_STORAGE_CALL lift -- that arm requires the callee return
        # the storage optional itself.
        src = _BOX + (
            "def make_box(v: Int32) -> Own[Box]:\n"
            "    return Box(v)\n"
            "def main() -> None:\n"
            "    t: Box | None = make_box(5)\n"
            "    if t is not None:\n"
            "        print(t.val)\n"
            "main()\n"
        )
        hpp, cpp = _assert_byte_identical(src)
        assert "Box __slot_1 = make_box(5);" in cpp
        assert "Box* t = &__slot_1;" in cpp
        assert "optional_to_ptr(__slot_1)" not in cpp

    def test_container_pointee_print_spells_the_formatter(self):
        # print_optional over a CONTAINER pointee spells the kind-keyed
        # template args -- the Formatter is a pure function of the pointee
        # type, so the OPT_PTR row picks it rather than excluding the shape.
        src = (
            "from tpy import Int32\n"
            "def show(lst: list[Int32] | None) -> None:\n"
            "    print(lst)\n"
            "def main() -> None:\n"
            "    xs: list[Int32] = [Int32(1)]\n"
            "    show(xs)\n"
            "    show(None)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("print.opt_ptr_name", 0) == 1
        assert ("::tpy::print_optional<::tpy::ListPrinter<std::vector"
                "<int32_t>>, std::vector<int32_t>>(lst)") in hpp + cpp

    def test_bytearray_pointee_print_and_binding_route(self):
        # bytearray joins the WIDE pointee class: the param binds `T*` and
        # its print spells ByteArrayPrinter (distinct from bytes' repr).
        src = (
            "def show(b: bytearray | None) -> None:\n"
            "    print(b)\n"
            "def main() -> None:\n"
            "    buf = bytearray(b\"ok\")\n"
            "    show(buf)\n"
            "    show(None)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ("::tpy::print_optional<::tpy::ByteArrayPrinter, "
                "std::vector<uint8_t>>(b)") in hpp + cpp

    def test_narrowed_optional_field_return_lifts(self):
        # BOUNDARY (fixed divergence): a None-NARROWED Optional FIELD at
        # the ptr-opt return keeps the storage lift
        # (`::tpy::optional_to_ptr(t.o)`) -- the pointee addr row keys the
        # DECLARED field type, and a narrowed field's declared storage is
        # still std::optional<T>.
        src = _BOX + (
            "class Holder:\n"
            "    o: Box | None\n"
            "    def __init__(self, o: Box | None) -> None:\n"
            "        self.o = o\n"
            "def get(t: Holder) -> Box | None:\n"
            "    if t.o is None:\n"
            "        return None\n"
            "    return t.o\n"
            "def main() -> None:\n"
            "    h = Holder(Box(3))\n"
            "    r = get(h)\n"
            "    if r is not None:\n"
            "        print(r.val)\n"
            "main()\n"
        )
        hpp, cpp = _assert_byte_identical(src)
        assert "::tpy::optional_to_ptr(t.o)" in cpp
        assert "&((*t.o))" not in cpp


class TestInheritedGenericAccessorFlags:
    def test_substitution_keeps_property_flags(self):
        # substitute_method_type_params drops no accessor identity: the
        # substituted fi of an inherited generic property keeps
        # is_property_getter/setter (the fact-drop class the
        # deep_const_borrow_params audit named; this pair now propagates).
        from ..parse.nodes import TpyMethodCall
        from .testutil import _compile, _entry
        src = (
            "from tpy import Int32\n"
            "class Holder[K]:\n"
            "    _k: K\n"
            "    def __init__(self, k: K) -> None:\n"
            "        self._k = k\n"
            "    @property\n"
            "    def key(self) -> K:\n"
            "        return self._k\n"
            "    @key.setter\n"
            "    def key(self, value: K) -> None:\n"
            "        self._k = value\n"
            "class Sub(Holder[Int32]):\n"
            "    pass\n"
            "def main() -> None:\n"
            "    h = Sub(5)\n"
            "    h.key = 6\n"
            "    print(h.key)\n"
            "main()\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)

        found = []

        def walk(node):
            if isinstance(node, TpyMethodCall):
                found.append(node)
            for f in getattr(node, "__dataclass_fields__", {}):
                v = getattr(node, f)
                if isinstance(v, (list, tuple)):
                    for x in v:
                        if hasattr(x, "__dataclass_fields__"):
                            walk(x)
                elif hasattr(v, "__dataclass_fields__"):
                    walk(v)

        for fn in entry.ast.functions:
            for s in fn.body:
                walk(s)
        setters = [c for c in found if c.method == "set_key"]
        getters = [c for c in found if c.method == "key"]
        assert setters and setters[0].resolved_function_info is not None
        assert setters[0].resolved_function_info.is_property_setter is True
        assert getters and getters[0].resolved_function_info is not None
        assert getters[0].resolved_function_info.is_property_getter is True


class TestStorageAccessorScalarBoundary:
    def test_storage_wide_rejects_scalar_pointee(self):
        # BOUNDARY (mechanizes the accessor's prose invariant): the
        # force_pointer_repr SCALAR class is safe only under the
        # uses_pointer_repr guard of _optional_ptr_borrow_wide -- the
        # STORAGE flavor must never admit a scalar pointee.
        from ..typesys import INT32, OptionalType
        from .testutil import _compile
        compiler, modules = _compile(
            "from tpy import Int32\n"
            "def main() -> None:\n"
            "    print(1)\n"
            "main()\n")
        analyzer = modules[0].analyzer
        from .lower.predicates import _storage_optional_return_wide
        assert _storage_optional_return_wide(
            OptionalType(INT32), analyzer) is None


class TestOptionalPrintFormatters:
    """gen_print's two Optional arms: the FIELD source takes the `_val`
    spelling whatever the repr, and the Formatter template is keyed on the
    inner's C++ type having no plain `operator<<`."""

    _SRC = (
        "from tpy import Int32\n"
        "class Bag:\n"
        "    items: list[Int32] | None\n"
        "    by_key: dict[str, Int32] | None\n"
        "    elems: set[Int32] | None\n"
        "    data: bytes | None\n"
        "    buf: bytearray | None\n"
        "    tup: tuple[Int32, Int32] | None\n"
        "    def __init__(self, items: list[Int32] | None,\n"
        "                 by_key: dict[str, Int32] | None,\n"
        "                 elems: set[Int32] | None, data: bytes | None,\n"
        "                 buf: bytearray | None,\n"
        "                 tup: tuple[Int32, Int32] | None) -> None:\n"
        "        self.items = items\n"
        "        self.by_key = by_key\n"
        "        self.elems = elems\n"
        "        self.data = data\n"
        "        self.buf = buf\n"
        "        self.tup = tup\n"
        "    def show(self) -> None:\n"
        "        print(self.items)\n"
        "        print(self.by_key)\n"
        "        print(self.elems)\n"
        "        print(self.data)\n"
        "        print(self.buf)\n"
        "        print(self.tup)\n"
        "def show_bytes(b: bytes | None) -> None:\n"
        "    print(b)\n"
        "def main() -> None:\n"
        "    Bag(None, None, None, b\"hi\", None, (1, 2)).show()\n"
        "    show_bytes(None)\n"
        "main()\n"
    )

    def test_field_sources_take_print_optional_val(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        # Pointer-repr fields: field storage IS std::optional<T>, so the
        # `_val` spelling, with the kind-keyed Formatter.
        assert ("::tpy::print_optional_val<::tpy::ListPrinter<std::vector"
                "<int32_t>>, std::vector<int32_t>>(this->items)") in out
        assert ("::tpy::print_optional_val<::tpy::DictPrinter<std::string, "
                "int32_t>, ::tpy::ordered_map<std::string, int32_t>>"
                "(this->by_key)") in out
        assert ("::tpy::print_optional_val<::tpy::SetPrinter<int32_t>, "
                "::tpy::ordered_set<int32_t>>(this->elems)") in out
        assert ("::tpy::print_optional_val<::tpy::ByteArrayPrinter, "
                "std::vector<uint8_t>>(this->buf)") in out
        # Value-repr fields (bytes / value tuple) take the same wrap.
        assert ("::tpy::print_optional_val<::tpy::BytesPrinter, "
                "std::vector<uint8_t>>(this->data)") in out
        assert ("::tpy::print_optional_val<::tpy::TuplePrinter<int32_t, "
                "int32_t>, std::tuple<int32_t, int32_t>>(this->tup)") in out

    def test_view_param_spells_the_view_inner(self):
        # A borrow-form Optional[bytes] PARAM is `optional<span<const
        # uint8_t>>`, so its explicit inner template arg is the VIEW, not
        # the owned vector the field carries.
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert ("::tpy::print_optional_val<::tpy::BytesPrinter, "
                "std::span<const uint8_t>>(b)") in hpp + cpp

    def test_ctor_literal_args_and_value_opt_tuple_mil(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("ctor.bytes_literal_value_opt", 0) == 1
        assert wit.get("ctor.tuple_literal_value_opt", 0) == 1
        out = hpp + cpp
        assert '::tpy::bytes_literal_owned("hi", 2)' in out
        assert "std::tuple<int32_t, int32_t>{1, 2}" in out
        assert "tup(tup)" in out

    def test_record_pointee_print_keeps_the_ctad_form(self):
        # BOUNDARY: a record inner streams through its own operator<<, so
        # no Formatter is spelled -- the plain CTAD call, not the templated
        # one, on both the NAME and the FIELD source.
        src = (
            "from tpy import Int32\n"
            "class Rec:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "class Holder:\n"
            "    r: Rec | None\n"
            "    def __init__(self, r: Rec | None):\n        self.r = r\n"
            "    def show(self) -> None:\n        print(self.r)\n"
            "def show_name(r: Rec | None) -> None:\n    print(r)\n"
            "def main() -> None:\n"
            "    Holder(None).show()\n"
            "    show_name(None)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "::tpy::print_optional_val(this->r)" in out
        assert "::tpy::print_optional(r)" in out
        assert "print_optional<" not in out
