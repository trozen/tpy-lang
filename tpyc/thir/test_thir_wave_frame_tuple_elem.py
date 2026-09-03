"""Non-value tuple elements inside a RESUMABLE frame-emplace container
literal.

At a sync position a `tuple[Int32, Box]` element stores through
`tuple_to_storage<S>(S{...})`. The resumable frame emplaces an already
storage-typed brace (`items.emplace(std::vector<S>{S{1, Box(5)}})`), so the
AST applies no per-element wrap there -- but only on the LIST/Array element
axis: a dict VALUE keeps the wrap even inside a frame, which is the same
`retype_scalars` split that makes list elements render bare and dict/set ones
target-typed.
"""

from __future__ import annotations

from .testutil import _compile, _entry, _assert_byte_identical
from ..codegen_cpp import CodeGenOptions


def _thir_cpp(src: str) -> 'tuple[str, dict, dict]':
    """(cpp, fallback, witnesses). A routed RESUMABLE never appears in
    `thir.functions`, so the fallback map and the face witnesses are the only
    way to tell a routed body from one that fell back and re-emitted the
    identical AST text."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return cpp, compiler._thir_face_witnesses


_BOX = ("from typing import Iterator\n"
        "from tpy import Int32\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n        self.val = v\n")


class TestFrameTupleElement:
    def test_list_element_emplaces_bare(self):
        src = _BOX + ("def gen() -> Iterator[Int32]:\n"
                      "    items: list[tuple[Int32, Box]] = [(1, Box(5))]\n"
                      "    yield len(items)\n")
        cpp, faces = _thir_cpp(src)
        assert faces.get("containerlit.tuple_frame_elem")
        assert ("items.emplace(std::vector<std::tuple<int32_t, Box>>"
                "{std::tuple<int32_t, Box>{1, Box(5)}});") in cpp
        assert "tuple_to_storage" not in cpp
        _assert_byte_identical(src)

    def test_array_element_emplaces_bare_too(self):
        # The fixed-size `Array[T, N]` an unannotated literal-seeded local
        # takes: it RETYPES its scalars where a vector element does not, so
        # keying the row on that axis silently wrapped this one.
        src = _BOX + ("def gen() -> Iterator[Int32]:\n"
                      "    items = [(1, Box(5))]\n"
                      "    yield len(items)\n")
        cpp, faces = _thir_cpp(src)
        assert faces.get("containerlit.tuple_frame_elem")
        assert ("items.emplace(std::array<std::tuple<int32_t, Box>, 1>"
                "{std::tuple<int32_t, Box>{1, Box(5)}});") in cpp
        _assert_byte_identical(src)

    def test_dict_value_keeps_the_storage_wrap_in_a_frame(self):
        # The boundary the sequence arm must not swallow.
        src = _BOX + ("def gen() -> Iterator[Int32]:\n"
                      "    m: dict[str, tuple[Int32, Box]] = "
                      "{\"a\": (1, Box(5))}\n"
                      "    yield len(m)\n")
        cpp, faces = _thir_cpp(src)
        assert not faces.get("containerlit.tuple_frame_elem")
        assert ("::tpy::tuple_to_storage<std::tuple<int32_t, Box>>"
                "(std::tuple<int32_t, Box>{1, Box(5)})") in cpp
        _assert_byte_identical(src)

    def test_sync_list_element_keeps_the_storage_wrap(self):
        src = _BOX + ("def f() -> Int32:\n"
                      "    items: list[tuple[Int32, Box]] = [(1, Box(5))]\n"
                      "    return len(items)\n")
        cpp, faces = _thir_cpp(src)
        assert not faces.get("containerlit.tuple_frame_elem")
        assert "::tpy::tuple_to_storage<std::tuple<int32_t, Box>>" in cpp
        _assert_byte_identical(src)

    def test_value_tuple_element_unaffected(self):
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n"
               "def gen() -> Iterator[Int32]:\n"
               "    pairs: list[tuple[Int32, Int32]] = [(1, 2)]\n"
               "    yield len(pairs)\n")
        _, faces = _thir_cpp(src)
        # A VALUE tuple takes the value-tuple arm, never this row.
        assert not faces.get("containerlit.tuple_frame_elem")
        _assert_byte_identical(src)
