"""Pins for the ref-container tuple-unpack targets (os.walk-family loop
heads): a reference-family CONTAINER element in a for-head unpack aliases
via the type-agnostic `auto&& = unwrap_ref(tuple_elem_ref(...))` bind,
like the F1-record ref targets. A PENDING-str unpack target at an owned-str
sink routes and spells the same `std::string(x)` convert as a str param:
the AST resolves the pending type at its `var_types` lookup, so the target
is view-form to `_is_str_view_source` like any other view source."""

from __future__ import annotations

from .testutil import _assert_byte_identical, _compile, _entry


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp, compiler._thir_face_witnesses, compiler._thir_fallback


class TestUnpackRefContainers:
    def test_generator_list_ref_target_routes(self):
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def gen(xs: list[list[Int32]]) -> Iterator[tuple[Int32, list[Int32]]]:\n"
            "    i: Int32 = 0\n"
            "    for x in xs:\n"
            "        yield (i, x)\n"
            "        i += 1\n"
            "def main() -> None:\n"
            "    data: list[list[Int32]] = [[1], [2, 3]]\n"
            "    total: Int32 = 0\n"
            "    for i, lst in gen(data):\n"
            "        total += i + len(lst)\n"
            "    print(total)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        # gen's own body falls back on its list loop var (sgen lane) --
        # only the unpack head's routing is pinned here.
        assert set(fallback) <= {"body:sgen.loop_var_type"}
        assert faces.get("stmt.tuple_unpack.ref_target_iter", 0) >= 1
        assert "::tpy::unwrap_ref(::tpy::tuple_elem_ref(" in out
        _assert_byte_identical(src)

    def test_pending_str_target_own_sink_routes(self):
        # `seen.append(name)` on a str unpack target: the view-form source
        # takes the S1 inline convert, same as a str param at the slot.
        src = (
            "def main() -> None:\n"
            "    pairs: list[tuple[str, int]] = [(\"a\", 1), (\"b\", 2)]\n"
            "    seen: list[str] = []\n"
            "    for name, num in pairs:\n"
            "        seen.append(name)\n"
            "    print(len(seen), seen[0])\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "seen.push_back(std::string(name));" in out
        # The copy+move temp cascade the missed view binding used to force.
        assert "std::string __tmp_1{name};" not in out
        _assert_byte_identical(src)

    def test_str_target_reads_still_route(self):
        # Non-owned-sink reads of a str unpack target (compares, prints)
        # keep routing -- the reject is scoped to the Own[str] sinks.
        src = (
            "def main() -> None:\n"
            "    pairs: list[tuple[str, int]] = [(\"a\", 1), (\"b\", 2)]\n"
            "    for name, num in pairs:\n"
            "        if name == \"a\":\n"
            "            print(name, num)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        _assert_byte_identical(src)

    def test_pending_str_target_tuple_literal_elem_routes(self):
        # The container-literal sibling of the Own[str] sink: an unpack target
        # as a tuple-literal element at an owned-str slot takes the same S1
        # element convert the str-param element below does.
        src = (
            "def take(t: tuple[str, str]) -> int:\n"
            "    return len(t[0]) + len(t[1])\n"
            "def main() -> None:\n"
            "    src: tuple[str, str] = (\"ab\", \"c\")\n"
            "    x, y = src\n"
            "    print(take((x, y)))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "std::string(x)" in out
        _assert_byte_identical(src)

    def test_view_param_tuple_literal_elem_still_converts(self):
        # The boundary the unpack-target rows now match: a str PARAM at the
        # same element sink, whose owned copy both paths have always spelled.
        src = (
            "def take(t: tuple[str, str]) -> int:\n"
            "    return len(t[0]) + len(t[1])\n"
            "def main(a: str, b: str) -> None:\n"
            "    print(take((a, b)))\n"
            "main(\"ab\", \"c\")\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "std::string(a)" in out
        _assert_byte_identical(src)
