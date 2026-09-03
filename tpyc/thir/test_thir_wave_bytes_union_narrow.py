"""Pins for the bytes member of the WIDE ptr-variant-union class plus the
narrowed-alias Optional reseat it unblocks.

`_ptr_union_member_wide` gains bytes, so a `bytes | dict[str, str] | None`
subject reaches the isinstance narrowing, the print / arg / ternary rows and
the ptr-union decl the same way a str-membered union already does -- every
one of them spells members through `render_type` alone. The reseat arm is
the rung behind it: a narrowed union alias binds `M&`, so re-pointing a
slotless pointer-repr Optional off it takes the plain address-of.
"""

from __future__ import annotations

from .testutil import (
    _reject_tally,
    _assert_byte_identical,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
)

_HDR = "from tpy import Int32\n"

_SUBJ = (
    "def enc(data: bytes | dict[str, str] | None,\n"
    "        files: dict[str, Int32] | None) -> Int32:\n"
)


class TestBytesUnionNarrowing:
    def test_compound_narrow_and_alias_reseat_route(self):
        # The stdlib shape: the `&&` chain narrows a bytes-membered union to
        # its dict member, and the alias re-points a slotless `dict | None`.
        src = _HDR + _SUBJ + (
            "    if files is not None and len(files) > 0:\n"
            "        form: dict[str, str] | None = None\n"
            "        if data is not None and isinstance(data, dict)"
            " and len(data) > 0:\n"
            "            form = data\n"
            "        if form is None:\n"
            "            return -1\n"
            "        return len(form)\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(enc(None, None))\n"
        )
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("reseat.narrow_alias_addr", 0) > 0
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "form = &(__data);" in hpp + cpp

    def test_bytes_member_narrow_body_routes(self):
        # The bytes leg of the same subject: the else-branch narrowing binds
        # a `std::vector<uint8_t>&` alias, spelled by render_type like any
        # other member.
        src = _HDR + (
            "def consume(data: bytes | dict[str, str] | None) -> Int32:\n"
            "    if isinstance(data, bytes):\n"
            "        return len(data)\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(consume(b'abc'))\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ("std::holds_alternative<std::vector<uint8_t>*>(data)"
                in hpp + cpp)

    def test_bytes_union_print_and_pass_route(self):
        # The member-shape-blind rows the widened class also opens: the
        # `__str__` visitor over the whole variant and the bare same-union
        # name pass.
        src = _HDR + (
            "def take(data: bytes | dict[str, str] | None) -> None:\n"
            "    print(data)\n"
            "def relay(data: bytes | dict[str, str] | None) -> None:\n"
            "    take(data)\n"
            "def main() -> None:\n"
            "    relay(b'abc')\n"
        )
        _assert_routes_byte_identical(src)


class TestBytesUnionNarrowBoundaries:
    def test_const_pointee_subject_alias_reseat_still_defers(self):
        # A readonly union param deep-consts the pointees, so the alias binds
        # `const M&` -- its address is a `const M*` the mutable slot cannot
        # take. The arm must keep rejecting.
        src = _HDR + (
            "from tpy import readonly\n"
            "def enc(data: readonly[bytes | dict[str, str] | None]) -> Int32:\n"
            "    form: dict[str, str] | None = None\n"
            "    if isinstance(data, dict):\n"
            "        form = data\n"
            "    if form is None:\n"
            "        return -1\n"
            "    return len(form)\n"
            "def main() -> None:\n"
            "    print(enc(None))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.opt_reseat_source")

    def test_value_variant_subject_alias_reseat_still_defers(self):
        # A loop element over `list[A | B]` binds the STORAGE variant, so its
        # alias is a by-value `std::get<M>` read, not the pointer-variant
        # `M&` this arm takes the address of.
        src = _HDR + (
            "class A:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 1\n"
            "class B:\n"
            "    m: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.m = 2\n"
            "def pick(items: list[A | B]) -> Int32:\n"
            "    found: A | None = None\n"
            "    for it in items:\n"
            "        if isinstance(it, A):\n"
            "            found = it\n"
            "    if found is None:\n"
            "        return -1\n"
            "    return found.n\n"
            "def main() -> None:\n"
            "    xs: list[A | B] = [A(), B()]\n"
            "    print(pick(xs))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.opt_reseat_source")
