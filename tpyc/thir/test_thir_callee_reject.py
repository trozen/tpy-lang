"""Reject units for callee-side call shapes that stay on the AST path by
design -- the spelling paths THIR does not thread. Each pins the first-reject
DETAIL (`_thir_reject_detail`, the note_detail drilldown) so a future change
that silently starts routing one of these -- without the matching byte-mirror --
trips here instead of surfacing as a corpus byte-diff regression.

The routine callee kinds (plain / imported / native / positional-template,
scalar/slice/owned-str type-constructors, cast, enum-from-value, arity-with-
defaults) already route; these are the residue left rejected on purpose."""

from __future__ import annotations

from ..compilation_context import activate_compiler
from .fallback import begin_attempt
from .lower import iter_module_callables, lower_function
from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _compile, _entry)


def _reject_detail(src: str, fn_name: str):
    """Lower `fn_name` from `src` and return (routed, reject_reason,
    reject_detail). Mirrors the fallback-tally test driver: a per-body
    begin_attempt clears the slots, and a None result means the body fell
    back to AST with its first reject recorded on the compiler."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            if func.name != fn_name:
                continue
            begin_attempt()
            result = lower_function(func, entry.analyzer, self_type=self_type)
            return (result is not None, compiler._thir_reject_reason,
                    compiler._thir_reject_detail)
    raise AssertionError(f"{fn_name} not found among callables")


class TestIsinstanceValueRejects:
    # isinstance-as-value carries the full narrowing/holds_alternative/dynamic-
    # cast/typeid spelling surface (an `if:cond.call` construct), so the
    # bare-name callee classifier rejects it as a special form.
    SRC = (
        "from tpy import Int32\n"
        "def f(x: Int32 | str) -> bool:\n"
        "    b = isinstance(x, Int32)\n"
        "    return b\n"
    )

    def test_union_subject_value_position_routes(self):
        # Former fence: the union-subject value-position isinstance now
        # renders the holds_alternative chain (call.isinstance_union_value,
        # dualgen-verified byte-identical), so the special-form reject no
        # longer fires for this shape.
        routed, _reason, _detail = _reject_detail(self.SRC, "f")
        assert routed is True


class TestStrLitOverloadPinRoutes:
    # A str literal into a str/StrView slot of a MULTI-overload callee takes
    # gen_call_arg's `param_view_t("...")` pin, mirrored per-arg: the literal
    # is `const char[N]` in C++, whose conversions outrank string_view's.
    SRC = (
        "from typing import overload\n"
        "from tpy import Int32\n"
        "@overload\n"
        "def h(a: Int32, s: str) -> str:\n    return s\n"
        "@overload\n"
        "def h(a: Int32, s: Int32) -> Int32:\n    return a + s\n"
        "def f() -> str:\n    return h(1, \"hi\")\n"
    )

    def test_routes_with_pin(self):
        routed, _reason, _detail = _reject_detail(self.SRC, "f")
        assert routed is True


class TestLiteralOverloadMangleRoutes:
    # A literal-specialized overload mangles its callee to `f__lit_N`
    # (literal_mangled_name) when there are multiple overloads; the mangled
    # spelling rides callee_cpp through the plain/imported kinds. These
    # BODIED stubs pin only the CALL spelling: their definition emission is
    # a pre-existing AST defect (duplicate unmangled definitions, see
    # BUGS.md's bodied-literal-stub entry) that both paths mirror
    # byte-identically.
    SRC = (
        "from typing import Literal, overload\n"
        "from tpy import Int32\n"
        "@overload\n"
        "def k(a: Int32, m: Literal[\"fast\"]) -> Int32:\n    return a\n"
        "@overload\n"
        "def k(a: Int32, m: Literal[\"slow\"]) -> Int32:\n    return a + 1\n"
        "def f() -> Int32:\n    return k(1, \"fast\")\n"
    )

    def test_routes_with_mangled_spelling(self):
        routed, _reason, _detail = _reject_detail(self.SRC, "f")
        assert routed is True
        _assert_byte_identical(self.SRC)


class TestExprCalleeRoutes:
    # An expression callee -- calling the result of a call (`f()()`) or a
    # subscript (`fns[i](x)`) -- has a non-Name func, so it gets its own
    # arm: the callee renders parenthesized ahead of the args. That arm
    # returns before any `e.func_name` read, which is what keeps the old
    # `func_name on non-Name callee` crash (the ord()-fold arm) unreachable.
    #
    # Three of the arm's four guards are unreachable defense-in-depth: sema
    # rejects a non-callable callee outright ("Expression is not callable"),
    # checks arity itself, and never routes an `Fn` template through here.
    # The FOURTH -- `**kwargs` -- IS reachable (`mk(10)(5, **d)` matches on
    # positional arity, so sema accepts it) and is pinned below.
    _MK = ("from typing import Callable\n"
           "from tpy import Int32\n"
           "def mk(n: Int32) -> Callable[[Int32], Int32]:\n"
           "    def add(x: Int32) -> Int32:\n        return x + n\n"
           "    return add\n")

    def test_call_result_callee_routes(self):
        src = self._MK + "def f() -> Int32:\n    return mk(10)(5)\n"
        routed, _reason, _ = _reject_detail(src, "f")
        assert routed is True

    def test_subscript_callee_routes(self):
        # `fns` is a param (not a local list-literal decl) so this isolates
        # the subscript callee, not the container init.
        src = (self._MK
               + "def f(fns: list[Callable[[Int32], Int32]]) -> Int32:\n"
               + "    return fns[0](100)\n")
        routed, _reason, _ = _reject_detail(src, "f")
        assert routed is True

    def test_renders_the_parenthesized_callee(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self._PROG)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "(mk(10))(5)" in cpp
        assert "(::tpy::__getitem__(fns, 0))(100)" in cpp

    _PROG = (_MK
             + "def f(fns: list[Callable[[Int32], Int32]]) -> Int32:\n"
             + "    return mk(10)(5) + fns[0](100)\n"
             + "def main() -> None:\n"
             + "    print(f([mk(1)]))\n"
             + "main()\n")

    def test_byte_identical(self):
        # The corpus case (calls/expr_callee) still falls back on an
        # unrelated container-literal blocker, so it does NOT byte-diff this
        # render -- this pin is the only thing that does.
        _assert_byte_identical(self._PROG)

    def test_double_star_unpack_still_rejects(self):
        # The one REACHABLE guard: positional arity matches, so sema accepts
        # `mk(10)(5, **d)` and it reaches lowering. The arm has no slot to
        # bind the unpacked keywords against.
        src = (self._MK
               + "def f(d: dict[str, Int32]) -> Int32:\n"
               + "    return mk(10)(5, **d)\n")
        routed, reason, _ = _reject_detail(src, "f")
        assert routed is False
        _assert_rejects_at(reason, "expr.call", "call.expr_callee_shape")
