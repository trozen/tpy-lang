"""The branch_scope mechanism: registry completeness (every mutable per-name
slot on _LowerCtx must be classified branch-scoped or function-scoped) and
snapshot/restore semantics (adds AND removals undo at the pop; deliberately
function-scoped state survives)."""

from .lower.context import (
    _BRANCH_SCOPED_SETS,
    _FUNCTION_SCOPED_STATE,
    _LowerCtx,
    ValueOptKind,
)
from .testutil import _compile, _entry

_SRC = """
def f(x: int) -> int:
    return x + 1
"""


def _make_lc() -> _LowerCtx:
    compiler, modules = _compile(_SRC)
    entry = _entry(modules)
    return _LowerCtx(entry.ast.functions[0], entry.analyzer, None)


class TestScopeRegistry:
    def test_every_mutable_slot_is_classified(self):
        """A new set/dict slot on _LowerCtx must be added to exactly one of
        the two scoping registries -- the failure mode this guards is the
        missed-restore bug class (a branch-registered name leaking into a
        sibling branch's classification)."""
        lc = _make_lc()
        classified = set(_BRANCH_SCOPED_SETS) | set(_FUNCTION_SCOPED_STATE)
        # Composite objects with their OWN scoping discipline: `narrow`
        # composes its snapshot into branch_scope; analyzer/func/prescan are
        # per-function facts swapped wholesale (the nested-def scope). A new
        # composite slot must be added here CONSCIOUSLY, not slip through.
        # `literal_facts` is arm-scoped by explicit save/restore in
        # `_lower_scalar_arms` (a dict -- the branch snapshot's set() copy
        # would drop its values).
        composite = {"narrow", "analyzer", "func", "prescan", "params",
                     "literal_facts"}
        unclassified = []
        for slot in _LowerCtx.__slots__:
            value = getattr(lc, slot)
            # frozensets are init-only facts; scalar-ish values and the
            # render callables carry no per-name state. Everything else
            # mutable (set/dict/list AND any future custom container) must
            # be classified or consciously listed composite.
            if isinstance(value, frozenset) or slot in composite:
                continue
            if (value is None or isinstance(value, (str, bool, int))
                    or callable(value)):
                continue
            if slot not in classified:
                unclassified.append(slot)
        assert not unclassified, (
            f"unclassified mutable _LowerCtx slots: {unclassified}; add each "
            "to _BRANCH_SCOPED_SETS or _FUNCTION_SCOPED_STATE (context.py), "
            "or to this test's composite list if it manages its own scope")

    def test_registries_are_disjoint_and_real(self):
        lc = _make_lc()
        both = set(_BRANCH_SCOPED_SETS) & set(_FUNCTION_SCOPED_STATE)
        assert not both
        for name in _BRANCH_SCOPED_SETS:
            # Plain name-sets plus the kind-tagged value_opt_bindings dict;
            # both restore by whole-copy snapshot.
            assert isinstance(getattr(lc, name), (set, dict)), name
        for name in _FUNCTION_SCOPED_STATE:
            assert hasattr(lc, name), name


class TestBranchScope:
    def test_adds_pop(self):
        lc = _make_lc()
        with lc.branch_scope():
            lc.pointers.add("p")
            lc.value_opt_bindings["v"] = ValueOptKind.SCALAR
            lc.movable_locals.add("m")
            lc.forbidden_writes.add("w")
        assert "p" not in lc.pointers
        assert "v" not in lc.value_opt_bindings
        assert "m" not in lc.movable_locals
        assert "w" not in lc.forbidden_writes

    def test_removals_pop(self):
        """The for-each shadow: names REMOVED for a body scope come back at
        the pop through the same symmetric restore."""
        lc = _make_lc()
        lc.frame_slots.add("g")
        lc.pointers.add("p")
        with lc.branch_scope():
            lc.frame_slots -= {"g"}
            lc.pointers -= {"p"}
            assert "g" not in lc.frame_slots
        assert "g" in lc.frame_slots
        assert "p" in lc.pointers

    def test_narrow_pops(self):
        lc = _make_lc()
        with lc.branch_scope():
            lc.narrow.narrowed["x"] = "__x"
            lc.narrow.persistent_aliases.add("__x")
        assert "x" not in lc.narrow.narrowed
        assert "__x" not in lc.narrow.persistent_aliases

    def test_function_scoped_state_survives(self):
        lc = _make_lc()
        lc.unhandled_hoists.add("h")
        with lc.branch_scope():
            lc.unhandled_hoists.discard("h")
            lc.nested_def_locals.add("f")
        assert "h" not in lc.unhandled_hoists  # the drain is the accounting
        assert "f" in lc.nested_def_locals    # survives like the AST re-add

    def test_nesting_restores_per_level(self):
        lc = _make_lc()
        with lc.branch_scope():
            lc.pointers.add("outer")
            with lc.branch_scope():
                lc.pointers.add("inner")
            assert "inner" not in lc.pointers
            assert "outer" in lc.pointers
        assert "outer" not in lc.pointers

    def test_exception_still_restores(self):
        lc = _make_lc()
        try:
            with lc.branch_scope():
                lc.pointers.add("p")
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert "p" not in lc.pointers
