"""THIR's movable WORKING set: built at the decl arms that promote, never
seeded wholesale from sema's raw owned-locals fact.

`analyzer.function_movable_locals` means "sema proved this local owned"; the
set the move sites read means "owned AND declared by an arm that promotes".
Conflating them moves a value-typed local (a view-promoted `str`, a BigInt)
and a ptr-variant alias where the AST copies -- invisible to the byte-diff
until some arm starts trusting the verdict. The last test here is the
detector for that whole class: it joins the two paths' move verdicts on the
shared AST node and fails on any disagreement.
"""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..codegen_cpp.expressions import ExpressionGenerator
from ..parse.nodes import TpyCoerce, TpyName
from .lower.context import _LowerCtx
from .testutil import (_assert_routes_byte_identical, _compile, _entry)


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    _, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=thir))
    return cpp


# A view-promoted `str` local: sema marks it owned, the AST's tier-1 decl arm
# skips it (value-typed), so its last-use read into an owned sink COPIES.
_VALUE_STR = (
    "from tpy import Own\n"
    "def in_list() -> Own[list[str]]:\n"
    "    label: str = 'no'\n"
    "    return [label]\n"
    "def main() -> None:\n"
    "    print(in_list())\n"
    "main()\n"
)

# `a` is a ptr-variant union local -- its `__slot_N` owns the value, so the
# local is a non-owning alias and the AST never promotes it.
_PTR_VARIANT = (
    "from tpy import Int32\n"
    "class Cat:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class Dog:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "def main() -> None:\n"
    "    a: Cat | Dog = Cat(1)\n"
    "    items: list[Cat | Dog] = [a]\n"
    "    print(len(items))\n"
    "main()\n"
)

# The boundary: an Own-element tuple IS promoted despite being value-typed
# (it is by-value storage with no borrow form, so it owns its elements).
_OWN_TUPLE = (
    "from tpy import Own, nocopy, Int32\n"
    "@nocopy\n"
    "class Counter:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "def make_pair() -> tuple[Own[Counter], Own[Counter]]:\n"
    "    return (Counter(1), Counter(2))\n"
    "def consume(c: Own[Counter]) -> None:\n"
    "    print(c.n)\n"
    "def main() -> None:\n"
    "    t = make_pair()\n"
    "    a, b = t\n"
    "    consume(a)\n"
    "    consume(b)\n"
    "main()\n"
)

# The other boundary: a frame-promoted VALUE local (BigInt) moves at the async
# return -- the frame decl arm carries no value-type filter.
_FRAME_VALUE = (
    "import asyncio\n"
    "async def value(n: int) -> int:\n"
    "    return n\n"
    "async def loop_sum(n: int) -> int:\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < n:\n"
    "        total = total + await value(1)\n"
    "        i = i + 1\n"
    "    return total\n"
    "def main() -> None:\n"
    "    print(asyncio.run(loop_sum(5)))\n"
    "main()\n"
)


class TestWorkingSetSeed:
    def test_locals_are_not_seeded_at_construction(self):
        """The raw sema fact must not reach the working set up front -- the
        whole defect class is one being mistaken for the other."""
        compiler, modules = _compile(_VALUE_STR)
        entry = _entry(modules)
        fn = next(f for f in entry.ast.functions if f.name == "in_list")
        lc = _LowerCtx(fn, entry.analyzer, None)
        assert "label" in lc.sema_movable_locals, (
            "fixture no longer exercises a sema-owned local")
        assert "label" not in lc.movable_locals, (
            "sema's raw owned-locals fact leaked into the working set")

    def test_promote_movable_is_the_only_door(self):
        compiler, modules = _compile(_VALUE_STR)
        entry = _entry(modules)
        fn = next(f for f in entry.ast.functions if f.name == "in_list")
        lc = _LowerCtx(fn, entry.analyzer, None)
        lc.promote_movable("label")
        assert "label" in lc.movable_locals
        # A name sema never proved owned stays out, whatever the arm asks.
        lc.promote_movable("not_a_local")
        assert "not_a_local" not in lc.movable_locals


class TestNonPromotingArms:
    def test_value_typed_local_copies_into_an_owned_sink(self):
        cpp = _cpp(_VALUE_STR, thir=True)
        assert "std::move(label)" not in cpp, (
            "a value-typed sema-owned local must not move -- the AST's "
            "tier-1 decl arm skips it")
        assert "std::string(label)" in cpp

    def test_value_typed_local_byte_identical(self):
        _assert_routes_byte_identical(_VALUE_STR)

    def test_ptr_variant_local_copies_at_a_container_element(self):
        cpp = _cpp(_PTR_VARIANT, thir=True)
        assert "std::move(a)" not in cpp, (
            "a ptr-variant local aliases its slot; the AST never promotes it")

    def test_ptr_variant_local_byte_identical(self):
        _assert_routes_byte_identical(_PTR_VARIANT)


