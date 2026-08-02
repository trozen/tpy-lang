"""Wave 12 of the grind loop: the temp-free Own-slot MOVE at flushless
plain-call positions.

`_own_lvalue_arg` (the Own-slot copy+move gate row) is temps_ok-gated
because its COPY half hoists a `__tmp_N`; the MOVE half renders
`std::move(<name read>)` position-independently and already had a
flushless rescue on the native/template branch. The plain branch gains
the same rescue, unblocking the await-bound frame local at an `Own[T]`
param inside a resumable leaf (`take(std::move((*p)))` -- the name's
own read supplies the deref).
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
    "def take(p: Own[Payload]) -> Int32:\n"
    "    return p.n\n"
)


class TestAwaitBoundMoveIntoOwnParam:
    SRC = (
        _PRE +
        "async def into_call_arg() -> Int32:\n"
        "    p = await make(2)\n"
        "    return take(p)\n"
        "def main() -> None:\n"
        "    print(asyncio.run(into_call_arg()))\n"
        "main()\n"
    )

    def test_routes_byte_identical_with_deref_move(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "take(std::move((*p)));" in thir[1]
        assert compiler._thir_face_witnesses.get("move.own_last_use", 0) >= 1


class TestAwaitBoundCopyRoutes:
    # The COPY half at the leaf assign: the temp-drain wave opened
    # allow_temps at the frame-field assign init, so the copy hoists its
    # `auto __tmp_1 = (*p);` through the leaf renderer's shared TempSink
    # (the same render-then-flush arm a sync body uses). The move rescue
    # still must not capture the NON-last-use name -- the copy, not a
    # move, is what keeps `p.items`/`p.n` readable afterwards.
    SRC = (
        _PRE +
        "def size_of(p: Own[Payload]) -> Int32:\n"
        "    return p.n\n"
        "async def used_again() -> Int32:\n"
        "    p = await make(5)\n"
        "    first = size_of(p)\n"
        "    return first + p.n\n"
        "def main() -> None:\n"
        "    print(asyncio.run(used_again()))\n"
        "main()\n"
    )

    def test_copy_shape_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "auto __tmp_1 = (*p);" in thir[1]
        assert "size_of(std::move(__tmp_1));" in thir[1]


class TestSyncFlushlessMove:
    # The rescue is kind-blind and not resumable-specific: a plain sync
    # local at an Own[T] param inside a CONDITION (a flushless position)
    # takes the same temp-free move.
    SRC = (
        "from tpy import Int32, Own\n"
        "class Payload:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "def take(p: Own[Payload]) -> Int32:\n"
        "    return p.n\n"
        "def main() -> None:\n"
        "    p = Payload(3)\n"
        "    if take(p) > 0:\n"
        "        print(1)\n"
        "main()\n"
    )

    def test_sync_condition_move_routes(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "take(std::move(p))" in thir[1]
        assert compiler._thir_face_witnesses.get("move.own_last_use", 0) >= 1
