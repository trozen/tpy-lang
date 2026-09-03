"""Pins for the bare INT-LITERAL expression statement.

The REPL appends `0` to every compile so that `main()` is always emitted
(typed module-level declarations become globals, not `main()` body), so this
statement shape is on the path of every REPL line. Discarding a literal has
nothing to sequence, so the render is the literal cast to void -- an
expression statement's ordinary shape, which is why the arm sits beside the
call and subscript discards rather than skipping the statement the way a
docstring is skipped. The cast is load-bearing: a bare `0;` is what GCC's
`-Wunused-value` rejects under `-Werror`, which the suite builds with.
"""

from __future__ import annotations

from .testutil import (_assert_rejects_at, _assert_routes_byte_identical,
                       _thir_ctx, _top_level)

_SRC = (
    "def main() -> None:\n"
    "    total = 0\n"
    "    7\n"
    "    total += 1\n"
    "    print(total)\n"
    "main()\n"
    "0\n"
)


class TestBareIntLiteralStatement:
    def test_module_level_bare_int_literal_routes(self):
        thir, _faces, reasons = _top_level(_SRC)
        assert reasons == [], reasons
        assert thir is not None

    def test_renders_the_literal_cast_to_void(self):
        hpp, cpp = _assert_routes_byte_identical(_SRC, comments=False)
        out = hpp + cpp
        assert "\n    (void)(7);\n" in out
        assert "\n    (void)(0);\n" in out

    def test_bare_name_statement_routes_on_its_own_row(self):
        # The NEIGHBOURING row, not a boundary: a discarded NAME reads
        # through the name arm (a pointer-local or frame slot renders its
        # own indirection), so it carries its own face rather than riding
        # the literal one.
        src = (
            "def main() -> None:\n"
            "    x = 1\n"
            "    x\n"
            "    print(x)\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        assert fell == [], fell
        cpp = _assert_routes_byte_identical(src, comments=False)[1]
        assert "\n    (void)(x);\n" in cpp

    def test_bare_float_literal_statement_keeps_rejecting(self):
        # BOUNDARY: the neighbouring literal kind. It is not needed by any
        # caller and its render carries a target-typed suffix question the
        # int row does not answer.
        src = (
            "def main() -> None:\n"
            "    1.5\n"
            "    print(1)\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.expr_stmt",
                           "expr_stmt.float_literal")
