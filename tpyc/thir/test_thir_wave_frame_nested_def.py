"""Pins for the frame nested-def marker statement: inside a RESUMABLE
body a nested `def` is a frame MEMBER (declared + emitted by the
gen_async scaffolding), so the leaf statement lowers to the marker line
(`// def g: frame member`) and the name registers up front for call
sites in any resume case. The SYNC lambda lowering is untouched."""

from __future__ import annotations

from .testutil import _assert_byte_identical, _compile, _entry


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp, compiler._thir_face_witnesses


class TestFrameNestedDef:
    def test_async_nested_def_routes_marker(self):
        src = (
            "import asyncio\n"
            "async def outer() -> int:\n"
            "    await asyncio.sleep(0)\n"
            "    def g() -> int:\n"
            "        return 5\n"
            "    return g()\n"
            "def main() -> None:\n"
            "    print(asyncio.run(outer()))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("res.nested_def_member", 0) >= 1
        assert "// def g: frame member" in out
        _assert_byte_identical(src)

    def test_sync_nested_def_still_lambda(self):
        # The sync lowering keeps the inline lambda -- the marker arm is
        # frame-scoped only.
        src = (
            "def outer() -> int:\n"
            "    def g() -> int:\n"
            "        return 5\n"
            "    return g()\n"
            "def main() -> None:\n"
            "    print(outer())\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert not faces.get("res.nested_def_member")
        assert "auto g = []" in out
        _assert_byte_identical(src)

    def test_call_before_def_cfg_order_routes(self):
        # A resume case reaches the call site with the def statement in a
        # later source position relative to the suspension -- the up-front
        # registration (collect_frame_nested_defs) makes the name
        # resolvable from every case.
        src = (
            "import asyncio\n"
            "async def outer(n: int) -> int:\n"
            "    if n > 0:\n"
            "        await asyncio.sleep(0)\n"
            "    def h() -> int:\n"
            "        return 3\n"
            "    return h() + n\n"
            "def main() -> None:\n"
            "    print(asyncio.run(outer(2)))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert "// def h: frame member" in out
        _assert_byte_identical(src)


class TestFrameNestedDefMarkerName:
    def test_keyword_colliding_name_keeps_python_spelling(self):
        # The marker line spells the PYTHON name: a keyword-colliding def
        # (`double` -> C++ member `double_`) stays `double` in the comment,
        # matching the AST's unescaped `// def {func.name}` render.
        src = (
            "import asyncio\n"
            "async def outer() -> int:\n"
            "    n = 2\n"
            "    def double() -> int:\n"
            "        return n * 2\n"
            "    await asyncio.sleep(0)\n"
            "    return double()\n"
            "def main() -> None:\n"
            "    print(asyncio.run(outer()))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("res.nested_def_member", 0) >= 1
        assert "// def double: frame member" in out
        assert "// def double_: frame member" not in out
        _assert_byte_identical(src)
