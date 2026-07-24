"""Pins for the ref-container tuple-unpack targets (os.walk-family loop
heads): a reference-family CONTAINER element in a for-head unpack aliases
via the type-agnostic `auto&& = unwrap_ref(tuple_elem_ref(...))` bind,
like the F1-record ref targets. Boundary: a PENDING-str unpack target at
an Own[str] sink rejects -- the AST's _is_str_view_source misses the view
binding there and takes the owned copy+move temp cascade (a pre-existing
latent divergence this reject fences until the AST side is reconciled)."""

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

    def test_pending_str_target_own_sink_rejects(self):
        # `seen.append(name)` on a str unpack target: the AST hoists
        # `std::string __tmp_N{name};` + move (its view bookkeeping misses
        # the target), so the S1 inline convert must not fire.
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
        assert any("arg_unpack_pending_view" in r for r in fallback)
        # The fallback body keeps the AST's cascade render.
        assert "std::string __tmp_1{name};" in out
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
