"""
TurboPython Definite-Assignment Tracker

Tracks which variables are guaranteed to be initialized on all paths
reaching the current point. Prevents use-before-assignment errors that
would be undefined behavior in generated C++.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from functools import reduce

from .flow_facts import (
    FlowFacts, GuardFacts, merge_borrow_triples, merge_binding_provenance,
    narrowed_dict,
)
from .value_range import ValueRange
from ..typesys import is_any_int_type, unwrap_qualifiers
from ..prescan import FactKills, LiteralLocals, fact_key_root
from .storage_walk import StorageKey
from .context import NestedMutationMark, OwnSlot

if TYPE_CHECKING:
    from ..holders import Holder
    from ..typesys import TpyType
    from .context import LoanInfo, SemanticContext
    from .narrowing import NarrowingTracker




class InitTracker:
    """Definite-assignment and rvalue-vars flow analysis."""

    def __init__(self, ctx: SemanticContext, narrowing: NarrowingTracker):
        self.ctx = ctx
        self.narrowing = narrowing

    def save(self) -> FlowFacts:
        return FlowFacts(
            definitely_assigned=frozenset(self.ctx.func.definitely_assigned),
            loop_bound_assigned=frozenset(self.ctx.func.loop_bound_assigned),
            init_terminated=self.ctx.func.init_terminated,
            rvalue_vars=frozenset(self.ctx.func.rvalue_vars),
            non_null_ptr_vars=frozenset(self.ctx.func.non_null_ptr_vars),
            narrowed_types=frozenset(self.ctx.func.narrowed_types.items()),
            consumed_vars=frozenset(self.ctx.func.consumed_vars),
            binding_provenance=frozenset(self.ctx.func.binding_provenance.items()),
            borrows=self.ctx.func.borrow_tracker.freeze(),
            value_ranges=frozenset(self.ctx.func.value_ranges.items()),
        )

    def restore(self, state: FlowFacts) -> None:
        self.ctx.func.definitely_assigned = set(state.definitely_assigned)
        self.ctx.func.loop_bound_assigned = set(state.loop_bound_assigned)
        self.ctx.func.init_terminated = state.init_terminated
        self.ctx.func.rvalue_vars = set(state.rvalue_vars)
        self.ctx.func.non_null_ptr_vars = set(state.non_null_ptr_vars)
        # A pending post-access pointer fact is the current statement's
        # (flushed at its end); it never crosses a snapshot.
        self.ctx.func.pending_non_null_ptr_vars.clear()
        self.ctx.func.narrowed_types = narrowed_dict(state.narrowed_types)
        self.ctx.func.consumed_vars = set(state.consumed_vars)
        self.ctx.func.binding_provenance = dict(state.binding_provenance)
        self.ctx.func.borrow_tracker.restore_from_frozen(state.borrows)
        self.ctx.func.value_ranges = dict(state.value_ranges)

    def mark_assigned(self, name: str) -> None:
        self.ctx.func.definitely_assigned.add(name)

    def mark_terminated(self) -> None:
        self.ctx.func.init_terminated = True

    def mark_provenance(self, name: str, is_param_derived: bool) -> None:
        """Track whether a variable's storage derives from parameters/globals."""
        self.ctx.func.bp_set_param_derived(name, is_param_derived)

    def mark_safe_to_return(self, name: str, is_safe: bool) -> None:
        """Record that returning ``name`` after this binding is non-dangling.

        This is what is_dangling_return consults for locals; param_derived is
        a subset (see BindingProvenance / is_safe_to_return_expr).
        """
        self.ctx.func.bp_set_safe_to_return(name, is_safe)

    def mark_non_null_ptr(self, name: str, is_non_null: bool) -> None:
        """Track whether a pointer variable is provably non-null."""
        if is_non_null:
            self.ctx.func.non_null_ptr_vars.add(name)
        else:
            self.ctx.func.non_null_ptr_vars.discard(name)

    def flush_pending_ptr_facts(self) -> None:
        """Make the pointer facts the expressions analyzed so far proved
        live: at a point sequenced after all of them."""
        f = self.ctx.func
        if f.pending_non_null_ptr_vars:
            f.non_null_ptr_vars |= f.pending_non_null_ptr_vars
            f.pending_non_null_ptr_vars.clear()

    def add_loop_var_provenance(self, name: str) -> None:
        self.ctx.func.bp_add_loop_var_provenance(name)

    def remove_loop_var_provenance(self, name: str) -> None:
        self.ctx.func.bp_remove_loop_var_provenance(name)

    @contextmanager
    def hidden(self, names: 'frozenset[str]',
               kills: 'Callable[[], FactKills] | None' = None
               ) -> Iterator[None]:
        """A scope of its own binding `names` (a comprehension's targets, a
        lambda's parameters): what the function state knows under those
        names (`HIDDEN_FIELDS`) is about the enclosing bindings, so it is
        parked on entry and put back on exit, and what the scope learned
        under them is dropped there. A range of another name bounded by
        the length of a hidden one keeps its concrete part inside. `kills`
        (a comprehension, which runs its body here) gives the body's
        fact-kill set, collected once the body is analyzed: replayed after
        the restore, but for what it kills through a hidden name, which is
        the scope's own binding."""
        if not names:
            yield
            return
        parked = self._park(names)
        completed = False
        try:
            yield
            completed = True
        finally:
            # Re-read: a trial scope inside restores the function state as a
            # copy.
            self._unpark(names, parked)
            # An unwind's state is the handler's to restore; it runs no
            # body after the scope.
            if completed and kills is not None:
                self.apply_fact_kills(kills().outside(names))

    def _park(self, names: 'frozenset[str]') -> 'dict[str, object]':
        f = self.ctx.func
        parked: dict[str, object] = {}
        for fld in HIDDEN_FIELDS:
            table = getattr(f, fld)
            if isinstance(table, LiteralLocals):
                parked[fld] = table
                setattr(f, fld, table.without(names))
            elif isinstance(table, list):
                # A log: what it held before is history no reader inside the
                # scope takes; the entries the scope appends are its own.
                parked[fld] = len(table)
            else:
                mine = [k for k in table if _key_rooted_in(k, names)]
                if isinstance(table, dict):
                    parked[fld] = {k: table.pop(k) for k in mine}
                else:
                    parked[fld] = set(mine)
                    table.difference_update(mine)
        bounded = {k: v for k, v in f.value_ranges.items()
                   if v.hi_len_of is not None
                   and fact_key_root(v.hi_len_of) in names}
        for k, v in bounded.items():
            f.value_ranges[k] = replace(v, hi_len_of=None)
        parked["bounded"] = bounded
        return parked

    def _unpark(self, names: 'frozenset[str]',
                parked: 'dict[str, object]') -> None:
        f = self.ctx.func
        for fld in HIDDEN_FIELDS:
            table = getattr(f, fld)
            held = parked[fld]
            if isinstance(held, LiteralLocals):
                setattr(f, fld, held)
                continue
            if isinstance(table, list):
                assert isinstance(held, int)
                table[held:] = [k for k in table[held:]
                                if not _key_rooted_in(k, names)]
                continue
            for k in [k for k in table if _key_rooted_in(k, names)]:
                if isinstance(table, dict):
                    del table[k]
                else:
                    table.discard(k)
            table.update(held)
        for k, v in parked["bounded"].items():
            if k in f.value_ranges:
                f.value_ranges[k] = v

    def apply_fact_kills(self, kills: FactKills, *,
                         keep_steps: bool = False) -> None:
        """Drop check-elision facts the given kill-set may invalidate.

        Touches only the UB-driving fact families (type narrowing, ptr
        non-null, value ranges); definite-assignment and the hazard sets
        are managed by their own merge policies.
        """
        f = self.ctx.func
        if kills.suspends:
            # The body suspends, so this meet crosses a suspension point
            # (back-edge re-entry, or handler/finally entry after a
            # mid-body suspension) -- everything a suspension kills must
            # die here too, or the restore resurrects it.
            self.narrowing.invalidate_suspension_facts()
        elif kills.calls:
            self.narrowing.invalidate_closure_written_facts()

        def _sweep(name: str) -> None:
            for k in [k for k in f.narrowed_types if fact_key_root(k) == name]:
                del f.narrowed_types[k]
            for k in [k for k in f.non_null_ptr_vars
                      if fact_key_root(k) == name]:
                f.non_null_ptr_vars.discard(k)
            for k in [k for k, v in f.value_ranges.items()
                      if fact_key_root(k) == name
                      or (v.hi_len_of is not None
                          and fact_key_root(v.hi_len_of) == name)]:
                del f.value_ranges[k]

        rel = f.in_place_writes
        copy = self.narrowing.holds_copy

        def _kill_written(key: str, own: bool,
                          keeps: TpyType | None = None) -> None:
            for k in self.narrowing.narrowing_kills(
                    key, f.narrowed_types, own=own, keeps=keeps):
                del f.narrowed_types[k]
            for k in rel.affected_facts(key, f.non_null_ptr_vars, own=own,
                                        is_copy=copy):
                f.non_null_ptr_vars.discard(k)
            for k in rel.affected_facts(key, f.value_ranges, own=own,
                                        is_copy=copy):
                del f.value_ranges[k]
            root = key.split(".", 1)[0]
            for k in [k for k, v in f.value_ranges.items()
                      if v.hi_len_of is not None
                      and (v.hi_len_of in (key, root)
                           or rel.may_share(v.hi_len_of, root))]:
                del f.value_ranges[k]

        # A rebound name loses the facts spelled through that name only (the
        # live rebind kills through every spelling: TODO.md "One fact-kill
        # helper"). A field store or a call reaches the written object under
        # every spelling the may-hold relation gives it, as the live path
        # does; this sweep also clears dotted range keys.
        # The interim sema form of TODO.md "Loop-carried value ranges over
        # MIR places": a name the body only steps one way keeps the bound on
        # the other side.
        stepped = kills.stepped() if keep_steps else {}
        for name in kills.names:
            kept = self._stepped_bound(name, stepped.get(name))
            _sweep(name)
            if kept is not None:
                f.value_ranges[name] = kept
        for path, stores in kills.paths.items():
            # A path is a field store target (`o.f = v`, `o.f += v`,
            # `del o.f`): it replaces the slot its last field names.
            _kill_written(path, own=True,
                          keeps=self.narrowing.stores_keep(stores))
        for recv in kills.receivers:
            _kill_written(recv, own=False)

    def _stepped_bound(self, name: str,
                       direction: str | None) -> ValueRange | None:
        """The bound of `name`'s range that a body stepping it only in
        `direction` keeps: the lower one under `+`, the upper under `-`."""
        if direction is None:
            return None
        f = self.ctx.func
        current = f.value_ranges.get(name)
        if current is None or name in f.closure_written_names:
            return None
        if self.narrowing.is_rebindable_global(name):
            return None
        scope = f.current_scope
        typ = scope.bound_type(name) if scope is not None else None
        if typ is None or not is_any_int_type(unwrap_qualifiers(typ)):
            return None
        if direction == "+" and current.lo is not None:
            return ValueRange(lo=current.lo, non_zero=current.lo > 0)
        if direction == "-" and current.hi is not None:
            return ValueRange(hi=current.hi, non_zero=current.hi < 0)
        return None

    def apply_loop_entry_facts(
        self,
        before: FlowFacts,
        kills: FactKills | None = None,
    ) -> None:
        """Apply flow facts at loop body entry.

        Restores the state from just before the loop, so that outer
        narrowing proven by enclosing if-blocks is preserved inside the
        loop body. The body's kill-set is then applied (the back-edge may
        re-enter the body after the facts were invalidated -- single-pass
        analysis has no fixpoint, so killed facts must not be assumed at
        entry); a name the body only steps one way keeps the bound on the
        other side (`keep_steps`).
        """
        self.ctx.func.definitely_assigned = set(before.definitely_assigned)
        self.ctx.func.loop_bound_assigned = set(before.loop_bound_assigned)
        self.ctx.func.init_terminated = False
        self.ctx.func.rvalue_vars = set(before.rvalue_vars)
        self.ctx.func.non_null_ptr_vars = set(before.non_null_ptr_vars)
        self.ctx.func.narrowed_types = narrowed_dict(before.narrowed_types)
        self.ctx.func.consumed_vars = set(before.consumed_vars)
        self.ctx.func.binding_provenance = dict(before.binding_provenance)
        self.ctx.func.borrow_tracker.restore_from_frozen(before.borrows)
        self.ctx.func.value_ranges = dict(before.value_ranges)
        if kills is not None:
            self.apply_fact_kills(kills, keep_steps=True)

    def join_entry_from_any_point(self, before: FlowFacts,
                                  kills: FactKills,
                                  consumed: frozenset[str]) -> None:
        """Enter a point control may reach from anywhere in a region: a
        loop's `break` exit, a `finally` body, the code after a suppressing
        `with`. Only the check-elision facts that held at the region's start
        and that nothing in it kills (`kills`) survive, joined with the
        current ones; a name a traversal consumed anywhere in the region
        (`consumed`, a returning branch's included) may be consumed here."""
        self.join_facts(before)
        self.apply_fact_kills(kills)
        self.join_consumed(consumed)

    def join_consumed(self, consumed: frozenset[str]) -> None:
        """Names a traversal consumed at some point of a region control may
        leave from anywhere (an exception) may be consumed where it lands."""
        self.ctx.func.consumed_vars |= consumed

    def merge_branches(self, then_state: FlowFacts, else_state: FlowFacts) -> None:
        self.restore(FlowFacts.merge(then_state, else_state))

    def apply_guard(self, guard: GuardFacts) -> None:
        """Enter the path a test proves `guard` on."""
        f = self.ctx.func
        f.narrowed_types.update(guard.types)
        f.non_null_ptr_vars |= guard.non_null_ptrs
        self.apply_range_facts(guard.ranges)

    def apply_range_facts(self, facts: Mapping[str, ValueRange]) -> None:
        """Apply integer range facts, intersecting with any existing ranges."""
        ranges = self.ctx.func.value_ranges
        for name, new_range in facts.items():
            existing = ranges.get(name)
            ranges[name] = (new_range if existing is None
                            else ValueRange.intersect(existing, new_range))

    def join_facts(self, other: FlowFacts) -> None:
        """Join the check-elision facts (narrowing, non-null pointers,
        value ranges) of the current state with those of `other`."""
        merged = FlowFacts.merge(self.save(), other)
        f = self.ctx.func
        f.narrowed_types = narrowed_dict(merged.narrowed_types)
        f.non_null_ptr_vars = set(merged.non_null_ptr_vars)
        f.value_ranges = dict(merged.value_ranges)

    def join(self) -> FlowJoin:
        """Start collecting the ends of the paths through a construct."""
        return FlowJoin(self)

    def apply_loop_exit_facts(self, before: FlowFacts, *,
                              runs_once: bool = True,
                              exit_from_head: bool = False) -> None:
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
        body_end_narrowed = frozenset(self.ctx.func.narrowed_types.items())
        body_end_bp = frozenset(self.ctx.func.binding_provenance.items())
        body_end_borrows = self.ctx.func.borrow_tracker.freeze()
        body_end_loop_bound = frozenset(self.ctx.func.loop_bound_assigned)
        self.restore(before)
        # A local a NESTED loop body bound is function-scoped, so the fact
        # that it is assigned outlives this loop -- but only past a head this
        # loop provably ran.
        if runs_once:
            self.ctx.func.loop_bound_assigned |= body_end_loop_bound
        if not exit_from_head:
            # `before` is the pre-loop state; a `while` exit is its head's
            # FALSE path, analyzed in the loop-entry state, which already
            # holds for every pass.
            self.ctx.func.non_null_ptr_vars &= body_end_nn_ptr
            self.ctx.func.narrowed_types = narrowed_dict(
                body_end_narrowed & before.narrowed_types)
        self._merge_loop_body_loans(before, body_end_borrows)
        # Same lattice as a both-arms-live branch join: MUST facts (return
        # safety) intersect so a fact cleared in the body is not reinstated;
        # HAZARD facts (tuple-member) union in so a hazard introduced in the
        # body survives the loop.
        self.ctx.func.binding_provenance = dict(merge_binding_provenance(
            before.binding_provenance, body_end_bp, False, False))

    def _merge_loop_body_loans(
        self,
        before: FlowFacts,
        body_end_borrows: frozenset[tuple[StorageKey, Holder, LoanInfo]],
    ) -> None:
        """Carry the loans the loop body took past the loop's closing brace.

        A loan bound in the body is still held after the last iteration -- the
        holder reads it there -- so dropping it with ``restore(before)`` hides
        every hazard a post-loop statement poses to it. Same lattice as a
        both-arms-live join: the union, each pair at its more dangerous kind.

        The loop's own iterator is not among them: `loop_scope` releases it
        where the statement ends, before the caller freezes the body-end
        state. An ENCLOSING loop's iterator is (`IterHolder`), on whatever
        storage the body retargeted its loans to, joined with the pre-loop
        one: the zero-trip path keeps the latter. A loan whose holder is
        function-scoped but whose STORAGE the body declared is carried out
        unchanged and is NOT diagnosed -- codegen gives that storage a
        per-iteration block scope, so the holder dangles at the closing brace
        (BUGS.md#loop-body-storage-outlived-by-loan).
        """
        merged = merge_borrow_triples(before.borrows, body_end_borrows,
                                      False, False)
        self.ctx.func.borrow_tracker.restore_from_frozen(merged)


