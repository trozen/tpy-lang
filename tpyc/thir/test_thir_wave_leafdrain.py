"""The resumable-leaf temp-drain wave.

The drain already existed at the emit layer: emit_leaf_stmt renders through
the same render-then-flush statement arms as a sync body, on the ctx-backed
TempSink (shared `__tmp_N` numbering) -- exactly where the AST hoists
`auto __tmp_1 = (*p);` inside the case block. The wave opened the LOWERING
gates: the leaf frame-field assign init lowers with allow_temps (the copy
half of the Own-slot cascade lands), and the resumable return tail lowers
its value with the sync return arm's STORAGE use plus the `return copy(x)`
interception row. The routing half of the assign drain is pinned in
test_thir_wave_grind12.TestAwaitBoundCopyRoutes.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile,
    _entry,
)


def _gen(source):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    outs = {}
    for flag in (False, True):
        outs[flag] = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=flag))
    return compiler, outs[False], outs[True]


_PRE = (
    "import asyncio\n"
    "from tpy import Int32, Own\n"
    "class Payload:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "async def make(n: Int32) -> Own[Payload]:\n"
    "    return Payload(n)\n"
    "def size_of(p: Own[Payload]) -> Int32:\n"
    "    return p.n\n"
)


class TestReturnCopyRecordRoutes:
    # `return copy(self)` in a resumable method: the copy-construct rvalue
    # lands bare in the scaffolding's storage decl
    # (`C __tpy_async_ret = C(__self);`) -- no temp involved; the sync
    # return sink's interception row, taken before the generic tail.
    SRC = (
        "import asyncio\n"
        "from tpy import Own, copy\n"
        "class C:\n"
        "    v: int\n"
        "    def __init__(self) -> None:\n"
        "        self.v = 1\n"
        "    async def snapshot(self) -> Own[\"C\"]:\n"
        "        await asyncio.sleep(0)\n"
        "        return copy(self)\n"
        "async def main_coro() -> None:\n"
        "    c = C()\n"
        "    r = await c.snapshot()\n"
        "    print(r.v)\n"
        "def main() -> None:\n"
        "    asyncio.run(main_coro())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "C __tpy_async_ret = C(__self);" in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("res.return_copy_record", 0) >= 1


class TestCopyTempAtReturnSeamDefers:
    # BOUNDARY: the return seam keeps allow_temps OFF -- the skeleton
    # composes the value render into its own scaffolding line and no oracle
    # has verified a flush point there. A non-last-use Own-arg copy INSIDE
    # the return value needs the hoisted `__tmp_N`, so the body defers.
    SRC = (
        _PRE +
        "async def f() -> Int32:\n"
        "    p = await make(1)\n"
        "    return size_of(p) + p.n\n"
        "def main() -> None:\n"
        "    print(asyncio.run(f()))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb.get("resumable:expr.call") == 1, fb


class TestFrameSlotEmplaceTempDefers:
    # BOUNDARY: the frame_slot emplace write keeps allow_temps OFF (the
    # emplace arg composes inside the skeleton's `xs.emplace(...)` line --
    # widening it is its own rung needing an oracle). A copy-arg call as
    # the slot init still defers.
    SRC = (
        _PRE +
        "def wrap(p: Own[Payload]) -> Own[list[Int32]]:\n"
        "    return [p.n]\n"
        "async def g() -> Int32:\n"
        "    p = await make(2)\n"
        "    xs = wrap(p)\n"
        "    extra = size_of(p)\n"
        "    await asyncio.sleep(0)\n"
        "    return xs[0] + extra\n"
        "def main() -> None:\n"
        "    print(asyncio.run(g()))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb.get("resumable:expr.call") == 1, fb
