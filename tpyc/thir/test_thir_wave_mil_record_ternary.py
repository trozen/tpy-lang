"""Pins for the PRVALUE record ternary at a ctor member-init slot.

`self._ctx = copy(ctx) if ctx is not None else make_ctx()` renders
`_ctx((((ctx != nullptr)) ? (Ctx((*ctx))) : (make_ctx())))`: both arms are
prvalues, so the C++ `?:` is a prvalue the direct-init consumes. That is a
VALUE-form record ternary, unlike the lvalue slice whose arms are names or
`T&`-returning calls. The value category is scoped to this one sink -- every
other consumer of a record ternary (a decl's REF_ALIAS bind, an arg slot)
has a per-shape render and keeps rejecting.

The `copy(<pointer-local>)` arm rides here too: the copy-construct spells
`T((*p))` over the deref, which the plain `copy()` rows exclude.
"""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctor,
)


def _gen_faces(src: str):
    """Face witnesses from a full THIR generate -- `_lower_ctx_witnessed`
    only runs `lower_module`, which never reaches constructor lowering."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=True,
                               comment_line_numbers=False))
    return dict(compiler._thir_face_witnesses)


_HDR = "from tpy import Int32, Own, copy\n"

_TYPES = _HDR + (
    "class Ctx:\n"
    "    mode: Int32\n"
    "    def __init__(self, mode: Int32 = 0) -> None:\n"
    "        self.mode = mode\n"
    "def default_ctx() -> Own[Ctx]:\n"
    "    return Ctx(7)\n"
)


class TestMilRecordPrvalueTernary:
    def test_copy_or_factory_ternary_routes(self):
        # The stdlib shape: a copied caller-supplied record, else a factory
        # call -- two prvalue arms at the member-init slot.
        src = _TYPES + (
            "class Conn:\n"
            "    _ctx: Ctx\n"
            "    def __init__(self, ctx: Ctx | None = None) -> None:\n"
            "        self._ctx = copy(ctx) if ctx is not None"
            " else default_ctx()\n"
            "def main() -> None:\n"
            "    c = Conn()\n"
            "    print(c._ctx.mode)\n"
            "    given = Ctx(3)\n"
            "    d = Conn(given)\n"
            "    print(d._ctx.mode)\n"
        )
        assert _lower_ctor(src, "Conn") is not None
        assert _gen_faces(src).get("ifexpr.record_prvalue", 0) > 0
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ("_ctx((((ctx != nullptr)) ? (Ctx((*ctx))) : (default_ctx())))"
                in hpp + cpp)

    def test_ctor_rvalue_arms_route(self):
        # Both arms plain constructor calls -- the other prvalue shape.
        src = _TYPES + (
            "class Conn:\n"
            "    _ctx: Ctx\n"
            "    def __init__(self, strict: bool) -> None:\n"
            "        self._ctx = Ctx(1) if strict else Ctx(2)\n"
            "def main() -> None:\n"
            "    print(Conn(True)._ctx.mode)\n"
        )
        assert _gen_faces(src).get("ifexpr.record_prvalue", 0) > 0
        _assert_routes_byte_identical(src)


class TestMilRecordTernaryBoundaries:
    def test_lvalue_name_arms_at_mil_still_defer(self):
        # NAME arms make the C++ `?:` an LVALUE, a different render category;
        # the prvalue arm lowerer must reject them rather than claim the slot.
        src = _TYPES + (
            "class Conn:\n"
            "    _ctx: Ctx\n"
            "    def __init__(self, a: Ctx, b: Ctx, strict: bool) -> None:\n"
            "        self._ctx = a if strict else b\n"
            "def main() -> None:\n"
            "    x = Ctx(1)\n"
            "    y = Ctx(2)\n"
            "    print(Conn(x, y, True)._ctx.mode)\n"
        )
        _assert_rejects_at(_reject_tally(src), "ctor:expr.ifexpr")

    def test_prvalue_ternary_at_decl_still_defers(self):
        # A decl binds a REF_ALIAS off a record ternary; the prvalue form is
        # not that render, and the flag never reaches this position.
        src = _TYPES + (
            "def use(ctx: Ctx | None) -> Int32:\n"
            "    picked = copy(ctx) if ctx is not None else default_ctx()\n"
            "    return picked.mode\n"
            "def main() -> None:\n"
            "    print(use(None))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

    def test_prvalue_ternary_at_arg_slot_still_defers(self):
        # An arg slot materializes its own temp; the prvalue ternary has no
        # witness there.
        src = _TYPES + (
            "def take(c: Own[Ctx]) -> Int32:\n"
            "    return c.mode\n"
            "def use(ctx: Ctx | None) -> Int32:\n"
            "    return take(copy(ctx) if ctx is not None else default_ctx())\n"
            "def main() -> None:\n"
            "    print(use(None))\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.ifexpr")

    def test_copy_of_pointer_local_at_decl_still_defers(self):
        # The `copy(<pointer-local>)` copy-construct is admitted as a ternary
        # ARM only -- the plain decl row still excludes a pointer source.
        src = _TYPES + (
            "def use(ctx: Ctx | None) -> Int32:\n"
            "    if ctx is None:\n"
            "        return -1\n"
            "    picked = copy(ctx)\n"
            "    return picked.mode\n"
            "def main() -> None:\n"
            "    print(use(None))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.builtin_special")
