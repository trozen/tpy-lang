"""A raw `Ptr[T]` param on a resumable frame.

The frame captures a Ptr param VALUE-kind, so the field is the bare `T*` the
sync param already spells and every leaf read takes the sync pointer rows --
the same standing the Ptr LOCAL slot already has. A pointee outside the
Ptr-value families keeps rejecting, on the frame param exactly as it does at
every other Ptr-value slot.
"""

from .testutil import (
    _reject_tally,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)
from ..codegen_cpp.context import CodeGenOptions


def _reject_tags(src: str) -> dict:
    return _reject_tally(src)


class TestPtrGeneratorParam:
    # A `Ptr[R]` on a bounded type param, the shape a generator takes so a
    # borrowed-Ptr holder can drive it from inside another frame.
    SRC = (
        "from typing import Iterator, Protocol\n"
        "from tpy import Int32, Ptr, take_ptr\n"
        "class Readable(Protocol):\n"
        "    def readline(self) -> str: ...\n"
        "class Src:\n"
        "    buf: list[str]\n"
        "    i: Int32\n"
        "    def __init__(self, lines: list[str]) -> None:\n"
        "        self.buf = lines\n"
        "        self.i = 0\n"
        "    def readline(self) -> str:\n"
        "        if self.i >= len(self.buf):\n"
        "            return \"\"\n"
        "        s = self.buf[self.i]\n"
        "        self.i += 1\n"
        "        return s\n"
        "def rows[R: Readable](fp: Ptr[R]) -> Iterator[str]:\n"
        "    while True:\n"
        "        line = fp.readline()\n"
        "        if not line:\n"
        "            return\n"
        "        yield line\n"
        "def main() -> None:\n"
        "    s = Src([\"a\", \"b\"])\n"
        "    for r in rows(take_ptr(s)):\n"
        "        print(r)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        out = "".join(_assert_routes_byte_identical(self.SRC))
        # The capture is the sync spelling: a bare pointer field, read bare.
        assert "R* fp;" in out


class TestPtrCoroParam:
    # The coro half of the same admission: a concrete record pointee.
    SRC = (
        "import asyncio\n"
        "from tpy import Int32, Ptr, take_ptr\n"
        "class Counter:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 0\n"
        "    def bump(self) -> Int32:\n"
        "        self.n += 1\n"
        "        return self.n\n"
        "async def tick(c: Ptr[Counter]) -> Int32:\n"
        "    await asyncio.sleep(0)\n"
        "    return c.bump()\n"
        "def main() -> None:\n"
        "    k = Counter()\n"
        "    print(asyncio.run(tick(take_ptr(k))))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        out = "".join(_assert_routes_byte_identical(self.SRC))
        assert "Counter* c;" in out


class TestNonValuePointeeKeepsRejecting:
    """BOUNDARY: a pointee outside the Ptr-value families (StrView) has no
    render at any slot. Two slots refuse it and only the FIRST one a program
    reaches is reported, so each gets a fixture of its own -- a single program
    holding both asserts nothing about the second."""

    # A `Ptr[StrView]` LOCAL in the caller: the decl's slot type is the first
    # refusal, well before the frame is reached.
    LOCAL_SRC = (
        "from typing import Iterator\n"
        "from tpy import Int32, Ptr, StrView, take_ptr\n"
        "def scan(buf: Ptr[StrView]) -> Iterator[Int32]:\n"
        "    i: Int32 = 0\n"
        "    while True:\n"
        "        if i >= 2:\n"
        "            return\n"
        "        yield i\n"
        "        i += 1\n"
        "def main() -> None:\n"
        "    s: StrView = \"hi\"\n"
        "    p = take_ptr(s)\n"
        "    for v in scan(p):\n"
        "        print(v)\n"
        "main()\n"
    )

    # The frame param gate on its own: nothing in this program mentions the
    # pointee outside `scan`'s signature, so the frame's capture slot is the
    # only thing that can refuse it.
    FRAME_SRC = (
        "from typing import Iterator\n"
        "from tpy import Int32, Ptr, StrView\n"
        "def scan(buf: Ptr[StrView]) -> Iterator[Int32]:\n"
        "    i: Int32 = 0\n"
        "    while True:\n"
        "        if i >= 2:\n"
        "            return\n"
        "        yield i\n"
        "        i += 1\n"
        "def main() -> None:\n"
        "    print(1)\n"
        "main()\n"
    )

    def test_the_caller_local_decl_rejects(self):
        _assert_rejects_at(_reject_tags(self.LOCAL_SRC), "body:stmt.var_decl",
                           shape="decl.slot_type", count=1)

    def test_the_frame_param_slot_rejects(self):
        _assert_rejects_at(_reject_tags(self.FRAME_SRC),
                           "resumable:res.param_type", shape="ptr", count=1)