class TestPromotingArmsStillMove:
    """The boundary: two arms promote value-typed names on purpose. A filter
    that keyed on the TYPE rather than the ARM would break both -- it did,
    on 17 cases, which is why the promotion mirrors the arm."""

    def test_own_element_tuple_moves_at_its_unpack(self):
        cpp = _cpp(_OWN_TUPLE, thir=True)
        assert "std::move(t)" in cpp

    def test_own_element_tuple_byte_identical(self):
        _assert_routes_byte_identical(_OWN_TUPLE)

    def test_frame_value_local_moves_at_the_async_return(self):
        cpp = _cpp(_FRAME_VALUE, thir=True)
        assert "::tpy::BigInt __tpy_async_ret = std::move(total);" in cpp

    def test_frame_value_local_byte_identical(self):
        _assert_routes_byte_identical(_FRAME_VALUE)


class TestVerdictsAgreeWithTheAst:
    """The durable detector. Both paths decide moves with the same shape of
    predicate over the SAME TpyName objects, so their verdicts join exactly on
    node identity -- any disagreement is a move-vs-copy divergence, including
    one no current arm happens to render."""

    SOURCES = (_VALUE_STR, _PTR_VARIANT, _OWN_TUPLE, _FRAME_VALUE)

    @staticmethod
    def _peel(e):
        while isinstance(e, TpyCoerce):
            e = e.expr
        return e

    def _disagreements(self, src: str) -> list[tuple[str, bool, bool]]:
        from . import lower as thir_lower_pkg
        import importlib
        import pkgutil
        import tpyc.thir.lower.expressions as thir_expr

        ast_v: dict[int, tuple[str, bool]] = {}
        thir_v: dict[int, tuple[str, bool]] = {}
        orig_ast = ExpressionGenerator._is_last_use_movable
        orig_thir = thir_expr._is_move_source
        peel = self._peel

        def ast_patched(self_, expr, movable_names=None):
            r = orig_ast(self_, expr, movable_names)
            inner = peel(expr)
            if isinstance(inner, TpyName) and movable_names is None:
                prev = ast_v.get(id(inner), (inner.name, False))
                ast_v[id(inner)] = (inner.name, prev[1] or r)
            return r

        def thir_patched(value, lc, movable_names=None):
            r = orig_thir(value, lc, movable_names)
            inner = peel(value)
            if isinstance(inner, TpyName) and movable_names is None:
                prev = thir_v.get(id(inner), (inner.name, False))
                thir_v[id(inner)] = (inner.name, prev[1] or r)
            return r

        patched_mods = []
        ExpressionGenerator._is_last_use_movable = ast_patched
        for m in pkgutil.iter_modules(thir_lower_pkg.__path__):
            mod = importlib.import_module(f"tpyc.thir.lower.{m.name}")
            if getattr(mod, "_is_move_source", None) is orig_thir:
                mod._is_move_source = thir_patched
                patched_mods.append(mod)
        try:
            compiler, modules = _compile(src)
            entry = _entry(modules)
            for thir in (False, True):
                compiler.generate_code_to_strings(
                    entry, options=CodeGenOptions(emit_source_comments=False,
                                                  thir_codegen=thir))
        finally:
            ExpressionGenerator._is_last_use_movable = orig_ast
            for mod in patched_mods:
                mod._is_move_source = orig_thir
        return [(t[0], a[1], t[1])
                for nid, t in thir_v.items()
                if (a := ast_v.get(nid)) is not None and a[1] != t[1]]

    def test_no_source_disagrees(self):
        for src in self.SOURCES:
            bad = self._disagreements(src)
            assert not bad, (
                f"move verdicts diverge (name, ast, thir): {bad}\n"
                f"source:\n{src}")

    def test_the_detector_can_fail(self):
        """A wholesale seed is exactly what this must catch -- inject it."""
        import tpyc.thir.lower.context as thir_ctx
        orig_init = thir_ctx._LowerCtx.__init__

        def seeded_init(self, *a, **kw):
            orig_init(self, *a, **kw)
            self.movable_locals.update(self.sema_movable_locals)

        thir_ctx._LowerCtx.__init__ = seeded_init
        try:
            bad = self._disagreements(_VALUE_STR)
        finally:
            thir_ctx._LowerCtx.__init__ = orig_init
        assert bad, "the detector passed a deliberately over-seeded set"
