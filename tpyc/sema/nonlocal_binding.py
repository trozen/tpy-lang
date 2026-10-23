"""A body that writes variables of the scope it is written in.

A nested def does through `nonlocal`; a generator expression's frame does
for the target of a walrus its enclosing scope also uses (PEP 572 binds it
there, `TpyGeneratorExpression.walrus_used_outside`). Both bodies are
analyzed under a function state of their own, so what a store into such a
name needs to know of the enclosing scope -- which of its names a
declaration types, where each was first bound, which a sibling closure
rebinds -- is read off the enclosing state before the switch and seeded
into the inner one, by the same two calls for both.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..diagnostics import Scope
from ..typesys import (PendingViewType, TpyType, unwrap_readonly,
                       unwrap_ref_type)
from .context import SemanticContext


@dataclass(frozen=True)
class EnclosingScopeFacts:
    """What a store from an inner body reads of the enclosing scope's
    variables (`LocalDeduction.foreign_inferred_slot` and its siblings)."""
    nonlocal_rebinds: frozenset[str]
    declared: frozenset[str]
    annotation_lines: dict[str, int | None]
    binding_lines: dict[str, int]
    outer_locals: frozenset[str]
    self_is_receiver: bool
    # Each name's type in the enclosing scope, for the names the inner body
    # writes through.
    types: dict[str, TpyType]


def enclosing_scope_facts(ctx: SemanticContext,
                          written: set[str]) -> EnclosingScopeFacts:
    """Read the enclosing scope's facts from the live (enclosing) state.
    `written` are the names the inner body stores into; their types are
    read here, where the enclosing scope is still current."""
    fs = ctx.func
    scope = fs.current_scope
    types: dict[str, TpyType] = {}
    for name in written:
        t = scope.lookup(name) if scope is not None else None
        if t is not None:
            types[name] = t
    return EnclosingScopeFacts(
        nonlocal_rebinds=frozenset(fs.nested_nonlocal_rebinds),
        declared=frozenset(set(fs.authoritative_types) | fs.current_param_names),
        annotation_lines={n: fs.authoritative_type_lines.get(n)
                          for n in fs.authoritative_types},
        binding_lines={n: d.loc.line for n, d in fs.var_decl_by_name.items()
                       if d.loc is not None},
        outer_locals=frozenset(fs.definitely_assigned),
        self_is_receiver=ctx.receiver_self_in_scope(),
        types=types)


def enter_enclosing_scope(ctx: SemanticContext,
                          facts: EnclosingScopeFacts) -> None:
    """Seed the inner body's fresh state with the enclosing scope's facts."""
    fs = ctx.func
    fs.enclosing_nonlocal_rebinds = set(facts.nonlocal_rebinds)
    fs.enclosing_declared_names = set(facts.declared)
    fs.enclosing_annotation_lines = dict(facts.annotation_lines)
    fs.enclosing_binding_lines = dict(facts.binding_lines)
    fs.outer_scope_locals = set(facts.outer_locals)
    fs.outer_self_is_receiver = facts.self_is_receiver


def bind_nonlocals(ctx: SemanticContext, facts: EnclosingScopeFacts,
                   scope: Scope, names: set[str], *,
                   declare: bool) -> None:
    """Bind `names` in the inner body's `scope` as the enclosing scope's
    variables, at the type they have there. `declare`: the names are
    nonlocal from the start (a generator expression's implicit ones);
    a nested def's own `nonlocal` statements declare them as they run."""
    fs = ctx.func
    for name in sorted(names):
        outer_type = facts.types.get(name)
        if outer_type is None:
            continue
        pending = unwrap_readonly(unwrap_ref_type(outer_type))
        if isinstance(pending, PendingViewType):
            # A str/bytes variable whose storage the enclosing scope decides:
            # what this body stores into it decides it too.
            ctx.view_var_map(pending.family)[name] = pending.var_id
        scope.define(name, outer_type)
        fs.definitely_assigned.add(name)
        if fs.current_ns:
            fs.current_ns.bind_variable(name, outer_type)
        if declare:
            fs.current_nonlocal_names.add(name)
