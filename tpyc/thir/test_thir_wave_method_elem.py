"""Pins for the method-only container element families
(`_container_method_elem`): an open `T`, the unit type, a `Callable` value
and a non-wrapper union all route a container method receiver, with the
matching insert-arg rows. Boundary pins hold the neighbours whose inserts
carry a lift the bare `push_back` does not render."""

from __future__ import annotations

from .testutil import (_assert_byte_identical, _assert_rejects_at, _compile,
                       _entry)


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp, compiler._thir_face_witnesses, compiler._thir_fallback


class TestTparamElement:
    def test_open_t_field_append_routes(self):
        src = (
            "from tpy import Int32\n"
            "class Bag[T]:\n"
            "    items: list[T]\n"
            "    def __init__(self):\n"
            "        self.items = []\n"
            "    def add(self, item: T) -> None:\n"
            "        self.items.append(item)\n"
            "def main() -> None:\n"
            "    b = Bag[Int32]()\n"
            "    b.add(7)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "this->items.push_back(item);" in out
        _assert_byte_identical(src)

    def test_discarded_open_t_pop_routes(self):
        src = (
            "from tpy import Int32\n"
            "class Bag[T]:\n"
            "    items: list[T]\n"
            "    def __init__(self):\n"
            "        self.items = []\n"
            "    def add(self, item: T) -> None:\n"
            "        self.items.append(item)\n"
            "    def drop(self) -> None:\n"
            "        self.items.pop()\n"
            "def main() -> None:\n"
            "    b = Bag[Int32]()\n"
            "    b.add(7)\n"
            "    b.drop()\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "::tpy::pop_back(this->items);" in out
        _assert_byte_identical(src)


class TestUnitAndCallableElements:
    def test_unit_element_append_renders_monostate(self):
        src = (
            "def main() -> None:\n"
            "    xs: list[None] = []\n"
            "    xs.append(None)\n"
            "    print(len(xs))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "xs.push_back(std::monostate{});" in out
        _assert_byte_identical(src)

    def test_callable_element_append_and_read_route(self):
        src = (
            "from typing import Callable\n"
            "from tpy import Int32\n"
            "def double(x: Int32) -> Int32:\n"
            "    return x + x\n"
            "def main() -> None:\n"
            "    fs: list[Callable[[Int32], Int32]] = []\n"
            "    fs.append(double)\n"
            "    f = fs[0]\n"
            "    print(f(3))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "fs.push_back(double_);" in out
        assert "::tpy::__getitem__(fs, 0)" in out
        assert faces.get("name.func_ref", 0) >= 1
        _assert_byte_identical(src)

    def test_closure_local_element_append_reads_bare_name(self):
        src = (
            "from typing import Callable\n"
            "from tpy import Int32\n"
            "def main() -> None:\n"
            "    fs: list[Callable[[Int32], Int32]] = []\n"
            "    n: Int32 = 2\n"
            "    def scale(x: Int32) -> Int32:\n"
            "        return x * n\n"
            "    fs.append(scale)\n"
            "    g = fs[0]\n"
            "    print(g(4))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "fs.push_back(scale);" in out
        assert faces.get("name.closure_local", 0) >= 1
        _assert_byte_identical(src)


class TestUnionElement:
    def test_union_member_ctor_append_routes(self):
        src = (
            "from tpy import Int32\n"
            "class Circle:\n"
            "    r: Int32\n"
            "    def __init__(self, r: Int32):\n"
            "        self.r = r\n"
            "class Rect:\n"
            "    w: Int32\n"
            "    def __init__(self, w: Int32):\n"
            "        self.w = w\n"
            "type Shape = Circle | Rect\n"
            "def main() -> None:\n"
            "    xs: list[Shape] = []\n"
            "    xs.append(Circle(1))\n"
            "    xs.append(Rect(2))\n"
            "    print(len(xs))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "xs.push_back(Circle(1));" in out
        _assert_byte_identical(src)

    def test_union_member_NAME_append_stays_ast(self):
        # A NAME source into a union element slot copies/moves where the ctor
        # rvalue is absorbed in place -- only the rvalue row is mirrored.
        src = (
            "from tpy import Int32\n"
            "class Circle:\n"
            "    r: Int32\n"
            "    def __init__(self, r: Int32):\n"
            "        self.r = r\n"
            "class Rect:\n"
            "    w: Int32\n"
            "    def __init__(self, w: Int32):\n"
            "        self.w = w\n"
            "type Shape = Circle | Rect\n"
            "def main() -> None:\n"
            "    xs: list[Shape] = []\n"
            "    c = Circle(1)\n"
            "    xs.append(c)\n"
            "    print(len(xs))\n"
            "main()\n"
        )
        _out, _faces, fallback = _gen_thir(src)
        assert fallback
        _assert_byte_identical(src)


class TestElementBoundaries:
    def test_pointer_repr_tuple_element_append_routes(self):
        # `list[tuple[P | None, ...]]` inserts through the
        # `tuple_to_storage_move` lift -- the consuming borrow-tuple row
        # (arg.own_btuple_literal) renders it byte-identically now.
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32):\n"
            "        self.v = v\n"
            "def main() -> None:\n"
            "    a = P(1)\n"
            "    pairs: list[tuple[P | None, P | None]] = []\n"
            "    pairs.append((a, None))\n"
            "    print(len(pairs))\n"
            "main()\n"
        )
        _out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert faces.get("arg.own_btuple_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_non_discarded_open_t_pop_routes(self):
        # A CONSUMED T result lands in the bare open type-param slot, which
        # takes the plain spelled copy: the slot has no borrow or storage form
        # to choose between until instantiation.
        src = (
            "from tpy import Int32\n"
            "class Bag[T]:\n"
            "    items: list[T]\n"
            "    def __init__(self):\n"
            "        self.items = []\n"
            "    def add(self, item: T) -> None:\n"
            "        self.items.append(item)\n"
            "    def take(self) -> T:\n"
            "        x = self.items.pop()\n"
            "        return x\n"
            "def main() -> None:\n"
            "    b = Bag[Int32]()\n"
            "    b.add(7)\n"
            "    print(b.take())\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert faces.get("decl.type_param_slot", 0) >= 1
        assert "T x = ::tpy::pop_back(this->items);" in out
        _assert_byte_identical(src)

    def test_reassigned_open_t_pop_stays_ast(self):
        # BOUNDARY, where the old one moved to: a REBOUND T local is a
        # rebind-slot pointer binding, not a copy.
        src = (
            "from tpy import Int32\n"
            "class Bag[T]:\n"
            "    items: list[T]\n"
            "    def __init__(self):\n"
            "        self.items = []\n"
            "    def add(self, item: T) -> None:\n"
            "        self.items.append(item)\n"
            "    def take(self) -> T:\n"
            "        x = self.items.pop()\n"
            "        x = self.items.pop()\n"
            "        return x\n"
            "def main() -> None:\n"
            "    b = Bag[Int32]()\n"
            "    b.add(7)\n"
            "    b.add(8)\n"
            "    print(b.take())\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        _assert_rejects_at(fallback, "body:stmt.var_decl",
                           "decl.slot_type")
        assert "T* x = &__slot_1;" in out
        _assert_byte_identical(src)
