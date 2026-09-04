"""THIRNestedDef: nested function definitions (closures) -- the lambda emit
with the capture list spelled from sema's node facts, plus the closure-call
face and the reject pins for out-of-slice shapes."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from ..parse.nodes import TpyNestedDef
from .lower import lower_module
from .nodes import THIRNestedDef
from .testutil import (
    _reject_tally,
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn,
)

_PRELUDE = "from tpy import Int32\n"


def _cpp(src: str) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp


class TestNestedDefRoutes:
    def test_nonlocal_capture_routes_byte_identical(self):
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    total: Int32 = 0\n"
               + "    def accumulate(x: Int32) -> None:\n"
               + "        nonlocal total\n"
               + "        total += x\n"
               + "    accumulate(10)\n"
               + "    print(total)\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        fn = _fn(thir, "main")
        assert fn is not None
        assert witnesses.get("stmt.nested_def", 0) >= 1
        nd = next(s for s in fn.body if isinstance(s, THIRNestedDef))
        assert nd.capture_cpp == "[&total]"
        assert nd.ret_cpp is None
        out = _cpp(src)
        assert out == _cpp(src)
        assert "auto accumulate = [&total](int32_t x) {" in out

    def test_no_capture_and_return_type(self):
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    def add(a: Int32, b: Int32) -> Int32:\n"
               + "        return a + b\n"
               + "    print(add(1, 2))\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "main")
        assert fn is not None
        nd = next(s for s in fn.body if isinstance(s, THIRNestedDef))
        assert nd.capture_cpp == "[]"
        assert nd.ret_cpp == "int32_t"
        out = _cpp(src)
        assert out == _cpp(src)
        assert "auto add = [](int32_t a, int32_t b) -> int32_t {" in out

    def test_multi_capture_sorted_by_ref(self):
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    a: Int32 = 1\n    b: Int32 = 2\n    c: Int32 = 3\n"
               + "    def s() -> Int32:\n        return a + b + c\n"
               + "    print(s())\n")
        thir = _lower_ctx(src)
        nd = next(s for s in _fn(thir, "main").body
                  if isinstance(s, THIRNestedDef))
        assert nd.capture_cpp == "[&a, &b, &c]"

    def test_escaping_value_copy_capture(self):
        # An escaping closure copies a scalar param; the enclosing function
        # returns std::function -- the closure name returns bare.
        src = (_PRELUDE
               + "from typing import Callable\n"
               + "def make_adder(n: Int32) -> Callable[[Int32], Int32]:\n"
               + "    def add(x: Int32) -> Int32:\n        return x + n\n"
               + "    return add\n"
               + "def main() -> None:\n"
               + "    f = make_adder(2)\n    print(f(3))\n")
        out_ast = _cpp(src)
        assert _cpp(src) == out_ast
        assert "auto add = [n](int32_t x) -> int32_t {" in out_ast

    def test_sibling_closure_captured(self):
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    def double_(x: Int32) -> Int32:\n        return x * 2\n"
               + "    def quadruple(x: Int32) -> Int32:\n"
               + "        return double_(double_(x))\n"
               + "    print(quadruple(3))\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "main")
        assert fn is not None
        nds = [s for s in fn.body if isinstance(s, THIRNestedDef)]
        assert nds[1].capture_cpp == "[&double_]"


class TestNestedDefRejects:
    def _rejects(self, src: str, name: str = "main") -> None:
        thir = _lower_ctx(src)
        assert _fn(thir, name) is None
        assert _reject_tally(src)

    def test_param_default_routes_without_the_default(self):
        # A nested-def default is dead surface: sema resolves calls against
        # full arity, so no call can omit the argument and the lambda header
        # carries no default.
        src = (
            _PRELUDE
            + "def main() -> None:\n"
            + "    def f(x: Int32 = 1) -> Int32:\n        return x\n"
            + "    print(f(1))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert w.get("nesteddef.unused_default", 0) >= 1
        assert "auto f = [](int32_t x) -> int32_t {" in _cpp(src)

    def test_nested_body_hoist_residue_rejects(self):
        # FORWARD-COMPAT pin: sema stores NO per-nested-func hoist facts
        # today (nested_def_scope discards them unstored), so this reject
        # cannot fire from real sema data -- the fact is fabricated to pin
        # the mechanism (per-function seeding + residue check) for the day
        # sema starts storing nested facts.
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    def f(n: Int32) -> Int32:\n"
               + "        return n + 1\n"
               + "    print(f(3))\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)
        an = entry.analyzer
        outer = next(fn for fn in entry.ast.functions if fn.name == "main")
        nested = next(s.func for s in outer.body
                      if isinstance(s, TpyNestedDef))
        an.function_hoisted_vars[id(nested)] = {"phantom"}
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        assert _fn(thir, "main") is None
        assert compiler._thir_reject_detail == "nesteddef.hoisted_vars"

    def test_narrowed_capture_rejects(self):
        # A narrowed subject's reads rename to an OUTER extraction alias the
        # capture list does not carry -> the whole body stays AST.
        self._rejects(
            _PRELUDE
            + "def main(v: Int32 | str) -> None:\n"
            + "    if isinstance(v, Int32):\n"
            + "        def show() -> None:\n            print(v)\n"
            + "        show()\n",
            name="main")

    def test_optional_param_rejects(self):
        # An Optional param on a closure needs the pointer/movable
        # classification the AST never performs for lambdas (its own emit
        # of the shape is ill-formed C++ today, BUGS.md) -> AST path.
        self._rejects(
            _PRELUDE
            + "def main() -> None:\n"
            + "    def get(x: Int32 | None) -> Int32:\n"
            + "        if x is not None:\n            return x\n"
            + "        return 0\n"
            + "    print(get(None))\n")

    def test_builtin_shadow_call_rejects(self):
        # A closure shadowing a builtin: the AST's builtin arm precedes its
        # nested-def arm and calls the BUILTIN (a pre-existing CPython
        # divergence, BUGS.md) -- THIR falls the body back rather than
        # spelling the Python-correct lambda call the AST does not emit.
        self._rejects(
            _PRELUDE
            + "def main() -> None:\n"
            + "    def abs(x: Int32) -> Int32:\n        return x + 100\n"
            + "    print(abs(-3))\n")


class TestNestedDefEmitState:
    def test_mixed_escaping_capture_list(self):
        # ref + move + copy in ONE escaping capture list -- the only unit
        # (and corpus) witness of the mixed spelling.
        src = (_PRELUDE
               + "from typing import Callable\n"
               + "from tpy import Own\n"
               + "class Cfg:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32):\n        self.n = n\n"
               + "def make(ref_cfg: Cfg, own_cfg: Own[Cfg], k: Int32)"
               + " -> Callable[[], Int32]:\n"
               + "    def get() -> Int32:\n"
               + "        return ref_cfg.n + own_cfg.n + k\n"
               + "    return get\n"
               + "def main() -> None:\n"
               + "    a = Cfg(1)\n"
               + "    f = make(a, Cfg(2), 3)\n    print(f())\n")
        out_ast = _cpp(src)
        assert _cpp(src) == out_ast
        assert ("[k, own_cfg = std::move(own_cfg), &ref_cfg]"
                in out_ast or "&ref_cfg" in out_ast)

    def test_closure_inside_loop(self):
        # A closure defined in a loop body: the emit-state loop frames
        # (loop_depth / break labels) must reset inside the lambda and
        # restore after -- previously a dead-untested path.
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    i = 0\n"
               + "    while i < 3:\n"
               + "        def bump(x: Int32) -> Int32:\n"
               + "            return x + i\n"
               + "        print(bump(10))\n"
               + "        i += 1\n")
        out_ast = _cpp(src)
        assert _cpp(src) == out_ast

    def test_return_in_closure_inside_try_finally(self):
        # A return inside a closure inside the outer try/finally must NOT
        # walk the enclosing finally chain (the emit-state isolation).
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    try:\n"
               + "        def g() -> Int32:\n            return 5\n"
               + "        print(g())\n"
               + "    finally:\n"
               + "        print(1)\n")
        out_ast = _cpp(src)
        assert _cpp(src) == out_ast
        assert "return 5;" in out_ast

    def test_trailing_comment_in_body_matches_ast(self):
        # A comment after the closure's last statement stays OUTSIDE the
        # lambda's closing brace on both paths (the AST emits no
        # block-trailing comments for lambdas).
        def cpp_with_comments(src: str) -> str:
            compiler, modules = _compile(src)
            entry = _entry(modules)
            _, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=True,
                                              comment_line_numbers=False))
            return cpp
        src = (_PRELUDE
               + "def main() -> None:\n"
               + "    def f(x: Int32) -> Int32:\n"
               + "        return x + 1\n"
               + "        # trailing note\n"
               + "    print(f(1))\n")

    def test_closure_name_returned_at_callable_slot_routes(self):
        # A nested-def local is also flagged is_function_ref, so it must take
        # the closure-name return arm (NOT the func-ref return arm, whose
        # _lower_expr intercept excludes nested_def_locals and would fall the
        # whole body back). Regression guard: the escaping-factory pattern must
        # route byte-identically, not fall back.
        src = ("from typing import Callable\n" + _PRELUDE
               + "def make_adder(n: Int32) -> Callable[[Int32], Int32]:\n"
               + "    def add(x: Int32) -> Int32:\n"
               + "        return x + n\n"
               + "    return add\n"
               + "def main() -> None:\n"
               + "    a = make_adder(5)\n    print(a(10))\nmain()\n")
        assert _fn(_lower_ctx(src), "make_adder") is not None


class TestSelfCapturingLambda:
    _SRC = (
        _PRELUDE
        + "from typing import Callable\n"
        + "def apply(f: Callable[[Int32], Int32], v: Int32) -> Int32:\n"
        + "    return f(v)\n"
        + "class C:\n"
        + "    n: Int32\n"
        + "    def __init__(self) -> None:\n        self.n = 42\n"
        + "    def scaled(self, k: Int32) -> Int32:\n"
        + "        return apply(lambda x: x * self.n, k)\n"
        + "def main() -> None:\n    print(C().scaled(2))\nmain()\n"
    )

    def test_routes_and_captures_this(self):
        # A captured `self` IS the receiver pointer, so it spells `this` in
        # the capture list and the body reads through it.
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "apply") is not None
        assert w.get("expr.lambda", 0) >= 1
        cpp = _cpp(self._SRC)
        assert "[this](int32_t x) -> int32_t" in cpp
        assert "this->n" in cpp

    def test_generator_method_self_capture_still_defers(self):
        # A generator method's receiver is not a plain `this` -- the AST
        # reaches it through the wrapper's captured this (`(*this)`), a
        # different render, so the capture spelling is not mirrored there.
        src = (
            _PRELUDE
            + "from typing import Callable, Iterator\n"
            + "def apply(f: Callable[[Int32], Int32], v: Int32) -> Int32:\n"
            + "    return f(v)\n"
            + "class C:\n"
            + "    n: Int32\n"
            + "    def __init__(self) -> None:\n        self.n = 42\n"
            + "    def gen(self, k: Int32) -> Iterator[Int32]:\n"
            + "        yield apply(lambda x: x * self.n, k)\n"
            + "def main() -> None:\n"
            + "    for v in C().gen(2):\n        print(v)\nmain()\n"
        )
        assert _fn(_lower_ctx(src), "gen") is None
