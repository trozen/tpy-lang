"""Pins for the cross-path binding-fact subset join (tpyc/binding_audit.py).

The corpus harness is the real witness (every routed body joins there); these
pin the machinery itself -- that both sides RECORD under one function key,
that the subset arithmetic reports a missing name, and that a fallback body's
partial THIR record is dropped rather than joined.
"""

from .. import binding_audit
from ..compilation_context import activate_compiler
from ..codegen_cpp import CodeGenOptions
from .testutil import _compile, _entry

_SRC = (
    "from tpy import Int32\n"
    "class A:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
    "def f(a: A | None) -> Int32:\n"
    "    if a is None:\n        return -1\n"
    "    return a.v\n"
    "def main() -> None:\n    print(f(A(3)))\n"
    "main()\n"
)


def _run_both_passes(src: str):
    """One compiler, AST pass then THIR overlay pass -- the conftest shape,
    which is the only arrangement whose records join."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    binding_audit.set_enabled(True)
    try:
        with activate_compiler(compiler):
            compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=False))
            compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=True))
    finally:
        binding_audit.set_enabled(False)
    return compiler


def _record_for(store: dict, name: str):
    for func, rec in store.values():
        if func.name == name:
            return rec
    return None


class TestBindingFactJoin:
    def test_routed_body_joins_clean(self):
        # The pointer-repr Optional param seeds `pointers` on both sides;
        # the routed body joins with the AST union a subset of THIR's.
        compiler = _run_both_passes(_SRC)
        assert binding_audit.joined(compiler) >= 1
        assert binding_audit.violations(compiler) == []
        ast_rec = _record_for(compiler._binding_facts_ast, "f")
        thir_rec = _record_for(compiler._binding_facts_thir, "f")
        assert ast_rec is not None and "a" in ast_rec["pointers"]
        assert thir_rec is not None and "a" in thir_rec["pointers"]

    def test_missing_name_reports_violation(self):
        # Remove a recorded name from the THIR side: the join must name the
        # function, the set, and the name -- the missed-producer report.
        compiler = _run_both_passes(_SRC)
        thir_rec = _record_for(compiler._binding_facts_thir, "f")
        thir_rec["pointers"].discard("a")
        gaps = binding_audit.violations(compiler)
        assert ("f", "pointers", ["a"]) in gaps

    def test_extra_thir_name_is_legal(self):
        # Subset, not equality: a mirror seeded wider than the AST set must
        # not fire (extra names are unconsulted, not wrong-form).
        compiler = _run_both_passes(_SRC)
        thir_rec = _record_for(compiler._binding_facts_thir, "f")
        thir_rec["pointers"].add("__not_in_ast")
        assert binding_audit.violations(compiler) == []

    def test_fallback_body_publishes_nothing(self):
        # A body THIR rejects (a nested def with a param default is a named
        # reject) must have no THIR record: the failed attempt published
        # nothing, so the join never compares a body whose walk did not
        # finish. The fallback ledger must be non-empty or the pin is vacuous.
        src = (
            "from tpy import Int32\n"
            "def f() -> Int32:\n"
            "    def g(a: Int32 = 1) -> Int32:\n"
            "        return a\n"
            "    return g(2)\n"
            "def main() -> None:\n    print(f())\n"
            "main()\n"
        )
        compiler = _run_both_passes(src)
        assert any(k.startswith("body:") for k in compiler._thir_fallback), (
            compiler._thir_fallback)
        thir_names = {func.name
                      for func, _rec in compiler._binding_facts_thir.values()}
        assert "f" not in thir_names
        assert binding_audit.violations(compiler) == []

    def test_rollback_drops_published_record(self):
        # The journal arithmetic itself: a record published inside an attempt
        # window disappears at rollback (the multi-stub @overload shape --
        # stub 1 publishes, stub 2 rejects, the body folds back whole).
        compiler, _modules = _compile(_SRC)
        binding_audit.set_enabled(True)
        try:
            with activate_compiler(compiler):
                class _FakeLc:
                    binding_union = binding_audit.fresh_record()
                    func = object()
                    pointers = {"x"}
                    ptr_variant_locals = set()
                    optional_locals = set()
                    storage_tuple_locals = set()
                    const_borrow_tuple_locals = set()
                    const_opt_borrow_tuple_locals = set()
                binding_audit.begin_body()
                binding_audit.publish_thir(_FakeLc)
                assert id(_FakeLc.func) in compiler._binding_facts_thir
                binding_audit.rollback_body()
                assert id(_FakeLc.func) not in compiler._binding_facts_thir
        finally:
            binding_audit.set_enabled(False)

    def test_async_frame_window_isolated(self):
        # Regression (the exc_val leak): `_resumable_frame_ctx` restores the
        # PREVIOUS emission's binding sets at exit, so the async body's
        # window must close before that restore -- lazily-closed windows
        # attributed the previous method's pointer-param residue to every
        # async function emitted after it.
        src = (
            "from tpy import Int32\n"
            "import asyncio\n"
            "class C:\n"
            "    def __init__(self) -> None:\n        pass\n"
            "    def touch(self, exc_val: 'C | None') -> bool:\n"
            "        return exc_val is not None\n"
            "async def coro(n: Int32) -> Int32:\n"
            "    return n\n"
            "def main() -> None:\n"
            "    print(asyncio.run(coro(1)))\n"
            "main()\n"
        )
        compiler = _run_both_passes(src)
        coro_rec = _record_for(compiler._binding_facts_ast, "coro")
        assert coro_rec is not None
        assert "exc_val" not in coro_rec["pointers"]
        assert binding_audit.violations(compiler) == []

    def test_disabled_records_nothing(self):
        compiler, modules = _compile(_SRC)
        entry = _entry(modules)
        # The harness turns the audit on process-globally, so this unit must
        # establish the disabled state it is asserting about rather than
        # inheriting whatever a sibling left behind.
        binding_audit.set_enabled(False)
        try:
            with activate_compiler(compiler):
                compiler.generate_code_to_strings(
                    entry, options=CodeGenOptions(emit_source_comments=False,
                                                  thir_codegen=True))
            assert compiler._binding_facts_ast == {}
            assert compiler._binding_facts_thir == {}
        finally:
            binding_audit.set_enabled(False)


class TestAckSuppression:
    # The ACK arithmetic: `acknowledge_binding_partial(lc, label, name)`
    # suppresses exactly its (label, name) pair in `violations()` -- a
    # DIFFERENT label or name must still report. Exercised directly on a
    # synthetic record pair (the corpus call sites cannot show a wrong
    # pair being silently absorbed).
    def test_ack_suppresses_only_its_exact_pair(self):
        compiler = _run_both_passes(_SRC)
        key, (func, thir_rec) = next(
            iter(compiler._binding_facts_thir.items()))
        _f, ast_rec = compiler._binding_facts_ast[key]
        # Fabricate an AST-side fact THIR never mirrored, in two labels.
        ast_rec["pointers"] = set(ast_rec.get("pointers", set())) | {"ghost"}
        ast_rec["optional_locals"] = (
            set(ast_rec.get("optional_locals", set())) | {"ghost"})
        thir_rec.setdefault("pointers", set())
        thir_rec.setdefault("optional_locals", set())
        # Acknowledge only the pointers pair.
        thir_rec.setdefault(binding_audit.ACK_KEY, set()).add(
            ("pointers", "ghost"))
        vs = binding_audit.violations(compiler)
        labels = {(fn, label) for fn, label, _names in vs}
        assert (func.name, "pointers") not in labels
        assert (func.name, "optional_locals") in labels
        missing = next(names for fn, label, names in vs
                       if fn == func.name and label == "optional_locals")
        assert missing == ["ghost"]

    def test_stale_ack_reports_dead_ack(self):
        # The mirror-landed hazard: an ack whose name THIR now records
        # would silently absorb a future genuine miss -- violations()
        # reports it as a dead-ack row so the ack dies with the mirror.
        compiler = _run_both_passes(_SRC)
        key, (func, thir_rec) = next(
            iter(compiler._binding_facts_thir.items()))
        thir_rec.setdefault("pointers", set()).add("mirrored")
        thir_rec.setdefault(binding_audit.ACK_KEY, set()).add(
            ("pointers", "mirrored"))
        vs = binding_audit.violations(compiler)
        assert (func.name, "dead-ack:pointers", ["mirrored"]) in vs

    def test_eager_ack_with_no_fact_is_not_dead(self):
        # An eager per-shape ack whose name NEITHER side records stays
        # silent -- the ack sites register per-shape, not per-miss.
        compiler = _run_both_passes(_SRC)
        key, (func, thir_rec) = next(
            iter(compiler._binding_facts_thir.items()))
        thir_rec.setdefault(binding_audit.ACK_KEY, set()).add(
            ("pointers", "never_recorded"))
        vs = binding_audit.violations(compiler)
        assert not any(label.startswith("dead-ack:") for _fn, label, _n in vs)
