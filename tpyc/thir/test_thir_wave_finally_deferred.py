"""The finally-deferred return cell: `THIRFinallyDeferredReturn` mirroring
`_deferred_return_recipe`'s two-recipe table.

Shape A -- Own[T] return of a reference-type local: `auto* p = &(lvalue);`
before the inline finally chain, `return std::move(*p);` after, so finally
mutations stay visible in the returned object (CPython's pending return is
an alias). The lvalue derefs `(*name)` for a pointer local.
Shape B -- pointer-repr Optional local into the storage-Optional slot:
`auto* p = name;` ... `return ::tpy::ptr_to_optional_move(p);`.

Boundaries: value-type locals keep the EAGER `__tpy_ret_N` capture (sema
never stamps them); a resumable body's deferred return keeps rejecting (the
`__tpy_retp` scaffolding there is the AST frame's, unmirrored).
"""

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)
from ..codegen_cpp import CodeGenOptions

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


class TestResumableKeepsRejecting:
    SRC = (
        _BOX
        + "import asyncio\n"
        + "async def ret_deferred() -> Own[Box]:\n"
        + "    b = Box()\n"
        + "    try:\n        return b\n"
        + "    finally:\n        b.n += 1\n"
        + "async def run() -> None:\n"
        + "    r = await ret_deferred()\n"
        + "    print(r.n)\n"
        + "def main() -> None:\n    asyncio.run(run())\n"
        + "main()\n"
    )

    def test_resumable_deferred_return_stays_ast(self):
        # The async frame's deferred-return scaffolding is unmirrored: the
        # body must fall back (byte-identically) at a resumable: seam, not
        # route through the sync recipe.
        _assert_byte_identical(self.SRC)
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=True,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        assert any(k.startswith("resumable:")
                   for k in compiler._thir_fallback), compiler._thir_fallback
