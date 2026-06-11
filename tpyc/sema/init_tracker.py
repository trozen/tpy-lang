"""
TurboPython Definite-Assignment Tracker

Tracks which variables are guaranteed to be initialized on all paths
reaching the current point. Prevents use-before-assignment errors that
would be undefined behavior in generated C++.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .flow_facts import FlowFacts
from ..prescan import FactKills, alias_group, deref_view_key

if TYPE_CHECKING:
    from ..typesys import TpyType
    from .context import SemanticContext




class InitTracker:
    """Definite-assignment and rvalue-vars flow analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def save(self) -> FlowFacts:
        return FlowFacts(
            definitely_assigned=frozenset(self.ctx.func.definitely_assigned),
            init_terminated=self.ctx.func.init_terminated,
            rvalue_vars=frozenset(self.ctx.func.rvalue_vars),
            param_provenance_vars=frozenset(self.ctx.func.param_provenance_vars),
            safe_to_return_vars=frozenset(self.ctx.func.safe_to_return_vars),
            non_null_ptr_vars=frozenset(self.ctx.func.non_null_ptr_vars),
            narrowed_types=frozenset(self.ctx.func.narrowed_types.items()),
            consumed_vars=frozenset(self.ctx.func.consumed_vars),
            owns_fresh_tuple_member_vars=frozenset(self.ctx.func.owns_fresh_tuple_member_vars.items()),
            owning_storage_tuple_vars=frozenset(self.ctx.func.owning_storage_tuple_vars),
            borrow_into_own_hazards=frozenset(self.ctx.func.borrow_into_own_hazards),
            copies_into_own_hazards=frozenset(self.ctx.func.copies_into_own_hazards),
            borrows=self.ctx.func.borrow_tracker.freeze(),
            value_ranges=frozenset(self.ctx.func.value_ranges.items()),
        )

    def restore(self, state: FlowFacts) -> None:
        self.ctx.func.definitely_assigned = set(state.definitely_assigned)
        self.ctx.func.init_terminated = state.init_terminated
        self.ctx.func.rvalue_vars = set(state.rvalue_vars)
        self.ctx.func.param_provenance_vars = set(state.param_provenance_vars)
        self.ctx.func.safe_to_return_vars = set(state.safe_to_return_vars)
        self.ctx.func.non_null_ptr_vars = set(state.non_null_ptr_vars)
        # Invariant: pending post-access ptr-narrowing flushes at statement
        # boundary (see `analyze_stmt` try/finally). Any entries surviving a
        # restore would leak into a branch they don't belong to.
        self.ctx.func.pending_non_null_ptr_vars.clear()
        self.ctx.func.narrowed_types = dict(state.narrowed_types)
        self.ctx.func.consumed_vars = set(state.consumed_vars)
        self.ctx.func.owns_fresh_tuple_member_vars = dict(state.owns_fresh_tuple_member_vars)
        self.ctx.func.owning_storage_tuple_vars = set(state.owning_storage_tuple_vars)
        self.ctx.func.borrow_into_own_hazards = set(state.borrow_into_own_hazards)
        self.ctx.func.copies_into_own_hazards = set(state.copies_into_own_hazards)
        self.ctx.func.borrow_tracker.restore_from_frozen(state.borrows)
        self.ctx.func.value_ranges = dict(state.value_ranges)

    def mark_assigned(self, name: str) -> None:
        self.ctx.func.definitely_assigned.add(name)

    def mark_terminated(self) -> None:
        self.ctx.func.init_terminated = True

    def mark_provenance(self, name: str, is_param_derived: bool) -> None:
        """Track whether a variable's storage derives from parameters/globals."""
        if is_param_derived:
            self.ctx.func.param_provenance_vars.add(name)
        else:
            self.ctx.func.param_provenance_vars.discard(name)

    def mark_safe_to_return(self, name: str, is_safe: bool) -> None:
        """Record that returning ``name`` after this binding is non-dangling.

        Membership is what is_dangling_return consults for locals, so callers
        must also keep this set a superset of param_provenance_vars (the
        merge invariant relied on by FlowFacts.merge / apply_loop_exit_facts).
        See is_safe_to_return_expr in compatibility.py for the source rule.
        """
        if is_safe:
            self.ctx.func.safe_to_return_vars.add(name)
        else:
            self.ctx.func.safe_to_return_vars.discard(name)

    def mark_non_null_ptr(self, name: str, is_non_null: bool) -> None:
        """Track whether a pointer variable is provably non-null."""
        if is_non_null:
            self.ctx.func.non_null_ptr_vars.add(name)
        else:
            self.ctx.func.non_null_ptr_vars.discard(name)

    def add_loop_var_provenance(self, name: str) -> None:
        # Both sets must move together: leaving safe_to_return_vars empty
        # while populating param_provenance_vars would let an intersecting
        # merge drop the var from safe_to_return_vars while keeping it in
        # param_provenance_vars, breaking the documented subset invariant.
        self.ctx.func.param_provenance_vars.add(name)
        self.ctx.func.safe_to_return_vars.add(name)

    def remove_loop_var_provenance(self, name: str) -> None:
        self.ctx.func.param_provenance_vars.discard(name)
        self.ctx.func.safe_to_return_vars.discard(name)

    def apply_fact_kills(self, kills: FactKills) -> None:
        """Drop check-elision facts the given kill-set may invalidate.

        Touches only the UB-driving fact families (type narrowing, ptr
        non-null, value ranges); definite-assignment and the hazard sets
        are managed by their own merge policies.
        """
        f = self.ctx.func

        def _sweep(killed_key: str, kill_self: bool) -> None:
            prefix = killed_key + "."
            for k in [k for k in f.narrowed_types
                      if k.startswith(prefix)
                      or (kill_self and (k == killed_key
                                         or k == deref_view_key(killed_key)))]:
                del f.narrowed_types[k]
            if not kill_self:
                # Contents may change behind the key; the deref-view payload
                # narrowing reaches through it, so it dies even when the
                # key's own narrowing survives.
                f.narrowed_types.pop(deref_view_key(killed_key), None)
            for k in [k for k in f.non_null_ptr_vars
                      if k.startswith(prefix) or (kill_self and k == killed_key)]:
                f.non_null_ptr_vars.discard(k)
            for k in [k for k in f.value_ranges
                      if k.startswith(prefix) or (kill_self and k == killed_key)]:
                del f.value_ranges[k]
            root = killed_key.split(".", 1)[0]
            for k in [k for k, v in f.value_ranges.items()
                      if v.hi_len_of in (killed_key, root)]:
                del f.value_ranges[k]

        # Rebinds (names) kill only the spelled name's facts -- rebinding one
        # alias does not touch the others. Mutations (paths, receivers) reach
        # the shared object, so they kill across the static alias group,
        # mirroring NarrowingTracker._invalidate_field_facts. Coverage is
        # deliberately a superset of the live path: _sweep also clears
        # dotted-key value ranges and deref-view keys under receivers, which
        # the live invalidation never keys today -- if dotted range facts are
        # ever added, extend the live path too or it under-invalidates.
        aliases = f.current_alias_sources
        for name in kills.names:
            _sweep(name, kill_self=True)
        for path in kills.paths:
            root, _, rest = path.partition(".")
            for alias_root in alias_group(aliases, root):
                _sweep(f"{alias_root}.{rest}" if rest else alias_root,
                       kill_self=True)
        for recv in kills.receivers:
            root, _, rest = recv.partition(".")
            for alias_root in alias_group(aliases, root):
                _sweep(f"{alias_root}.{rest}" if rest else alias_root,
                       kill_self=False)

    def apply_loop_entry_facts(
        self,
        before: FlowFacts,
        condition_type_facts: dict[str, TpyType] | None = None,
        kills: FactKills | None = None,
    ) -> None:
        """Apply flow facts at loop body entry.

        Restores the state from just before the loop, so that outer
        narrowing proven by enclosing if-blocks is preserved inside the
        loop body. The body's kill-set is then applied (the back-edge may
        re-enter the body after the facts were invalidated -- single-pass
        analysis has no fixpoint, so killed facts must not be assumed at
        entry). Any additional narrowing the loop condition itself proves
        (e.g. ``while x is not None``) is layered on top last, since the
        condition is re-proven on every iteration.
        """
        self.ctx.func.definitely_assigned = set(before.definitely_assigned)
        self.ctx.func.init_terminated = False
        self.ctx.func.rvalue_vars = set(before.rvalue_vars)
        self.ctx.func.param_provenance_vars = set(before.param_provenance_vars)
        self.ctx.func.safe_to_return_vars = set(before.safe_to_return_vars)
        self.ctx.func.non_null_ptr_vars = set(before.non_null_ptr_vars)
        self.ctx.func.narrowed_types = dict(before.narrowed_types)
        self.ctx.func.consumed_vars = set(before.consumed_vars)
        self.ctx.func.owns_fresh_tuple_member_vars = dict(before.owns_fresh_tuple_member_vars)
        self.ctx.func.owning_storage_tuple_vars = set(before.owning_storage_tuple_vars)
        self.ctx.func.borrow_into_own_hazards = set(before.borrow_into_own_hazards)
        self.ctx.func.copies_into_own_hazards = set(before.copies_into_own_hazards)
        self.ctx.func.borrow_tracker.restore_from_frozen(before.borrows)
        self.ctx.func.value_ranges = dict(before.value_ranges)
        if kills is not None:
            self.apply_fact_kills(kills)
        if condition_type_facts is not None:
            self.ctx.func.narrowed_types.update(condition_type_facts)

    def merge_branches(self, then_state: FlowFacts, else_state: FlowFacts) -> None:
        self.restore(FlowFacts.merge(then_state, else_state))

    def apply_loop_exit_facts(self, before: FlowFacts) -> None:
        """Apply post-loop state for a may-execute-zero-times loop.

        Call with live post-body state still in ``ctx.func.*``. Monotone
        kill-facts (pointer non-null, parameter provenance, safe-to-return,
        type narrowing) survive iff they held both before the loop AND at
        body exit, so a reassignment that cleared a fact is not undone by
        ``restore(before)``.

        Single-pass conservative approximation -- proper fixpoint belongs
        with the THIR/MIR migration (see ``docs/IR_DESIGN.md``).
        """
        body_end_nn_ptr = frozenset(self.ctx.func.non_null_ptr_vars)
        body_end_param_prov = frozenset(self.ctx.func.param_provenance_vars)
        body_end_safe_return = frozenset(self.ctx.func.safe_to_return_vars)
        body_end_narrowed = frozenset(self.ctx.func.narrowed_types.items())
        # Hazard fact: union the body-end value in (a tuple local assigned
        # owned-fresh inside the loop stays flagged after it -- the inverse of
        # the monotone kill-facts' intersect, so no dangle is dropped).
        body_end_owns_fresh = dict(self.ctx.func.owns_fresh_tuple_member_vars)
        # Owning-storage is a hazard fact too: an owning binding inside the loop
        # must survive after it (union body-end in, like owns-fresh).
        body_end_owning = set(self.ctx.func.owning_storage_tuple_vars)
        # Borrow-into-Own hazard: same UNION-in policy -- a plain-borrow element
        # bound inside the loop stays flagged after it.
        body_end_borrow_own = set(self.ctx.func.borrow_into_own_hazards)
        body_end_copies_own = set(self.ctx.func.copies_into_own_hazards)
        self.restore(before)
        self.ctx.func.non_null_ptr_vars &= body_end_nn_ptr
        self.ctx.func.param_provenance_vars &= body_end_param_prov
        self.ctx.func.safe_to_return_vars &= body_end_safe_return
        self.ctx.func.narrowed_types = dict(body_end_narrowed & before.narrowed_types)
        for name, idx in body_end_owns_fresh.items():
            self.ctx.func.owns_fresh_tuple_member_vars.setdefault(name, idx)
        self.ctx.func.owning_storage_tuple_vars |= body_end_owning
        self.ctx.func.borrow_into_own_hazards |= body_end_borrow_own
        self.ctx.func.copies_into_own_hazards |= body_end_copies_own