def _key_rooted_in(key: object, names: 'frozenset[str]') -> bool:
    """Whether a `HIDDEN_FIELDS` entry is about one of `names`: a fact key
    or name by its root, a storage key by its root name, a consume by its
    parameter, a nested def's mutation mark by its storage, a pair of names
    by either."""
    if isinstance(key, str):
        return fact_key_root(key) in names
    if isinstance(key, StorageKey):
        return key.root in names
    if isinstance(key, OwnSlot):
        return key.param in names
    if isinstance(key, NestedMutationMark):
        return key.key.root in names
    assert isinstance(key, tuple), key
    return any(_key_rooted_in(k, names) for k in key)


# The FlowFacts families whose entries are keyed by a variable's name, which
# a nested scope's binding of that name shadows; the others say nothing
# about one name. Every field is in exactly one list (a unit test checks).
NAME_KEYED_FAMILIES = (
    "definitely_assigned", "loop_bound_assigned", "rvalue_vars",
    "non_null_ptr_vars", "narrowed_types", "consumed_vars",
    "binding_provenance", "value_ranges",
)
UNKEYED_FAMILIES = ("init_terminated", "borrows")

# Every function state field keyed by a variable's name (`_key_rooted_in`),
# which `InitTracker.hidden` masks for the names a scope of its own binds:
# the flow facts, and the tables that say what a name's binding is (a loop
# variable, a closure-written name, a local the deduction tracks, an
# inferred generic argument, a coroutine not yet consumed, a frame a block
# closed, a view's source, a consume, a warned alias pair, an `Own`
# parameter's consume, a nested def's mutation mark). Every field is
# classified in exactly one of the lists below (a unit test checks).
HIDDEN_FIELDS = NAME_KEYED_FAMILIES + (
    "pending_non_null_ptr_vars", "loop_vars", "consumed_loop_vars",
    "loop_var_iter_roots", "closure_written_names", "closure_mutated_paths",
    "closure_mutated_receivers", "variable_to_literal",
    "variable_to_str_var", "variable_to_bytes_var",
    "inferred_generic_locals", "variable_to_generic_instance",
    "unread_coro_locals", "started_coro_locals", "block_closed_frames",
    "str_source_borrows", "bytes_source_borrows", "consume_log",
    "escape_warned_aliases", "literal_locals",
    "current_consumed_own_params", "nested_mutation_marks",
    "view_ids_by_name", "own_consume_aliases",
)
# Keyed by a variable's name and left unmasked, each with why.
UNMASKED_NAME_FIELDS = {
    "borrow_tracker": "BUGS.md#loan-borrower-keyed-by-name",
    "genexpr_source_loans": "BUGS.md#loan-borrower-keyed-by-name",
    # Read only by a `del` in a `finally`, which neither a comprehension nor
    # a lambda body holds.
    "pending_return_borrows": "read at a statement no scope of its own holds",
    # Written at a `break`, a `continue` and a loop body's end.
    "loop_clause_edges": "written at a statement no scope of its own holds",
    # The comprehension scopes' own bindings, each read by its scope's depth.
    "comp_targets": "the scopes' own bindings, resolved by scope depth",
}
# Not keyed by a variable's name: the analysis position, pending work lists,
# node-keyed tables. The test reads what each field's key (a mapping's) or
# element (a collection's) identifies: no name-derived key
# (`NAME_DERIVED_KEYS`), directly or through a record holding one, but for
# the fields in `NAMES_NOT_VARIABLES`, whose `str` is no variable's name.
NOT_NAME_KEYED_FIELDS = (
    "current_scope", "current_function", "body_root",
    "has_protocol_template_param", "current_ns", "loop_depth",
    "compound_stack", "try_except_error_type",
    "try_except_error_handled", "in_except_tier", "in_except_has_binding",
    "in_finally", "in_return_value",
    "pending_resolutions", "pre_analyzed_method_args",
    "pending_elem_type_fields", "pending_composite_exprs",
    "pending_container_exprs", "pending_branch_decl_maps", "arm_decl_sites",
    "pending_generic_instances",
    "pending_str_resolutions",
    "pending_bytes_resolutions", "base_init_calls", "super_del_call",
    "noted_places",
    "copies_from", "settled", "teardown_proof", "tuple_sinks",
    "union_landings", "bind_kinds", "gate_sites",
    "frame_rebind_sites", "init_terminated", "decided_rewrites",
    "pending_elem_cids", "pending_unowned_cids",
    "pending_num_deferred", "after_list_resolution", "awaiting_container",
    "pending_num_splices", "int_tparam_reads", "in_nested_def",
    "nested_def_name", "nested_def_first_literal", "outer_self_is_receiver",
    "nested_def_escapes", "closure_captured_defs",
    "own_ns", "pending_yield_root_checks",
    "pending_view_storage_checks",
    "current_call_edges", "current_self_mutated",
    "current_self_struct_mutated", "current_awaited_subframes",
)
# The annotation spellings of a key derived from a variable's name.
NAME_DERIVED_KEYS = ("str", "StorageKey", "BorrowTracker", "LiteralLocals")
NAMES_NOT_VARIABLES = {
    "try_except_error_type": "an exception type's name",
    "nested_def_name": "a nested def's name",
    "base_init_calls": "a base class's name",
    "pending_elem_type_fields": "an attribute of the node it is keyed by",
    "arm_decl_sites": "the name its declaring statement binds",
    "gate_sites": "the names its statement binds",
}
# Records whose name-like fields name no variable: the test does not look
# inside them.
RECORDS_NOT_NAMING_VARIABLES = {
    "TpyExpr": "a syntax node, by identity",
    "TpyStmt": "a syntax node, by identity",
    "TpyFunction": "a syntax node, by identity",
    "Scope": "a scope, by identity",
    "Namespace": "a scope, by identity",
    "FunctionInfo": "a function's declaration",
    "TpyType": "a type",
    "SourceLocation": "a source position",
}
# Keyed by a variable's name over the whole body, unmasked: TODO.md "Sema
# flow facts keyed by binding, not by spelling".
WHOLE_BODY_NAME_FIELDS = (
    "pinned_view_aliases", "pending_loop_vars", "var_scope_depth",
    "hoisted_vars", "escape_hoisted_vars", "owned_locals",
    "ever_owned_locals", "owned_generator_locals", "stmt_borrow_decls",
    "nonstmt_bound_names", "nonstmt_borrow_bindings", "nonowning_sites",
    "move_through_vars", "current_reassigned_vars", "body_bound_names",
    "local_names", "nested_nonlocal_rebinds", "enclosing_nonlocal_rebinds",
    "deleted_names", "decl_block_depth", "closure_pinned",
    "with_target_names", "frame_local_roots",
    "frame_binding_nodes", "frame_alias_sources", "pass_scoped_frames",
    "current_fresh_ctor_locals",
    "tuple_unpack_view_targets", "current_aug_assigned_vars",
    "current_alias_sources", "current_chain_alias_sources", "head_kills",
    "constant_locals", "unresolved_none_vars", "write_history",
    "authoritative_types", "authoritative_type_lines", "pending_num_names",
    "pending_num_excluded", "pending_num_rebound", "first_bindings",
    "pending_cell_of", "global_declarations", "outer_scope_locals",
    "current_nonlocal_names", "enclosing_declared_names",
    "enclosing_annotation_lines", "enclosing_binding_lines",
    "enclosing_loop_bound_lines", "enclosing_loop_vars",
    "enclosing_borrowing_names", "enclosing_nonvalue_params",
    "nested_def_nodes", "ephemeral_borrow_vars", "consume_causes",
    "nested_free_names",
    "var_decl_by_name", "current_param_names", "current_param_name_to_idx",
    "current_mutated_param_names", "current_nonprotocol_mutated_param_names",
    "current_receiver_deferred_param_names",
    "current_representational_params", "current_rebound_params",
    "current_struct_mutated_param_names",
    "current_elem_mutated_param_names", "current_returned_param_names",
    "current_storage_returned_param_names",
    "current_addr_escape_param_names",
    "current_readonly_addr_escape_param_names",
    "current_foreign_addr_escape_param_names", "owning_reads",
    "in_place_writes",
)


class FlowJoin:
    """The ends of the paths through a construct that joins the way an `if`
    statement's branches do -- a comprehension's iterations, the guards a
    `match` falls through -- and their `FlowFacts.merge`."""

    def __init__(self, init: InitTracker):
        self._init = init
        self._ends: list[FlowFacts] = []

    def leave(self, state: FlowFacts | None = None) -> None:
        """End a path: in `state`, or here."""
        self._ends.append(state if state is not None else self._init.save())

    def state(self) -> FlowFacts:
        """The merge of every path ended so far."""
        assert self._ends
        return reduce(FlowFacts.merge, self._ends)

    def close(self) -> None:
        """Continue after the construct: the merge of every path's end."""
        self._init.restore(self.state())
