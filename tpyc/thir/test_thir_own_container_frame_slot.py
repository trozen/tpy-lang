"""An `Own[container]` binding held in a resumable frame SLOT reads and
indexes exactly like a plain container local: the payload lives in the
frame's optional cell, so every body read is the bare `(*row)` peel and
nothing is ever moved out of it. Plus the generic family's composite open
slot fed by a FIELD read (`_parse_rows(self._fp, ..)` at `Ptr[R]` resolved
`Ptr[W]`), which the bare-`T` field row cannot reach."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_rejects_at, _assert_routes_byte_identical, _compile, _entry,
    _thir_ctx,
)


class TestGenericCompositeOpenSlotField:
    SRC = ("from typing import Protocol\n"
           "from tpy import Int32, Ptr\n"
           "class Src(Protocol):\n"
           "    def count(self) -> Int32: ...\n"
           "class Impl:\n"
           "    n: Int32\n"
           "    def __init__(self, n: Int32) -> None:\n"
           "        self.n = n\n"
           "    def count(self) -> Int32:\n"
           "        return self.n\n"
           "def total[R: Src](s: Ptr[R]) -> Int32:\n"
           "    return s.count()\n"
           "class Holder[W: Src]:\n"
           "    _src: Ptr[W]\n"
           "    def __init__(self, s: W) -> None:\n"
           "        self._src = s\n"
           "    def sum(self) -> Int32:\n"
           "        return total(self._src)\n"
           "def main() -> None:\n"
           "    h = Holder(Impl(5))\n"
           "    print(h.sum())\n"
           "main()\n")

    def test_field_at_composite_open_slot_routes(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "total<W>(this->_src)" in hpp + cpp

    def test_the_composite_field_row_fires(self):
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        faces = dict(compiler._thir_face_witnesses)
        assert faces["call.generic_open_slot_field_composite"] >= 1

    def test_chained_field_read_keeps_rejecting(self):
        # BOUNDARY: the row admits a ONE-level markers-clean field read. A
        # CHAINED receiver renders a different member nest and stays out.
        src = ("from typing import Protocol\n"
               "from tpy import Int32, Ptr\n"
               "class Src(Protocol):\n"
               "    def count(self) -> Int32: ...\n"
               "class Impl:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "    def count(self) -> Int32:\n"
               "        return self.n\n"
               "def total[R: Src](s: Ptr[R]) -> Int32:\n"
               "    return s.count()\n"
               "class Inner[W: Src]:\n"
               "    src: Ptr[W]\n"
               "    def __init__(self, s: W) -> None:\n"
               "        self.src = s\n"
               "class Holder[W: Src]:\n"
               "    _inner: Inner[W]\n"
               "    def __init__(self, s: W) -> None:\n"
               "        self._inner = Inner(s)\n"
               "    def sum(self) -> Int32:\n"
               "        return total(self._inner.src)\n"
               "def main() -> None:\n"
               "    h = Holder(Impl(5))\n"
               "    print(h.sum())\n"
               "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call",
                           shape="call.generic_arg_slot")


class TestOwnContainerFrameSlotReads:
    SRC = ("from tpy import Int32, Own, Iterator\n"
           "def rows(n: Int32) -> Iterator[Own[list[str]]]:\n"
           "    i = 0\n"
           "    while True:\n"
           "        if i >= n:\n"
           "            return\n"
           "        row: list[str] = []\n"
           '        row.append("a")\n'
           '        row.append("b")\n'
           "        yield row\n"
           "        i += 1\n"
           "def widths(n: Int32, out: list[str]) -> Iterator[Int32]:\n"
           "    started = False\n"
           "    for row in rows(n):\n"
           "        if not started:\n"
           "            started = True\n"
           "            yield Int32(0)\n"
           "        m = len(row)\n"
           "        h = 0\n"
           "        while h < m:\n"
           "            out.append(row[h])\n"
           "            h += 1\n"
           "        yield Int32(m)\n"
           "def main() -> None:\n"
           "    out: list[str] = []\n"
           "    for w in widths(2, out):\n"
           "        print(w)\n"
           "    print(len(out))\n"
           "main()\n")

    def test_frame_slot_reads_and_indexes_route(self):
        _, cpp = _assert_routes_byte_identical(self.SRC)
        # Every read is the bare slot peel -- no move out of the frame cell.
        assert "m = ::tpy::__len__((*row));" in cpp
        assert "out.push_back(::tpy::__getitem__((*row), h));" in cpp
        assert "std::move((*row))" not in cpp

    def test_the_frame_own_read_face_fires(self):
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        faces = dict(compiler._thir_face_witnesses)
        assert faces["name.frame_own_field"] >= 1

    def test_own_container_param_element_read_routes(self):
        # The same peel outside a frame: an `Own[list[str]]` PARAM indexes
        # through the identical `__getitem__` lvalue at an `Own[str]`
        # element slot -- ownership of the buffer says nothing about how an
        # element reads.
        src = ("from tpy import Int32, Own\n"
               "def f(row: Own[list[str]], out: list[str]) -> Int32:\n"
               "    out.append(row[0])\n"
               "    return Int32(len(out))\n"
               "def main() -> None:\n"
               "    out: list[str] = []\n"
               '    print(f(["a"], out))\n'
               "main()\n")
        _, cpp = _assert_routes_byte_identical(src)
        assert "out.push_back(::tpy::__getitem__(row, 0));" in cpp

    def test_simple_generator_own_container_param_still_rejects(self):
        # BOUNDARY: the frame-slot clause is scoped to the RESUMABLE leaf.
        # A simple generator's `Own[container]` binding flips the skeleton's
        # iterable-strategy classification, so it keeps its own reject.
        src = ("from tpy import Own, Iterator\n"
               "def each(items: Own[list[str]]) -> Iterator[str]:\n"
               "    for s in items:\n"
               "        yield s\n"
               "def main() -> None:\n"
               '    for s in each(["a", "b"]):\n'
               "        print(s)\n"
               "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:sgen.iterable_own_binding")
