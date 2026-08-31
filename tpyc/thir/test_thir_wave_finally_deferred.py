"""The finally-deferred return cell: `THIRFinallyDeferredReturn` mirroring
`_deferred_return_recipe`'s two-recipe table.

Shape A -- Own[T] return of a reference-type local: `auto* p = &(lvalue);`
before the inline finally chain, `return std::move(*p);` after, so finally
mutations stay visible in the returned object (CPython's pending return is
an alias). The lvalue derefs `(*name)` for a pointer local.
Shape B -- pointer-repr Optional local into the storage-Optional slot:
`auto* p = name;` ... `return ::tpy::ptr_to_optional_move(p);`.

A resumable frame shares the table through its leaf seam: the Poll wrap and
done-state transition around the capture stay skeleton, and the frame's own
name spelling (a frame slot reads `(*name)`) rides the capture expression, so
a C++-local shadow of the field suppresses the peel at emit exactly where the
AST's does.

Boundaries: value-type locals keep the EAGER `__tpy_ret_N` / `__tpy_async_ret`
capture (sema never stamps them), and a stamped return whose spelling the
table does not cover rejects the whole body rather than mirroring the AST's
emit-time retraction of the sema move mark. No source shape reaches those
rename guards today -- they are fail-closed -- so what is pinned below is the
seam's refusal to invent a recipe it was not handed.
"""

import pytest

from .emit import ResumableLeafEmitter, THIRCodeGenError
from .nodes import THIRResumableBody
from .testutil import _assert_routes_byte_identical

_BOX = (
    "from tpy import Int32, Own\n"
    "class Box:\n"
    "    n: Int32\n"
    "    def __init__(self) -> None:\n        self.n = 10\n"
)


