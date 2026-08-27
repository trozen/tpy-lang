"""Pins for the OPEN type-param decl slot -- a local whose declared type is a
bare `T` (`T newitem = copy(heap[pos]);`) and its `Span[readonly[T]]` sibling.

Both rest on one fact: an open type-param has no borrow form and no storage
form until instantiation, so the slot takes the plain spelled copy a scalar's
does and the C++ template traits settle the shape later. `T` is nonetheless a
plain NON-value type, so every indirection rule still applies -- a reassigned,
hoisted or move-through name and a non-rvalue source each bind an alias
instead, and the boundary units hold those out.

Being non-value also makes such a local the first one on the plain-copy decl
path that can OWN, which is what the element-sink move unit pins."""

from __future__ import annotations

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical,
                       _lower_ctx_witnessed, _thir_ctx)

_MAKE = (
    "from tpy import Own, Int32, Span, readonly, span\n"
    "def make[T](x: Own[T]) -> Own[T]:\n"
    "    return x\n"
)


class TestTypeParamDeclSlot:
    def test_type_param_slot_routes(self):
        src = _MAKE + (
            "def store[T](x: Own[T], xs: list[T]) -> Int32:\n"
            "    a: T = make(x)\n"
            "    xs[0] = a\n"
            "    return 1\n"
            "def main() -> None:\n"
            "    xs = [1, 2]\n"
            "    print(store(9, xs))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "T a = make<T>(std::move(x));" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["decl.type_param_slot"] >= 1

    def test_type_param_local_moves_at_element_sink(self):
        # The owning half: a `T` local is promoted movable, so its last use at
        # a checked element write is stolen, not copied.
        src = _MAKE + (
            "def store[T](x: Own[T], d: dict[Int32, T]) -> Int32:\n"
            "    a: T = make(x)\n"
            "    d[1] = a\n"
            "    return 1\n"
            "def main() -> None:\n"
            "    d = {0: 1}\n"
            "    print(store(9, d))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::__setitem__(d, 1, std::move(a));" in hpp + cpp

    def test_view_source_at_element_sink_copies_without_moving(self):
        # The owned-copy sink is the exception to the move above: what the
        # copy constructs from is a trivially-copyable view, not the binding
        # the name reads, so moving it would name the wrong object.
        src = (
            "def store(a: str | None, b: bytes | None) -> None:\n"
            "    out: dict[str, str] = {}\n"
            "    raw: dict[str, bytes] = {}\n"
            "    if a is not None:\n"
            "        out['k'] = a\n"
            "    if b is not None:\n"
            "        raw['k'] = b\n"
            "    print(len(out), len(raw))\n"
            "def main() -> None:\n"
            "    store('x', b'y')\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert '::tpy::__setitem__(out, "k", std::string((*a)));' in hpp + cpp
        assert ('::tpy::__setitem__(raw, "k", ::tpy::bytes_copy((*b)));'
                in hpp + cpp)

    def test_narrowed_non_view_optional_at_element_sink_moves(self):
        # ... and the shape that still moves: no copy intervenes, so the
        # narrowed deref is the operand and its last use is stolen.
        src = (
            "def store(a: int | None) -> None:\n"
            "    out: list[int] = [0]\n"
            "    if a is not None:\n"
            "        out[0] = a\n"
            "    print(out[0])\n"
            "def main() -> None:\n"
            "    store(9)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::__setitem__(out, 0, std::move((*a)));" in hpp + cpp

    def test_reassigned_type_param_local_stays_ast(self):
        # BOUNDARY: a rebound `T` local is the AST's rebind-slot pointer
        # binding (`T* v = &__slot_1;` off a hoisted optional), not a copy.
        src = _MAKE + (
            "def relay[T](x: Own[T], y: Own[T]) -> Own[T]:\n"
            "    v: T = make(x)\n"
            "    v = make(y)\n"
            "    return v\n"
            "def main() -> None:\n"
            "    print(relay(7, 9))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl", "decl.slot_type")
        _assert_byte_identical(src)

    def test_non_rvalue_type_param_source_stays_ast(self):
        # BOUNDARY: a `T` slot fed by an LVALUE binds a reference alias
        # (`T& b = a;`), which is a different decl entirely.
        src = _MAKE + (
            "def relay[T](x: Own[T]) -> Int32:\n"
            "    a: T = make(x)\n"
            "    b: T = a\n"
            "    if a == b:\n"
            "        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(relay(7))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl", "decl.slot_type")
        _assert_byte_identical(src)


class TestSpanTypeParamElement:
    def test_readonly_type_param_span_slot_routes(self):
        src = _MAKE + (
            "def first[T](s: Span[readonly[T]]) -> Int32:\n"
            "    view: Span[readonly[T]] = s\n"
            "    return Int32(len(view))\n"
            "def main() -> None:\n"
            "    xs = [1, 2, 3]\n"
            "    sp = span(xs)\n"
            "    print(first(sp))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::span<const T> view = s;" in hpp + cpp

    def test_mutable_type_param_span_slot_routes(self):
        src = _MAKE + (
            "def first[T](s: Span[T]) -> Int32:\n"
            "    view: Span[T] = s\n"
            "    return Int32(len(view))\n"
            "def main() -> None:\n"
            "    xs = [1, 2, 3]\n"
            "    sp = span(xs)\n"
            "    print(first(sp))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::span<T> view = s;" in hpp + cpp

    def test_type_param_span_method_result_routes(self):
        # The RESULT position shares the slot predicate: a span is a value
        # view either way, so the call lands bare at the decl.
        src = (
            "from tpy import Span, readonly, Int32, span\n"
            "class Rows[T]:\n"
            "    items: list[T]\n"
            "    def __init__(self, items: list[T]) -> None:\n"
            "        self.items = items\n"
            "    def view(self) -> Span[readonly[T]]:\n"
            "        return span(self.items)\n"
            "    def size(self) -> Int32:\n"
            "        sp = self.view()\n"
            "        return Int32(len(sp))\n"
            "def main() -> None:\n"
            "    r = Rows([1, 2, 3])\n"
            "    print(r.size())\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::span<const T> sp = this->view();" in hpp + cpp

    def test_nested_span_type_param_slot_stays_ast(self):
        # BOUNDARY: a composite element has element-dependent reads with no
        # admitted arm to gate them, whatever its innermost type is.
        src = (
            "from tpy import Span, Int32\n"
            "def widths[T](rows: Span[Span[T]]) -> Int32:\n"
            "    view: Span[Span[T]] = rows\n"
            "    return Int32(len(view))\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl", "decl.slot_type")
        _assert_byte_identical(src)
