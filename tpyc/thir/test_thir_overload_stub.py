"""Pins for per-@overload-stub lowering: the (impl, stub)-keyed seeding,
the isinstance/match dead-branch folds, per-stub return coercion -- and
the boundaries: generic_stub / arity / db_compare / narrow_param keep
rejecting, no partial folds, sema-placed checks survive in kept
branches."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _compile,
    _entry,
    _fn,
    _lower_ctx,
)
from .lower import lower_function
from .lower.functions import iter_module_callables, module_native_globals

_PRELUDE = (
    "from typing import overload\n"
    "class Dog:\n"
    "    name: str\n"
    "    def __init__(self, name: str) -> None:\n"
    "        self.name = name\n"
    "class Cat:\n"
    "    lives: int\n"
    "    def __init__(self, lives: int) -> None:\n"
    "        self.lives = lives\n"
)


def _per_stub_results(source: str, name: str):
    """Lower each stub of overload impl `name` directly; return
    [(result, reject_reason)] in stub order."""
    from ..compilation_context import activate_compiler
    from .fallback import begin_attempt

    compiler, modules = _compile(source)
    entry = _entry(modules)
    analyzer = entry.analyzer
    ng = module_native_globals(entry.ast)
    out = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, analyzer):
            stubs = analyzer.overload_groups.get(id(func))
            if func.name != name or not stubs:
                continue
            for stub in stubs:
                begin_attempt()
                fn = lower_function(func, analyzer, self_type=self_type,
                                    native_globals=ng, stub=stub)
                out.append((fn, compiler._thir_reject_reason))
    return out


class TestPerStubRouting:
    def test_isinstance_fold_routes_both_stubs(self):
        src = _PRELUDE + (
            "@overload\n"
            "def describe(a: Dog) -> str: ...\n"
            "@overload\n"
            "def describe(a: Cat) -> str: ...\n"
            "def describe(a: Dog | Cat) -> str:\n"
            "    if isinstance(a, Dog):\n"
            "        return 'dog ' + a.name\n"
            "    else:\n"
            "        return 'cat ' + str(a.lives)\n"
        )
        thir = _lower_ctx(src)
        assert len([f for f in thir.functions if f.name == "describe"]) == 2
        _assert_byte_identical(src)

    def test_match_fold_routes(self):
        src = _PRELUDE + (
            "@overload\n"
            "def label(a: Dog) -> str: ...\n"
            "@overload\n"
            "def label(a: Cat) -> str: ...\n"
            "def label(a: Dog | Cat) -> str:\n"
            "    match a:\n"
            "        case Dog(name=n):\n"
            "            return n\n"
            "        case Cat(lives=v):\n"
            "            return str(v)\n"
        )
        thir = _lower_ctx(src)
        assert len([f for f in thir.functions if f.name == "label"]) == 2
        _assert_byte_identical(src)

    def test_fold_return_in_loop_keeps_trailing_return(self):
        # A folded-True `return` INSIDE a while loop must not truncate the
        # reachable `return 0` after the loop (the loop may not run). Both
        # paths must emit the trailing return; byte-diff pins the scoping and
        # that THIR does not leak lc.overload_terminated past the loop body.
        src = _PRELUDE + (
            "@overload\n"
            "def in_loop(a: Dog, k: int) -> int: ...\n"
            "@overload\n"
            "def in_loop(a: Cat, k: int) -> int: ...\n"
            "def in_loop(a: Dog | Cat, k: int) -> int:\n"
            "    while k > 0:\n"
            "        if isinstance(a, Dog):\n"
            "            return k\n"
            "        else:\n"
            "            return a.lives - k\n"
            "    return 0\n"
        )
        thir = _lower_ctx(src)
        assert len([f for f in thir.functions if f.name == "in_loop"]) == 2
        _assert_byte_identical(src)

    def test_second_match_after_fold_keeps_numbering(self):
        # The AST burns __match_subject_N for the folded match; a real match
        # in a sibling body must number PAST it -- byte-diff pins the burn.
        src = _PRELUDE + (
            "@overload\n"
            "def pick(a: Dog) -> str: ...\n"
            "@overload\n"
            "def pick(a: Cat) -> str: ...\n"
            "def pick(a: Dog | Cat) -> str:\n"
            "    match a:\n"
            "        case Dog(name=n):\n"
            "            return n\n"
            "        case Cat(lives=v):\n"
            "            return str(v)\n"
            "    return ''\n"
        )
        _assert_byte_identical(src)

    def test_ret_coercion_strip_and_optional_keep(self):
        # Stub -> int returning an Int32 field: sema's union-return coercion
        # is stripped (raw expr, C++ implicit conv). Stub -> Dog | None
        # returning a coerce-to-inner keeps its coercion (the KEEP case).
        src = _PRELUDE + (
            "from tpy import Int32\n"
            "class A:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "@overload\n"
            "def get(o: A) -> int: ...\n"
            "@overload\n"
            "def get(o: Dog) -> float: ...\n"
            "def get(o: A | Dog) -> float | int:\n"
            "    if isinstance(o, A):\n"
            "        return o.x\n"
            "    return 1.5\n"
        )
        thir = _lower_ctx(src)
        assert len([f for f in thir.functions if f.name == "get"]) == 2
        _assert_byte_identical(src)

    def test_sema_checks_survive_in_kept_branch(self):
        # The surviving branch's list subscript keeps its sema-placed bounds
        # check -- byte-diff would catch a dropped check call.
        src = _PRELUDE + (
            "@overload\n"
            "def head(a: Dog, xs: list[int]) -> str: ...\n"
            "@overload\n"
            "def head(a: Cat, xs: list[int]) -> str: ...\n"
            "def head(a: Dog | Cat, xs: list[int]) -> str:\n"
            "    if isinstance(a, Dog):\n"
            "        return a.name + str(xs[0])\n"
            "    else:\n"
            "        return str(a.lives) + str(xs[1])\n"
        )
        thir = _lower_ctx(src)
        assert len([f for f in thir.functions if f.name == "head"]) == 2
        _assert_byte_identical(src)


class TestPerStubBoundaries:
    def test_generic_stub_keeps_rejecting(self):
        # A protocol-param stub is a template specialization (the
        # overload_template_stub_cross_module shape).
        src = (
            "from typing import Iterable, overload\n"
            "from tpy import Int32, Own\n"
            "@overload\n"
            "def total(xs: Iterable[Own[Int32]]) -> Int32: ...\n"
            "@overload\n"
            "def total(xs: Int32) -> Int32: ...\n"
            "def total(xs: Iterable[Own[Int32]] | Int32) -> Int32:\n"
            "    if isinstance(xs, Int32):\n"
            "        return xs\n"
            "    s = 0\n"
            "    for x in xs:\n"
            "        s += x\n"
            "    return s\n"
        )
        results = _per_stub_results(src, "total")
        assert results and all(fn is None for fn, _ in results)
        assert all(r == "sig.overload_set.generic_stub" for _, r in results)

    def test_arity_keeps_rejecting(self):
        src = _PRELUDE + (
            "@overload\n"
            "def greet(a: Dog) -> str: ...\n"
            "@overload\n"
            "def greet(a: Cat, loud: bool) -> str: ...\n"
            "def greet(a: Dog | Cat, loud: bool = False) -> str:\n"
            "    if isinstance(a, Dog):\n"
            "        return a.name\n"
            "    return str(a.lives)\n"
        )
        results = _per_stub_results(src, "greet")
        assert results and all(fn is None for fn, _ in results)
        assert all(r == "sig.overload_set.arity" for _, r in results)

    def test_literal_stubs_keep_db_compare_reject(self):
        # Literal-only groups emit via the AST's mangled-name path (the
        # per-stub key is never set there); seeding them would count bodies
        # migrated with AST emission.
        src = (
            "from typing import overload, Literal\n"
            "@overload\n"
            "def mode(k: Literal[0]) -> str: ...\n"
            "@overload\n"
            "def mode(k: Literal[1]) -> str: ...\n"
            "def mode(k: int) -> str:\n"
            "    if k == 0:\n"
            "        return 'zero'\n"
            "    return 'one'\n"
        )
        results = _per_stub_results(src, "mode")
        assert results and all(fn is None for fn, _ in results)
        assert all(r == "sig.overload_set.db_compare" for _, r in results)

    def test_narrow_param_keeps_rejecting(self):
        # A union impl param with NO isinstance/match in the body: the
        # narrowing extraction skips differently per stub -- unmirrored.
        src = _PRELUDE + (
            "@overload\n"
            "def tag(a: Dog) -> int: ...\n"
            "@overload\n"
            "def tag(a: None) -> int: ...\n"
            "def tag(a: Dog | None) -> int:\n"
            "    if a is None:\n"
            "        return 0\n"
            "    return 1\n"
        )
        results = _per_stub_results(src, "tag")
        assert results and all(fn is None for fn, _ in results)
        assert all(r == "sig.overload_set.narrow_param" for _, r in results)
        _assert_byte_identical(src)

    def test_partial_fold_rejects(self):
        # One chain condition stays dynamic (a plain value test) alongside
        # the foldable isinstance: the AST emits a trimmed live chain, an
        # unmirrored render -- partial folds must keep falling back.
        src = _PRELUDE + (
            "@overload\n"
            "def judge(a: Dog, n: int) -> str: ...\n"
            "@overload\n"
            "def judge(a: Cat, n: int) -> str: ...\n"
            "def judge(a: Dog | Cat, n: int) -> str:\n"
            "    if n > 3:\n"
            "        return 'big'\n"
            "    elif isinstance(a, Dog):\n"
            "        return a.name\n"
            "    else:\n"
            "        return str(a.lives)\n"
        )
        results = _per_stub_results(src, "judge")
        assert results and all(fn is None for fn, _ in results)
        _assert_byte_identical(src)

    def test_match_guard_keeps_rejecting(self):
        # The AST fold silently drops arm guards; reject instead of
        # mirroring that.
        src = _PRELUDE + (
            "@overload\n"
            "def gd(a: Dog, k: int) -> str: ...\n"
            "@overload\n"
            "def gd(a: Cat, k: int) -> str: ...\n"
            "def gd(a: Dog | Cat, k: int) -> str:\n"
            "    match a:\n"
            "        case Dog(name=n) if k > 0:\n"
            "            return n\n"
            "        case _:\n"
            "            return 'other'\n"
        )
        results = _per_stub_results(src, "gd")
        assert results and all(fn is None for fn, _ in results)

    def test_plain_functions_unaffected_by_sibling_overloads(self):
        # A normal function next to a specialized set: the interception key
        # is consumed per body, so the sibling routes through id(func) as
        # ever -- byte-diff over the whole module pins the key hygiene.
        src = _PRELUDE + (
            "@overload\n"
            "def describe(a: Dog) -> str: ...\n"
            "@overload\n"
            "def describe(a: Cat) -> str: ...\n"
            "def describe(a: Dog | Cat) -> str:\n"
            "    if isinstance(a, Dog):\n"
            "        return a.name\n"
            "    else:\n"
            "        return str(a.lives)\n"
            "def sibling(k: int) -> int:\n"
            "    return k + 1\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "sibling") is not None
        _assert_byte_identical(src)


class TestWriteGuard:
    def test_written_names_covers_compound_targets(self):
        # The by-ref match-capture guard keys on _written_names: compound
        # statements' OWN binding targets (for-loop var, with-as name) and
        # walrus targets must count as name-level writes -- a for-loop
        # rebind of an auto&-bound capture would write through the alias.
        from tpyc.parse.nodes import (
            TpyForEach, TpyIntLiteral, TpyName, TpyNamedExpr, TpyIf,
        )
        from .lower.statements import _written_names, _body_writes_name

        loop = TpyForEach(var="v", iterable=TpyName(name="xs"), body=[])
        assert "v" in _written_names(loop)
        walrus_cond = TpyIf(
            condition=TpyNamedExpr(target="w",
                                   value=TpyIntLiteral(value=1)),
            then_body=[], else_body=[])
        assert "w" in _written_names(walrus_cond)
        assert _body_writes_name([loop], "v")
        assert not _body_writes_name([loop], "xs")
        from tpyc.parse.nodes import TpyTry, TpyExceptHandler
        try_stmt = TpyTry(
            try_body=[], else_body=[], finally_body=[],
            handlers=[TpyExceptHandler(exception_type="ValueError",
                                       binding="e", body=[])])
        assert "e" in _written_names(try_stmt)
        from tpyc.parse.nodes import (
            TpyWith, TpyWithItem, TpyTupleUnpack,
        )
        with_stmt = TpyWith(
            items=[TpyWithItem(context_expr=TpyName(name="cm"),
                               target="p")],
            body=[])
        assert "p" in _written_names(with_stmt)
        unpack = TpyTupleUnpack(targets=["a", None, "b"],
                                value=TpyName(name="src"))
        assert _written_names(unpack) == {"a", "b"}  # None target discarded
        # A rebind nested inside a compound body counts (sub_bodies recursion).
        nested = TpyIf(condition=TpyIntLiteral(value=1),
                       then_body=[loop], else_body=[])
        assert _body_writes_name([nested], "v")

    def test_match_fold_literal_subpattern_keeps_rejecting(self):
        # The AST fold silently DROPS a literal field condition
        # (`case Cat(lives=9):` emits the body unconditionally); the THIR
        # fold must reject rather than mirror that.
        src = _PRELUDE + (
            "@overload\n"
            "def sniff(a: Dog) -> str: ...\n"
            "@overload\n"
            "def sniff(a: Cat) -> str: ...\n"
            "def sniff(a: Dog | Cat) -> str:\n"
            "    match a:\n"
            "        case Cat(lives=9):\n"
            "            return 'nine'\n"
            "        case _:\n"
            "            return 'other'\n"
        )
        # The Dog stub legitimately folds to the wildcard arm; only the
        # Cat stub reaches the literal sub-pattern -- and it must reject
        # (all-or-nothing seeding then keeps the whole impl on AST).
        results = _per_stub_results(src, "sniff")
        assert any(fn is None for fn, _ in results)
        assert any(r == "stmt.match:match.overload_fold_subpattern"
                   for _, r in results)


class TestPerStubReturnMismatch:
    def test_return_mismatch_keeps_rejecting(self):
        # A branch returning the WRONG type for its stub is a codegen-time
        # CodeGenError on the AST path; the per-stub lowering must reject
        # (never swallow the body), keeping the diagnostic on the emission
        # path.
        src = _PRELUDE + (
            "@overload\n"
            "def tag(a: Dog) -> int: ...\n"
            "@overload\n"
            "def tag(a: Cat) -> str: ...\n"
            "def tag(a: Dog | Cat) -> int | str:\n"
            "    if isinstance(a, Dog):\n"
            "        return a.name\n"
            "    return str(a.lives)\n"
        )
        results = _per_stub_results(src, "tag")
        assert any(fn is None for fn, _ in results)
        assert any(r is not None and "return.overload_mismatch" in r
                   for _, r in results)