class TestShapeAOwnReturn:
    SRC = (
        _BOX
        + "def ret_record() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n        return b\n"
        + "    finally:\n        b.n += 1\n"
        + "def nested_frames() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n"
        + "        try:\n            return b\n"
        + "        finally:\n            b.n += 1\n"
        + "    finally:\n        b.n += 2\n"
        + "def override() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n        return b\n"
        + "    finally:\n"
        + "        c = Box()\n        c.n = 99\n        return c\n"
        + "def main() -> None:\n"
        + "    print(ret_record().n)\n"
        + "    print(nested_frames().n)\n"
        + "    print(override().n)\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "auto* __tpy_retp_0 = &(b);" in cpp
        assert "return std::move(*__tpy_retp_0);" in cpp
        # The terminating finally keeps the evaluated-but-overridden capture.
        assert "[[maybe_unused]] auto* __tpy_retp_0 = &(b);" in cpp


class TestShapeBOptionalReturn:
    SRC = (
        _BOX
        + "def ret_optional(flag: bool) -> Own[Box] | None:\n"
        + "    b: Box | None = None\n"
        + "    if flag:\n        b = Box()\n"
        + "    try:\n        return b\n"
        + "    finally:\n"
        + "        if b is not None:\n            b.n += 1\n"
        + "def main() -> None:\n"
        + "    r = ret_optional(True)\n"
        + "    if r is not None:\n        print(r.n)\n"
        + "    print(ret_optional(False) is None)\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "auto* __tpy_retp_0 = b;" in cpp
        assert "return ::tpy::ptr_to_optional_move(__tpy_retp_0);" in cpp


class TestWithFrameCrossing:
    SRC = (
        _BOX
        + "class Ctx:\n"
        + "    def __enter__(self) -> Int32:\n        return 1\n"
        + "    def __exit__(self, a, b, c) -> None:\n        pass\n"
        + "def with_frame() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    with Ctx():\n"
        + "        try:\n            return b\n"
        + "        finally:\n            b.n += 1\n"
        + "def main() -> None:\n    print(with_frame().n)\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The chain walks BOTH frames: the try's finally body, then the
        # with's __exit__ -- after the capture, before the materialize.
        assert "auto* __tpy_retp_0 = &(b);" in cpp
        assert "__exit__" in cpp


class TestErrorReturnDeferred:
    SRC = (
        "from tpy import Int32, Own, ReturnException, error_return\n"
        "class NoGood(Exception, ReturnException):\n    pass\n"
        + _BOX.replace("from tpy import Int32, Own\n", "")
        + "@error_return(NoGood)\n"
        + "def er_deferred(flag: bool) -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    if flag:\n        raise NoGood\n"
        + "    try:\n        return b\n"
        + "    finally:\n        b.n += 1\n"
        + "def main() -> None:\n"
        + "    try:\n        print(er_deferred(False).n)\n"
        + "    except NoGood:\n        print('err')\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The deferred move implicitly constructs the std::expected slot.
        assert "return std::move(*__tpy_retp_0);" in cpp


class TestValueTypeStaysEager:
    SRC = (
        "from tpy import Int32\n"
        "def ret_value() -> Int32:\n"
        "    n: Int32 = 10\n"
        "    try:\n        return n\n"
        "    finally:\n        n += 1\n"
        "def ret_str() -> str:\n"
        "    s = 'abc'\n"
        "    try:\n        return s\n"
        "    finally:\n        s = 'changed'\n"
        "def main() -> None:\n"
        "    print(ret_value())\n    print(ret_str())\n"
        "main()\n"
    )

    def test_eager_capture_no_deferred_pointer(self):
        # Value types are never sema-stamped: the eager signature-typed temp
        # stays, and no `__tpy_retp` capture may appear.
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "__tpy_retp" not in cpp
        assert "__tpy_ret_0" in cpp


class TestResumableDeferredReturn:
    """The same two recipes inside a resumable frame, sourced from the lowered
    body through the leaf seam."""

    SRC = (
        _BOX
        + "import asyncio\n"
        # Shape A off a FRAME SLOT: the local lives in the frame, so its own
        # render is `(*b)` and the capture wraps that, not a pointer.
        + "async def ret_frame_slot() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n        return b\n"
        + "    finally:\n        b.n += 1\n"
        # Shape A off an Own[T] PARAM, a plain frame field read bare.
        + "async def ret_own_param(b: Own[Box]) -> Own[Box]:\n"
        + "    try:\n        return b\n"
        + "    finally:\n        b.n += 1\n"
        # Shape B: the pointer-repr Optional frame local into the storage
        # Optional slot.
        + "async def ret_optional(flag: bool) -> Own[Box] | None:\n"
        + "    b: Box | None = None\n"
        + "    if flag:\n        b = Box()\n"
        + "    try:\n        return b\n"
        + "    finally:\n"
        + "        if b is not None:\n            b.n += 1\n"
        # A return NESTED in a non-suspending leaf compound reaches the same
        # recipe through the leaf-return hook rather than a CFG terminator.
        + "async def ret_nested(flag: bool) -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n"
        + "        if flag:\n            return b\n"
        + "        b.n += 5\n"
        + "        return b\n"
        + "    finally:\n        b.n += 1\n"
        + "async def run() -> None:\n"
        + "    print((await ret_frame_slot()).n)\n"
        + "    print((await ret_own_param(Box())).n)\n"
        + "    o = await ret_optional(True)\n"
        + "    if o is not None:\n        print(o.n)\n"
        + "    print((await ret_nested(True)).n)\n"
        + "def main() -> None:\n    asyncio.run(run())\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The frame slot's own `(*b)` peel, then the address-of around it.
        assert "auto* __tpy_retp_0 = &((*b));" in cpp
        assert "std::move(*__tpy_retp_0)" in cpp
        # The Own[T] param is a plain frame field: no peel.
        assert "auto* __tpy_retp_0 = &(b);" in cpp
        assert "auto* __tpy_retp_0 = b;" in cpp
        assert "::tpy::ptr_to_optional_move(__tpy_retp_0)" in cpp


class TestResumableSuspendingTryDeferred:
    """A suspension inside the try lifts the finally into a helper member; the
    capture still binds before the helper call and materializes after it."""

    SRC = (
        _BOX
        + "import asyncio\n"
        + "async def step() -> Int32:\n    return 1\n"
        + "async def ret_after_await() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n"
        + "        b.n += await step()\n"
        + "        return b\n"
        + "    finally:\n        b.n += 1\n"
        + "async def run() -> None:\n"
        + "    print((await ret_after_await()).n)\n"
        + "def main() -> None:\n    asyncio.run(run())\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "auto* __tpy_retp_0 = &((*b));" in cpp
        assert "this->__finally_0();" in cpp
        assert "std::move(*__tpy_retp_0)" in cpp


class TestResumableUnstampedStaysEager:
    """The adjacent shapes sema does not stamp keep the frame's EAGER
    `__tpy_async_ret` capture -- the seam must not manufacture a deferral for
    a return that never asked for one.

    A suspending finally is the interesting one: liveness suppresses the stamp
    there, which is what keeps the pending-return slot's eager store correct
    (its copy-vs-alias gap is tracked in BUGS.md, and is not this seam's)."""

    SRC = (
        _BOX
        + "import asyncio\n"
        + "async def step() -> Int32:\n    return 1\n"
        + "async def ret_value() -> Int32:\n"
        + "    n: Int32 = 10\n"
        + "    try:\n        return n\n"
        + "    finally:\n        n += 1\n"
        + "async def ret_suspending_finally() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n        return b\n"
        + "    finally:\n        b.n += await step()\n"
        + "async def run() -> None:\n"
        + "    print(await ret_value())\n"
        + "    print((await ret_suspending_finally()).n)\n"
        + "def main() -> None:\n    asyncio.run(run())\n"
        + "main()\n"
    )

    def test_no_deferred_capture(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "__tpy_retp" not in cpp
        assert "int32_t __tpy_async_ret_0 = n;" in cpp
        assert "this->__finally_ret_0 = (*b);" in cpp


class TestResumableSeamRefusesToInventARecipe:
    """A routed body's stamped return ALWAYS has a recipe -- lowering rejects
    the body when the table does not cover its spelling. So a seam lookup that
    finds nothing is a lowering/seam disagreement, and raising is the only
    correct answer: falling through to the eager arms would move storage the
    finally chain still reads, which no gate downstream can see."""

    def test_missing_recipe_raises(self):
        empty = THIRResumableBody(leaves={}, conds={}, await_args={},
                                  return_values={})
        leaf = ResumableLeafEmitter(empty)
        with pytest.raises(THIRCodeGenError, match="deferred return recipe"):
            leaf.render_deferred_return(object())
