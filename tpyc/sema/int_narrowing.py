"""One render for an integer narrowing into a fixed-width type, explicit
(`int8(x)`) or implicit (`x8: int8 = x`): the target's constructor overload
for the source type, or a plain cast where the value range proves the value
fits -- so the two spellings cannot drift, and the elision covers both."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING

from ..parse import TpyExpr, TpyName
from ..type_def_registry import int_traits_of, is_fixed_int_type
from ..typesys import FunctionInfo, IntLiteralType, TpyType
from .overloads import _scalar_widening_cost, type_matches_numeric

if TYPE_CHECKING:
    from .context import SemanticContext
    from .value_range import ValueRange


def select_template_ctor(init_overloads: list[FunctionInfo],
                         arg_types: list[TpyType],
                         default_int_type: TpyType) -> FunctionInfo | None:
    """The `__init__` overload a constructor call of a template-constructed
    type takes: the first that matches, except that among fixed-int
    overloads accepting an int literal the one nearest the default int wins
    (str(5) -> fixed_to_str<int32_t>, not <int8_t>)."""
    best_ctor: FunctionInfo | None = None
    best_cost = 0
    for ctor in init_overloads:
        if len(ctor.params) != len(arg_types):
            continue
        if not all(type_matches_numeric(arg_type, ptype)
                   for (_pname, ptype), arg_type in zip(ctor.params, arg_types)):
            continue
        is_fixed_int = False
        cost = 0
        for (_pname, ptype), arg_type in zip(ctor.params, arg_types):
            if isinstance(arg_type, IntLiteralType) and is_fixed_int_type(ptype):
                is_fixed_int = True
                cost += _scalar_widening_cost(arg_type, ptype, default_int_type)
        if best_ctor is None:
            best_ctor = ctor
            best_cost = cost
            # Only a fixed-int first match can be improved on.
            if not is_fixed_int:
                break
        elif is_fixed_int and cost < best_cost:
            best_ctor = ctor
            best_cost = cost
        if best_cost == 0:
            break
    return best_ctor


def site_value_range(ctx: 'SemanticContext', arg: TpyExpr) -> 'ValueRange | None':
    """The value range of `arg` where it is read now: a name's, else none
    (a field access or sub-expression stays checked). A conversion decided
    later captures it at its own site."""
    return (ctx.func.value_ranges.get(arg.name)
            if isinstance(arg, TpyName) else None)


def narrowing_proven_safe(source: TpyType, target: TpyType,
                          rng: 'ValueRange | None') -> bool:
    """Whether a checked conversion of a value of type `source` into
    `target` can skip its range check: a signed value its range `rng` (at
    the conversion site) proves non-negative into an unsigned type at
    least as wide."""
    target_tr = int_traits_of(target)
    source_tr = int_traits_of(source)
    if target_tr is None or source_tr is None:
        return False
    return (source_tr.signed and not target_tr.signed
            and target_tr.bits >= source_tr.bits
            and rng is not None and rng.is_non_negative())


def proven_cast(ctx: 'SemanticContext', node: TpyExpr, ctor: FunctionInfo,
                source: TpyType, target: TpyType,
                rng: 'ValueRange | None') -> FunctionInfo | None:
    """`ctor` (the checked conversion into `target`) as a plain cast where
    `narrowing_proven_safe` holds, else None; records the line's
    `# tpyc: cast_safe(T)` / `cast_checked(T)` fact at `node`."""
    is_safe = narrowing_proven_safe(source, target, rng)
    loc = getattr(node, "loc", None)
    if loc is not None:
        ctx.cast_safe_facts[(loc.line, str(target))] = is_safe
    if not is_safe:
        return None
    return dc_replace(ctor,
                      cpp_template=f"static_cast<{target.to_cpp()}>({{0}})",
                      canonical_fi=ctor.root)
