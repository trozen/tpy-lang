"""Pins for reading another module's CONTAINER global as `mod.X`.

Such a global lives in a pointer slot, so its read is `(*<qualified>)` rather
than a bare name. The module-variable arm hands that deref out only where the
CONSUMER is pinned -- a consumer whose render is fixed and which binds the
lvalue by reference, so the deref cannot be copied or re-wrapped behind the
arm's back. Three consumers carry it here: the element template, the printer
wrap, and a plain container reference parameter.

Committed oracles, all in `tests/cases`: the element read is
`__getitem__((*::tpystd::_datetime_cal::_DAY_ABBR), wd)` and the by-reference
pass `_match_name(data, p, (*::tpystd::_datetime_cal::_DAY_FULL), ...)`, both
in `harness/stdlib_render`; the printer wrap is `ListPrinter((*xs))` in
`globals/module_scope_empty_literals`, over the read spelling
`__len__((*::tpystd::sys::argv))` pins in `builtins/sys_argv`.
"""

from __future__ import annotations

from pathlib import Path

from .testutil import (_assert_rejects_at, _assert_routes_byte_identical,
                       _lower_ctx_witnessed, _thir_ctx)

_TABLES = Path(__file__).resolve().parents[2] / "tests" / "cases" / "globals" \
    / "module_var_container_reads" / "src"

_STDLIB_RENDER = (Path(__file__).resolve().parents[2] / "tests" / "cases"
                  / "harness" / "stdlib_render" / "expected" / "src")

_SRC = (
    "import tables\n"
    "def add_name(xs: list[str], v: str) -> None:\n"
    "    xs.append(v)\n"
    "def main() -> None:\n"
    "    print(tables.NAMES[0])\n"
    "    print(tables.NAMES)\n"
    "    add_name(tables.NAMES, 'gamma')\n"
    "    print(tables.NAMES)\n"
    "main()\n"
)


def _lib():
    return [_TABLES]


class TestModuleVarContainerReads:
    def test_all_three_consumers_route(self):
        thir, _faces = _lower_ctx_witnessed(_SRC, extra_lib_dirs=_lib())
        assert {f.name for f in thir.functions} >= {"add_name", "main"}
        _ctx, reasons = _thir_ctx(_SRC, extra_lib_dirs=_lib())
        assert reasons == [], reasons

    def test_renders_the_deref_at_each_consumer(self):
        hpp, cpp = _assert_routes_byte_identical(_SRC, extra_lib_dirs=_lib(),
                                                 comments=False)
        out = hpp + cpp
        assert ("::tpy::__getitem__((*::tpyapp::tables::NAMES), 0)" in out)
        assert ("::tpy::ListPrinter((*::tpyapp::tables::NAMES))" in out)
        assert ('add_name((*::tpyapp::tables::NAMES), "gamma")' in out)

    def test_element_and_pass_shapes_match_the_stdlib_oracle(self):
        # The two shapes this arm mirrors, as committed.
        fmt = (_STDLIB_RENDER / "_datetime_fmt.cpp").read_text()
        assert ("::tpy::__getitem__((*::tpystd::_datetime_cal::_DAY_ABBR), wd)"
                in fmt)
        parse = (_STDLIB_RENDER / "_datetime_parse.cpp").read_text()
        assert ("(*::tpystd::_datetime_cal::_DAY_FULL)" in parse)

    def test_sys_argv_element_routes(self):
        # The stdlib pointer-slot global at the same element consumer -- the
        # shape every `sys.argv[i]` program hits.
        src = ("import sys\n"
               "def main() -> None:\n"
               "    print(len(sys.argv[0]) > 0)\n"
               "main()\n")
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert ("::tpy::__getitem__((*::tpystd::sys::argv), 0)" in hpp + cpp)

    def test_bare_expression_statement_keeps_rejecting(self):
        # BOUNDARY: a discarded module-variable read has no consumer at all,
        # so nothing pins the deref -- and the statement would emit a value
        # the C++ compiler flags as having no effect.
        src = ("import sys\n"
               "def main() -> None:\n"
               "    sys.argv\n"
               "main()\n")
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.expr_stmt",
                           "expr_stmt.field_access")

    def test_local_alias_of_a_module_container_keeps_rejecting(self):
        # BOUNDARY: binding the read to a local is an ALIAS decl, whose gate
        # excludes every global source (a same-module global too), not just
        # the cross-module one -- a different question from consuming the
        # read in place.
        src = ("import sys\n"
               "def main() -> None:\n"
               "    a = sys.argv\n"
               "    print(len(a) >= 1)\n"
               "main()\n")
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl", "decl.slot_type")

    def test_own_slot_call_argument_keeps_rejecting(self):
        # BOUNDARY: an `Own[list[str]]` parameter is a move sink, not a
        # by-reference bind, so the pass row must not claim it.
        src = ("import sys\n"
               "from tpy import Own\n"
               "def eat(xs: Own[list[str]]) -> int:\n"
               "    return len(xs)\n"
               "def main() -> None:\n"
               "    print(eat(sys.argv) >= 1)\n"
               "main()\n")
        _ctx, fell = _thir_ctx(src)
        assert fell, "an Own[container] slot must not take the pass row"
