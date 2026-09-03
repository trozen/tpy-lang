"""Two rows an epoll reactor's poll loop needs.

A container FIELD read at a native/@cpp_template callee's plain container
ref slot (`bytes(self.buf)`, `unsafe_ptr(self.arr)`): the member read binds
the slot bare, exactly as the bare-NAME twin the shared row already carries.

A ValueType-record element read copied into a by-value slot
(`w = self._waiters[fd]`): a value record has no borrow form, so the checked
element lvalue copies straight into the storage slot -- unlike a reference
record, whose decl binds `const T&` on its own row.

Corpus witness: asyncio._executor `poll`.
"""

from __future__ import annotations

from .testutil import _assert_routes_byte_identical, _compile, _entry
from ..codegen_cpp import CodeGenOptions


_HOLDER = (
    "from tpy import Int32, ValueType, Array, Ptr, Own\n"
    "from tpy.unsafe import unsafe_ptr\n"
    "class W(ValueType):\n"
    "    k: Int32\n"
    "    def __init__(self, k: Int32) -> None:\n"
    "        self.k = k\n"
    "    def use(self) -> Int32:\n"
    "        return self.k\n"
    "class Node:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.v = v\n"
)


def _emit(src: str):
    compiler, modules = _compile(src)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False))
    return compiler, hpp + cpp


class TestContainerFieldAtNativeSlot:
    SRC = (_HOLDER
           + "class Holder:\n"
           + "    buf: bytearray\n"
           + "    arr: Array[Int32, 4]\n"
           + "    opt_names: list[str] | None\n"
           + "    def __init__(self, buf: Own[bytearray],\n"
           + "                 arr: Array[Int32, 4]) -> None:\n"
           + "        self.buf = buf\n"
           + "        self.arr = arr\n"
           + "        self.opt_names = None\n"
           + "    def blen(self) -> Int32:\n"
           + "        return Int32(len(bytes(self.buf)))\n"
           + "    def aptr(self) -> Ptr[Int32]:\n"
           + "        return unsafe_ptr(self.arr)\n")

    def test_routes(self):
        _assert_routes_byte_identical(self.SRC)
        compiler, code = _emit(self.SRC)
        assert compiler._thir_face_witnesses.get("arg.container_field", 0) >= 2
        assert "::tpy::__len__(::tpy::bytes_copy(this->buf));" in code
        assert "return this->arr.data();" in code

    def test_narrowed_optional_field_is_not_this_row(self):
        # BOUNDARY: the row is DECLARED-type keyed, so a narrowed
        # `Optional[container]` field -- whose read renders through the
        # unwrap -- must not reach it. It routes on another row; what this
        # pins is that it is not claimed here.
        src = (self.SRC
               + "    def opt_len(self) -> Int32:\n"
               + "        if self.opt_names is not None:\n"
               + "            return Int32(len(sorted(self.opt_names)))\n"
               + "        return 0\n")
        base, _ = _emit(self.SRC)
        with_opt, _ = _emit(src)
        assert (with_opt._thir_face_witnesses.get("arg.container_field", 0)
                == base._thir_face_witnesses.get("arg.container_field", 0))


class TestValueRecordElementRead:
    SRC = (_HOLDER
           + "class Holder:\n"
           + "    tags: dict[Int32, W]\n"
           + "    wl: list[W]\n"
           + "    def __init__(self) -> None:\n"
           + "        self.tags = {}\n"
           + "        self.wl = []\n"
           + "    def take_dict(self, k: Int32) -> Int32:\n"
           + "        w = self.tags[k]\n"
           + "        return w.use()\n"
           + "    def take_list(self, i: Int32) -> Int32:\n"
           + "        w = self.wl[i]\n"
           + "        return w.use()\n")

    def test_routes(self):
        _assert_routes_byte_identical(self.SRC)
        compiler, code = _emit(self.SRC)
        assert compiler._thir_face_witnesses.get(
            "subscript.value_record_elem", 0) >= 2
        assert "W w = ::tpy::__getitem__(this->tags, k);" in code
        assert "W w = ::tpy::__getitem__(this->wl, i);" in code

    def test_reference_record_element_keeps_its_borrow_row(self):
        # BOUNDARY: a reference record's decl binds `const Node&`, a
        # different render decided on the borrow row -- the value-record
        # copy row must not claim it.
        src = (_HOLDER
               + "class Bag:\n"
               + "    nodes: list[Node]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.nodes = []\n"
               + "    def take_ref(self, i: Int32) -> Int32:\n"
               + "        n = self.nodes[i]\n"
               + "        return n.v\n")
        _assert_routes_byte_identical(src)
        compiler, code = _emit(src)
        assert "const Node& n = ::tpy::__getitem__(this->nodes, i);" in code
        assert compiler._thir_face_witnesses.get(
            "subscript.value_record_elem", 0) == 0
