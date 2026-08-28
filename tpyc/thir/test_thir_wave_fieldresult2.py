"""Field result-gate rows: whole value-opt field sources at the
value-opt field-write sink, the copy() container-field source, the
stored-awaitable field at the BORROWED suspend operand, and the
str-family field read into a str frame field -- plus the boundaries
that stay AST."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _lower_ctx_witnessed, _fn, _compile, _entry,
    _assert_routes_byte_identical,
)


def _gen(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=True,
                                      thir_codegen=thir))
    return compiler, hpp, cpp


def _assert_identical(src: str) -> 'tuple[dict, dict]':
    """Byte-compare THIR vs AST output; return (witnesses, fallback) --
    the frame-lowering twin of _assert_routes_byte_identical (testutil's
    lower_module does not drive resumable/generator frames)."""
    _, hpp_ast, cpp_ast = _gen(src, thir=False)
    c, hpp_thir, cpp_thir = _gen(src, thir=True)
    assert hpp_ast == hpp_thir
    assert cpp_ast == cpp_thir
    return c._thir_face_witnesses, c._thir_fallback


class TestWholeOptFieldWriteSource:
    """`b.value = b.value` -- a whole value-repr Optional FIELD source at
    the VALUE_OPT field-write sink copies the `std::optional<T>` member
    bare (the same rule the ptr-repr tail applies to its field sources)."""
    _SRC = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    value: Int32 | None\n"
        "    def __init__(self, v: Int32):\n        self.value = v\n"
        "def rewrite(a: Box, b: Box) -> None:\n"
        "    a.value = b.value\n"
        "def main() -> None:\n"
        "    a = Box(1)\n    b = Box(2)\n"
        "    rewrite(a, b)\n    print(a.value)\n"
        "main()\n")

    def test_field_source_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "rewrite") is not None
        assert w.get("field_write.value_opt_scalar", 0) >= 1
        assert w.get("field.whole_optional", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "a.value = b.value;" in cpp

    def test_subscript_source_stays_ast(self):
        # The admission is FIELD sources only: a whole-opt SUBSCRIPT
        # source at the same sink keeps deferring (its optional-element
        # read has no admitted arm).
        src = (
            "from tpy import Int32\n"
            "class Box:\n"
            "    value: Int32 | None\n"
            "    def __init__(self, v: Int32):\n        self.value = v\n"
            "def put(b: Box, xs: list[Int32 | None]) -> None:\n"
            "    b.value = xs[0]\n"
            "def main() -> None:\n"
            "    b = Box(1)\n    xs: list[Int32 | None] = [7]\n"
            "    put(b, xs)\n    print(b.value)\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert "body:stmt.assign:subscript.elem.optional" in fallback


class TestCopyContainerField:
    """`copy(c.items)` -- a container FIELD source through the copy()
    general tail (`std::vector<T>(c.items)`); the bare member read is the
    BORROW_BIND copy-source use. A pointer-local receiver rides the same
    row: the field arm renders its deref, probed byte-identical."""
    _SRC = (
        "from tpy import Int32, Own, copy\n"
        "class Holder:\n"
        "    items: list[Int32]\n"
        "    def __init__(self) -> None:\n        self.items = [1, 2]\n"
        "def snap(h: Holder) -> Own[list[Int32]]:\n"
        "    return copy(h.items)\n"
        "def main() -> None:\n"
        "    h = Holder()\n"
        "    xs = snap(h)\n    xs.append(3)\n"
        "    print(len(h.items))\n    print(len(xs))\n"
        "main()\n")

    def test_field_source_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "snap") is not None
        assert w.get("call.copy_container", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "return std::vector<int32_t>(h.items);" in cpp

    def test_pointer_receiver_routes(self):
        # A narrowed `Holder | None` receiver is a pointer local; the
        # field read's own deref arm renders `(*h).items` inside the
        # same copy tail.
        src = (
            "from tpy import Int32, Own, copy\n"
            "class Holder:\n"
            "    items: list[Int32]\n"
            "    def __init__(self) -> None:\n        self.items = [1, 2]\n"
            "def snap(h: Holder | None) -> Own[list[Int32]]:\n"
            "    if h is None:\n        return []\n"
            "    return copy(h.items)\n"
            "def main() -> None:\n"
            "    h = Holder()\n"
            "    print(len(snap(h)))\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback
        assert w.get("call.copy_container", 0) >= 1


class TestSuspendFieldOperand:
    """`await self.evt` -- a stored-awaitable F1-record field at the
    BORROWED suspend operand renders the bare member read inside the
    skeleton's `&(..)` wrap. A nested field-chain operand rides the same
    row (the receiver ladder owns the chain); the row's boundary is the
    F1 family itself -- a non-record awaitable field is out by type."""
    _SRC = (
        "import asyncio\n"
        "from asyncio import Event\n"
        "class Gate:\n"
        "    evt: Event\n"
        "    def __init__(self) -> None:\n        self.evt = Event()\n"
        "    def open(self) -> None:\n        self.evt.set()\n"
        "    async def passed(self) -> bool:\n"
        "        await self.evt\n"
        "        return True\n"
        "async def opener(g: Gate) -> None:\n"
        "    g.open()\n"
        "async def run() -> None:\n"
        "    g = Gate()\n"
        "    t = asyncio.create_task(opener(g))\n"
        "    ok = await g.passed()\n"
        "    print(ok)\n"
        "    await t\n"
        "def main() -> None:\n    asyncio.run(run())\nmain()\n")

    def test_self_field_operand_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert w.get("field.suspend_borrow", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(self._SRC, thir=True)
        assert "= &(__self.evt);" in cpp

    def test_nested_field_chain_operand_routes(self):
        src = (
            "import asyncio\n"
            "from asyncio import Event\n"
            "class Inner:\n"
            "    evt: Event\n"
            "    def __init__(self) -> None:\n        self.evt = Event()\n"
            "class Outer:\n"
            "    inner: Inner\n"
            "    def __init__(self) -> None:\n        self.inner = Inner()\n"
            "async def waiter(o: Outer) -> None:\n"
            "    await o.inner.evt\n"
            "async def run() -> None:\n"
            "    o = Outer()\n"
            "    o.inner.evt.set()\n"
            "    await waiter(o)\n"
            "def main() -> None:\n    asyncio.run(run())\nmain()\n")
        w, fallback = _assert_identical(src)
        assert w.get("field.suspend_borrow", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)


class TestStrFieldFrameReassign:
    """A narrowed `str | None` FIELD read reassigning a str frame local
    (`q = self.s` -> `q = (*__self.s);`): the frame-field assign threads
    the same str-family admission the sync decl sink has -- and the bytes
    field rides the identical row, both view families reading the member
    bare."""
    _SRC = (
        "from typing import Iterator\n"
        "class Box:\n"
        "    s: str | None\n"
        "    def __init__(self, v: str | None) -> None:\n        self.s = v\n"
        "    def gen(self) -> Iterator[str]:\n"
        "        q = \"\"\n"
        "        if self.s is not None:\n"
        "            q = self.s\n"
        "        yield \"start\"\n"
        "        yield q\n"
        "def main() -> None:\n"
        "    for x in Box(\"v\").gen():\n        print(x)\n"
        "main()\n")

    def test_narrowed_str_field_reassign_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert w.get("field.narrowed_deref", 0) >= 1
        assert not fallback
        _, _hpp, cpp = _gen(self._SRC, thir=True)
        assert "q = (*__self.s);" in cpp

    def test_bytes_field_frame_reassign_routes(self):
        src = (
            "from typing import Iterator\n"
            "class Box:\n"
            "    b: bytes\n"
            "    def __init__(self, v: bytes) -> None:\n        self.b = v\n"
            "    def gen(self) -> Iterator[bytes]:\n"
            "        q = b\"\"\n"
            "        q = self.b\n"
            "        yield \"start\".encode()\n"
            "        yield q\n"
            "def main() -> None:\n"
            "    for x in Box(b\"v\").gen():\n        print(x)\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback
        assert w.get("print.bytes_field", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "q = __self.b;" in cpp
