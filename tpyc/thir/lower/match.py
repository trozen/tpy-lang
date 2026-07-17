"""Match routing and lowering, with per-tier arm walkers for scalar
switch/chain, record, optional-partition, union, and guarded-union.
"""

from __future__ import annotations
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from ...parse.nodes import (
    TpyAsPattern,
    TpyCapturePattern,
    TpyClassPattern,
    TpyExpr,
    TpyFieldAccess,
    TpyLiteralPattern,
    TpyMatch,
    TpyName,
    TpyOrPattern,
    TpyValuePattern,
    TpyWildcardPattern,
)
from ...typesys import (
    LiteralType,
    NominalType,
    NoneType,
    OptionalType,
    TpyType,
    UnionType,
    unwrap_readonly,
    unwrap_ref_type,
)
from ...type_def_registry import is_bool_type, is_fixed_int_type
from ...codegen_cpp.forms import is_ptr_variant_union
from ...codegen_cpp.context import cpp_string_literal_expr
from ...codegen_cpp.match import MatchGenerator, partition_optional_cases
from ...codegen_cpp.string_dispatch import (
    STRING_SWITCH_THRESHOLD,
    case_label,
    discriminator_key,
    find_best_discriminator,
)
from ...liveness import stmts_terminate
from ..faces import witness as _witness
from ..fallback import ThirUnsupported
from ..nodes import (
    THIRExpr,
    THIRMatch,
    THIRMatchArm,
    THIRMatchArmEntry,
    THIRMatchBinding,
)
from .predicates import (
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _enum_member_cpp,
    _f1_record,
    _optional_ptr_borrow_name,
    _resolved_bytes_value,
    _resolved_str_value,
    _value_opt_scalar,
)
from .context import (
    _ExprResultUse,
    _ExprUse,
    _LowerCtx,
    _Prescan,
)
from .expressions import (
    _lower_expr,
)
from . import statements as _statements

def _match_strategy(stmt: TpyMatch, analyzer) -> 'str | None':
    """The routed-tier discriminant -- the SINGLE complete mirror of
    _gen_match_dispatch's scalar routing, consumed by lowering. Keeping one
    classifier avoids drift in guard promotion; the byte-diff is the
    backstop. A registered enum
    subject -> switch_enum, a plain fixed-int subject -> switch_primitive,
    and the remaining value scalars (bool -- -Wswitch-bool keeps it off
    the switch -- BigInt, floats) plus resolved-str subjects -> the `==`
    chain, if_elif or (any guard) if_elif_guarded -- EXCEPT a str subject
    at or above the switch-dispatch threshold, which routes to the
    discriminator switch REGARDLESS of guards (switch_str; the
    threshold counts only unguarded literal alternatives). A `Literal[...]`
    subject also dispatches on the AST path, but sema narrows the subject
    to a per-arm LiteralType (`case.type_facts`) whose dead-branch
    elimination can rewrite arm bodies that re-read the subject --
    rejected until THIR expression lowering learns the literal-fact fold.
    Union guard/shared-index/field-condition routing is
    `_match_union_route`; a user-record subject takes the record tiers
    (if_elif_record, or guarded_record when any arm has a guard) --
    polymorphic dispatch is checked before strategy selection, mirroring
    `_gen_match_dispatch`'s early poly branch. An Optional subject routes
    on the shared partition fact (optional_partition vs the chain-optional
    tiers); polymorphic subjects are their own tier
    (deferred)."""
    if stmt.subject_type is None:
        return None
    t = unwrap_readonly(stmt.subject_type)
    if isinstance(t, LiteralType):
        return None
    if isinstance(t, UnionType):
        # Recursive-alias wrappers (bare `Tree` unions, `Tree[Int32]`
        # instances -- the `.value` indirection) are PARKED against the
        # wrapper-form rung: wrapper-typed params/locals are themselves
        # unsupported at the callable boundary, so no wrapper match can route
        # until that form lands.
        if t.needs_wrapper():
            return None
        return "switch_union"
    if _eligible_enum(t, analyzer) is not None:
        return "switch_enum"
    if is_fixed_int_type(t):
        return "switch_primitive"
    if isinstance(t, OptionalType):
        # The AST dispatches on the SHARED partition fact: a None-arm prefix
        # takes `_gen_match_optimized_optional` (the `__match_inner_N`
        # second name); anything else is the if/elif-optional tier --
        # `_gen_match_if_elif_optional`'s plain chain, or (any guard) its
        # standalone-if + goto sibling. The selected lowerer rejects
        # unsupported arm shapes as it builds the partition / the chain.
        if partition_optional_cases(stmt.cases) is not None:
            return "optional_partition"
        if any(c.guard is not None for c in stmt.cases):
            return "if_elif_optional_guarded"
        return "if_elif_optional"
    if _eligible_scalar(t) or _resolved_str_value(t, analyzer) is not None:
        if _match_str_switches(stmt, analyzer):
            return "switch_str"
        if any(c.guard is not None for c in stmt.cases):
            return "if_elif_guarded"
        return "if_elif"
    if isinstance(t, NominalType) and t.is_user_record:
        if any(c.guard is not None for c in stmt.cases):
            return "guarded_record"
        return "if_elif_record"
    return None

def _union_index_members(u: UnionType) -> 'tuple | None':
    """The member ordering `_variant_index` scans: the wrapper's full
    member tuple when one exists (narrowed recursive subsets index against
    the full space), else the union's own members."""
    wrapper = u.wrapper_info()
    return wrapper.full_members if wrapper is not None else u.members

def _union_member_index(members, member) -> 'int | None':
    """`_variant_index`'s linear scan, None instead of raising."""
    for i, m in enumerate(members):
        if m == member:
            return i
    return None

def _match_str_switches(stmt: TpyMatch, analyzer) -> bool:
    """Mirrors _should_switch_str: a str subject with enough unguarded
    str-literal alternatives takes the discriminator-switch strategy
    (switch_str), not the if/elif chain."""
    t = unwrap_readonly(stmt.subject_type)
    if _resolved_str_value(t, analyzer) is None:
        return False
    count = 0
    for case in stmt.cases:
        if case.guard is not None:
            continue
        pat = case.pattern
        if isinstance(pat, TpyLiteralPattern) and isinstance(pat.value, str):
            count += 1
        elif isinstance(pat, TpyOrPattern):
            if all(isinstance(a, TpyLiteralPattern)
                   and isinstance(a.value, str) for a in pat.patterns):
                count += len(pat.patterns)
    return count >= STRING_SWITCH_THRESHOLD

def _match_arm_parts(case) -> 'tuple | None':
    """Split an arm into (test_pattern, binding_node): the label-generating
    pattern (None for an always-matching wildcard/capture arm -- the switch
    `default:` / the chain's final `} else {`) and the single whole-subject
    binding node (`TpyCapturePattern` / `TpyAsPattern`), or None when the
    shape is out of the slice (a capture or nested `as` under an `as` --
    two bindings; `_binding_pairs` emits both on the AST path)."""
    p = case.pattern
    if isinstance(p, TpyAsPattern):
        if isinstance(p.pattern, (TpyAsPattern, TpyCapturePattern)):
            return None
        return p.pattern, p
    if isinstance(p, TpyCapturePattern):
        return None, p
    if isinstance(p, TpyWildcardPattern):
        return None, None
    return p, None

def _match_label_ok(pattern, kind: str) -> bool:
    """A pattern the tier renders inline: an enum-member value pattern (the
    ENUM render, `_enum_member_cpp`), an int literal for the primitive
    switch (`_switch_literal_label`'s spelling), or any renderable literal
    for the if/elif `==` chain (`_gen_literal_cond`'s four arms).
    Captures/wildcards inside an or-pattern route the whole arm to
    `default:` on the AST switch path -- reject."""
    if kind == "switch_enum":
        return (isinstance(pattern, TpyValuePattern)
                and isinstance(pattern.expr, TpyFieldAccess)
                and pattern.expr.enum_member_of is not None)
    if kind == "switch_primitive":
        return (isinstance(pattern, TpyLiteralPattern)
                and isinstance(pattern.value, int))
    return (isinstance(pattern, TpyLiteralPattern)
            and isinstance(pattern.value, (bool, int, float, str)))

def _match_record_field_type(pattern: TpyClassPattern, field_name: str,
                             analyzer) -> 'TpyType | None':
    """`_record_field_type`'s registry walk (inherited fields included). The
    AST skips generic type-param substitution there; the slice admits only
    non-generic patterns (`_f1_record`), so the raw field type is exact."""
    rt = pattern.resolved_type
    if not isinstance(rt, NominalType):
        return None
    record = analyzer.registry.get_record_for_type(rt)
    if record is None:
        return None
    for f in analyzer.registry.get_all_fields(record):
        if f.name == field_name:
            return f.type
    return None

def _match_field_cond(pattern: TpyClassPattern, field_name: str, value,
                      analyzer) -> 'tuple[str, str] | None':
    """One literal field condition as a (prefix, suffix) pair around the
    runtime base spelling (the record tiers' `__match_subject_N`, the
    guarded-union tier's `__case_{idx}` -- both drawn at emit) -- the exact
    render of `_record_field_conditions`' literal arms. None for a value
    outside the rendered set (or a `=None` on a field whose repr the AST
    would refuse -- sema's nullable check makes that unreachable)."""
    if value is None:
        ft = _match_record_field_type(pattern, field_name, analyzer)
        if isinstance(ft, OptionalType):
            return ("!", f".{field_name}.has_value()")
        if isinstance(ft, UnionType):
            return ("std::holds_alternative<std::monostate>(",
                    f".{field_name})")
        return None
    if isinstance(value, bool):
        return ("", f".{field_name} == {'true' if value else 'false'}")
    if isinstance(value, int):
        return ("", f".{field_name} == {value}")
    if isinstance(value, float):
        return ("", f".{field_name} == {value!r}")
    if isinstance(value, str):
        return ("", f".{field_name} == {cpp_string_literal_expr(value)}")
    return None

def _match_capture_field_ok(ft: TpyType, analyzer) -> bool:
    """A field capture joins the arm walk as a plain declared local, so its
    type must be one the slice's name-read classification already covers:
    value scalars, Char, registered enums, resolved str."""
    t = unwrap_readonly(ft)
    return (_eligible_scalar(t) or _eligible_char(t)
            or _eligible_enum(t, analyzer) is not None
            or _resolved_str_value(t, analyzer) is not None)

def _match_keywords_ok(
        pattern: TpyClassPattern, analyzer, pointers: AbstractSet[str],
        narrowed: AbstractSet[str], storage_tuple_locals: AbstractSet[str],
        arm_declared: dict[str, TpyType], *, allow_conds: bool) -> bool:
    """The field sub-pattern slice for one class pattern: literal conditions
    (the record tiers and the guarded-union tier; the unconditional union
    switch has no `&&` position, so its walk passes allow_conds=False --
    defensive, `_match_union_route` already sends conditions to the guarded
    path), free-value captures (declared into the arm scope; the same
    pointer/narrowed/tuple-alias name rejects as a whole-subject binding),
    and wildcards. Class/`as` sub-patterns (union field guards, nested
    records, type guards) are deferred rows. Keyword-bearing patterns
    require the F1 record for the registry field-type walk (and keep
    @native field renames out -- the AST spells the raw Python name)."""
    if pattern.keywords and not _f1_record(pattern.resolved_type, analyzer):
        return False
    for fname, sub in pattern.keywords:
        if isinstance(sub, TpyWildcardPattern):
            continue
        if isinstance(sub, TpyLiteralPattern):
            if not allow_conds:
                return False
            if _match_field_cond(pattern, fname, sub.value, analyzer) is None:
                return False
            continue
        if isinstance(sub, TpyCapturePattern):
            if (sub.name in pointers or sub.name in narrowed
                    or sub.name in storage_tuple_locals):
                return False
            ft = _match_record_field_type(pattern, fname, analyzer)
            if ft is None or not _match_capture_field_ok(ft, analyzer):
                return False
            arm_declared[sub.name] = ft
            continue
        return False
    return True

def _match_pattern_captures(pattern: TpyClassPattern) -> 'list[str]':
    """The keyword capture names of one class pattern (slice shapes only)."""
    return [sub.name for _, sub in pattern.keywords
            if isinstance(sub, TpyCapturePattern)]

def _match_union_route(stmt: TpyMatch, u: UnionType) -> str:
    """_gen_match_dispatch's union routing: any guard, two arms landing on
    one variant index (`_has_shared_variant_index` -- class and or-class
    alternatives only), or a field-value sub-pattern (the unconditional
    switch has no `&&` position for its check -- the AST's own
    `_pattern_has_field_condition` is reused so the two routings cannot
    drift) takes the guarded path."""
    if any(c.guard is not None for c in stmt.cases):
        return "guarded_union"
    members = _union_index_members(u)
    seen: set[int] = set()
    for case in stmt.cases:
        pat = case.pattern
        if isinstance(pat, TpyAsPattern):
            pat = pat.pattern
        indices: list[int] = []
        if isinstance(pat, TpyClassPattern) and pat.resolved_type is not None:
            idx = _union_member_index(members, pat.resolved_type)
            if idx is not None:
                indices.append(idx)
        elif isinstance(pat, TpyOrPattern):
            for alt in pat.patterns:
                if (isinstance(alt, TpyClassPattern)
                        and alt.resolved_type is not None):
                    idx = _union_member_index(members, alt.resolved_type)
                    if idx is not None:
                        indices.append(idx)
        for idx in indices:
            if idx in seen:
                return "guarded_union"
            seen.add(idx)
    if any(MatchGenerator._pattern_has_field_condition(c.pattern)
           for c in stmt.cases):
        return "guarded_union"
    return "switch_union"

def _guarded_union_arm_ok(
        case, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], subj: TpyName,
        members: tuple) -> bool:
    """Admit one guarded-union arm while its lowering group is built."""
    parts = _match_arm_parts(case)
    if parts is None:
        return False
    test, bnode = parts
    arm_declared = dict(declared)
    member = None
    if test is None:
        pass
    elif isinstance(test, TpyClassPattern):
        member = test.resolved_type
        if member is None or test.positional:
            return False
        if _union_member_index(members, member) is None:
            return False
        if not _match_keywords_ok(
                test, analyzer, pointers, narrowed,
                storage_tuple_locals, arm_declared, allow_conds=True):
            return False
    elif isinstance(test, TpyOrPattern):
        if bnode is not None:
            return False
        for alt in test.patterns:
            if not (isinstance(alt, TpyClassPattern)
                    and alt.resolved_type is not None
                    and not alt.positional):
                return False
            # Keyword-bearing alternatives distribute to their index group
            # like plain class arms; lower_entry handles conds/captures.
            if alt.keywords and not _match_keywords_ok(
                    alt, analyzer, pointers, narrowed,
                    storage_tuple_locals, dict(arm_declared),
                    allow_conds=True):
                return False
            if _union_member_index(members, alt.resolved_type) is None:
                return False
    elif isinstance(test, TpyLiteralPattern) and test.value is None:
        if _union_member_index(members, NoneType()) is None:
            return False
    else:
        return False
    facts = case.type_facts or {}
    if facts:
        if set(facts) != {subj.name} or member is None:
            return False
        arm_declared[subj.name] = facts[subj.name]
    if bnode is not None:
        if (bnode.name in pointers or bnode.name in narrowed
                or bnode.name in storage_tuple_locals):
            return False
        arm_declared[bnode.name] = (member if member is not None
                                    else arm_declared[subj.name])
    return True

def _union_arm_ok(
        case, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], subj: TpyName,
        members: tuple, seen: set[int]) -> bool:
    """Admit one switch-union arm while that arm is lowered."""
    if case.guard is not None:
        return False
    parts = _match_arm_parts(case)
    if parts is None:
        return False
    test, bnode = parts
    arm_declared = dict(declared)
    member = None
    if test is None:
        pass
    elif isinstance(test, TpyClassPattern):
        member = test.resolved_type
        if member is None or test.positional:
            return False
        idx = _union_member_index(members, member)
        if idx is None or idx in seen:
            return False
        seen.add(idx)
        if not _match_keywords_ok(
                test, analyzer, pointers, narrowed,
                storage_tuple_locals, arm_declared, allow_conds=False):
            return False
    elif isinstance(test, TpyOrPattern):
        if bnode is not None:
            return False
        for alt in test.patterns:
            if not (isinstance(alt, TpyClassPattern)
                    and alt.resolved_type is not None
                    and not alt.positional):
                return False
            # Capture keywords admit (the binding or-arm duplicates its body
            # per alternative); literal conditions were already routed to the
            # guarded path by _match_union_route.
            if alt.keywords and not _match_keywords_ok(
                    alt, analyzer, pointers, narrowed,
                    storage_tuple_locals, dict(arm_declared),
                    allow_conds=False):
                return False
            idx = _union_member_index(members, alt.resolved_type)
            if idx is None or idx in seen:
                return False
            seen.add(idx)
    elif isinstance(test, TpyLiteralPattern) and test.value is None:
        idx = _union_member_index(members, NoneType())
        if idx is None or idx in seen:
            return False
        seen.add(idx)
    else:
        return False
    facts = case.type_facts or {}
    if facts:
        if set(facts) != {subj.name} or member is None:
            return False
        arm_declared[subj.name] = facts[subj.name]
    if bnode is not None:
        if (bnode.name in pointers or bnode.name in narrowed
                or bnode.name in storage_tuple_locals):
            return False
        arm_declared[bnode.name] = (member if member is not None
                                    else arm_declared[subj.name])
    return True

def _match_record_arm_always(test) -> bool:
    """Whether an arm matches unconditionally on the record tiers: a
    wildcard/capture (test None), a class pattern with no literal field
    sub-patterns, or an or-pattern whose rendered condition list collapses
    empty (a wildcard alternative clears it; condition-free class
    alternatives contribute nothing)."""
    if test is None:
        return True
    if isinstance(test, TpyClassPattern):
        return not any(isinstance(s, TpyLiteralPattern)
                       for _, s in test.keywords)
    if any(isinstance(a, TpyWildcardPattern) for a in test.patterns):
        return True
    return not any(isinstance(s, TpyLiteralPattern)
                   for a in test.patterns
                   if isinstance(a, TpyClassPattern)
                   for _, s in a.keywords)

def _record_arm_ok(
        case, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], subj: TpyName) -> bool:
    """Admit one record arm while that arm is lowered."""
    if case.type_facts:
        return False
    parts = _match_arm_parts(case)
    if parts is None:
        return False
    test, bnode = parts
    arm_declared = dict(declared)
    if test is None:
        pass
    elif isinstance(test, TpyClassPattern):
        if test.resolved_type is None or test.positional:
            return False
        if not _match_keywords_ok(
                test, analyzer, pointers, narrowed,
                storage_tuple_locals, arm_declared, allow_conds=True):
            return False
    elif isinstance(test, TpyOrPattern):
        if bnode is not None:
            return False
        for alt in test.patterns:
            if isinstance(alt, TpyWildcardPattern):
                continue
            if (not isinstance(alt, TpyClassPattern)
                    or alt.resolved_type is None or alt.positional):
                return False
            if alt.keywords and not _f1_record(alt.resolved_type, analyzer):
                return False
            for fname, sub in alt.keywords:
                if isinstance(sub, TpyWildcardPattern):
                    continue
                if not isinstance(sub, TpyLiteralPattern):
                    return False
                if _match_field_cond(alt, fname, sub.value, analyzer) is None:
                    return False
    else:
        return False
    if bnode is not None:
        if (bnode.name in pointers or bnode.name in narrowed
                or bnode.name in storage_tuple_locals):
            return False
        arm_declared[bnode.name] = arm_declared[subj.name]
    return True

@dataclass(frozen=True)
class _MatchRoute:
    kind: str
    hoist_types: tuple[tuple[str, TpyType], ...]
    union_route: 'str | None' = None


def _match_route(
        stmt: TpyMatch, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], prescan: _Prescan,
        *, in_branch: bool,
                     in_loop: bool) -> "_MatchRoute | None":
    """Return the strategy data consumed while lowering a match, or None.

    M1 covers the unguarded scalar switch tiers
    (switch_enum / switch_primitive), no captures. The subject is a bare
    declared name (an lvalue -- `auto&`; pointer-local / narrowed /
    tuple-alias names render indirect and reject). Arms are enum-member /
    int-literal patterns, or-patterns of those, and the wildcard; guards,
    `as`/capture bindings, and arms carrying narrowing `type_facts` (never
    set for plain enum/fixed-int subjects -- defensive) reject to the AST
    path, which keeps the guarded tiers' second counter draw
    (`__match_end_N`) and the bind-mode machinery unreachable. The sema
    hoist (`if_branch_decls`) follows the try arm's discipline exactly:
    already-declared names skip, fresh plain-value names admit in
    straight-line function scope only, and every arm body walks with them
    in scope."""
    if stmt.polymorphic_dispatch:
        return None
    kind = _match_strategy(stmt, analyzer)
    if kind is None:
        return None
    subj = stmt.subject
    if not isinstance(subj, TpyName) or subj.name not in declared:
        return None
    if (subj.name in pointers or subj.name in narrowed
            or subj.name in storage_tuple_locals):
        return None
    hoist_declared: dict[str, TpyType] = {}
    for name, raw in analyzer.if_branch_decls.get(id(stmt), {}).items():
        if name in declared:
            continue
        if name in prescan.native_globals:
            return None
        if in_branch or in_loop:
            return None
        vtype = unwrap_ref_type(raw)
        if not _statements._try_hoist_type_ok(vtype, analyzer):
            return None
        hoist_declared[name] = vtype
    hoist_types = tuple(hoist_declared.items())
    if kind == "switch_union":
        u = unwrap_readonly(stmt.subject_type)
        union_route = _match_union_route(stmt, u)
        return _MatchRoute(kind=kind, hoist_types=hoist_types,
                          union_route=union_route)
    if kind in ("if_elif_record", "guarded_record"):
        if not _f1_record(unwrap_readonly(stmt.subject_type), analyzer):
            return None
        return _MatchRoute(kind=kind, hoist_types=hoist_types)
    if kind in ("optional_partition", "if_elif_optional",
                "if_elif_optional_guarded"):
        # Pointer-repr subjects must be the admitted borrow form (the tiers'
        # null tests read the `T*` binding; a storage-form source's
        # optional_to_ptr lift is not mirrored); value-repr subjects take
        # the O2 multi-arm dispatch or the chain, whose inner-shape rejects
        # live in the lowerer.
        if (unwrap_readonly(stmt.subject_type).uses_pointer_repr()
                and _optional_ptr_borrow_name(subj, declared, analyzer)
                is None):
            return None
        return _MatchRoute(kind=kind, hoist_types=hoist_types)
    return _MatchRoute(kind=kind, hoist_types=hoist_types)

def _select_match_route(
        stmt: TpyMatch, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], prescan: _Prescan, *,
        in_branch: bool, in_loop: bool) -> _MatchRoute:
    """Select a match lowering strategy or reject at the lowering boundary."""
    route = _match_route(
        stmt, analyzer, declared, pointers, narrowed,
        storage_tuple_locals, prescan,
        in_branch=in_branch, in_loop=in_loop)
    if route is None:
        raise ThirUnsupported("stmt.match")
    return route

def _match_case_label(pattern, kind: str, analyzer) -> str:
    """One pre-rendered arm spelling. Switch tiers: the AST's
    `gen_expr(pattern.expr)` for an enum member (`_enum_member_cpp` is that
    render's ENUM arm) or `_switch_literal_label`'s bare spelling for an
    int/bool literal. The if/elif tier: `_gen_literal_cond`'s RHS -- the
    emit composes `{subject} == {rhs}` per alternative."""
    if kind == "switch_enum":
        return _enum_member_cpp(pattern.expr, analyzer)
    val = pattern.value
    if isinstance(val, bool):
        return "true" if val else "false"
    if kind != "switch_primitive":
        if isinstance(val, float):
            return repr(val)
        if isinstance(val, str):
            return cpp_string_literal_expr(val)
    return str(val)


def _lower_match_guard(guard: TpyExpr, lc: _LowerCtx,
                       declared: dict[str, TpyType]) -> THIRExpr:
    lowered = _lower_expr(
        guard, lc, declared,
        use=_ExprUse(result=_ExprResultUse.CONDITION))
    if not is_bool_type(unwrap_readonly(lowered.result_type)):
        raise ThirUnsupported("match.guard_type", detail=True)
    return lowered


def _lower_match(stmt: TpyMatch, route: _MatchRoute, lc: _LowerCtx,
                 declared: dict[str, TpyType], pointers: AbstractSet[str], loc, *,
                 loop_depth: int = 0,
                 arm_body_hooks: bool = False) -> THIRMatch:
    """Lower a scalar-tier `match` (see `THIRMatch` for the emit shapes).
    Labels/condition-RHS pre-render here. Switch tiers regroup the wildcard
    arm LAST regardless of source position (`_group_switch_arms` appends
    default_entries after the label groups); the if/elif tier keeps source
    order (its wildcard is the final `} else {` -- checked while lowering). Arm
    bodies lower under narrowing-scope snapshots like every branch body
    (the AST's `_emit_case_body` narrowed_vars/alias restores); the hoisted
    predecls follow the try arm exactly and enter the caller's `declared`.
    `emit_unreachable` folds the AST's `_emit_match_unreachable_tail`
    condition at lowering; `synthetic_default` its `default: break;` rule
    (switch tiers only -- the if/elif chain has no default)."""
    kind = route.kind
    if arm_body_hooks and (kind not in (
            "switch_enum", "switch_primitive", "if_elif", "if_elif_guarded")
            or route.hoist_types):
        # Dispatch-hook mode (a resumable MatchDispatch): only the scalar
        # tiers' arm-body points carry the skeleton hook; union / record /
        # optional tiers and hoisted arm decls (frame-field territory in a
        # resumable) stay their own rungs.
        raise ThirUnsupported("res.match_strategy")
    if kind != "switch_union":  # the union lowerers witness their route
        _witness(f"match.{kind}")
    predeclared = set(declared)
    hoist_decls: list[tuple[str, str]] = []
    for name, vtype in route.hoist_types:
        render_src = (_resolved_str_value(vtype, lc.analyzer)
                      or _resolved_bytes_value(vtype, lc.analyzer)
                      or vtype)
        hoist_decls.append((name, lc.render_type(render_src)))
        declared[name] = vtype
    if hoist_decls:
        _witness("match.hoist_decl")
    if kind == "switch_union":
        if route.union_route == "guarded_union":
            return _lower_match_guarded_union(stmt, lc, declared, loc,
                                              pointers, hoist_decls,
                                              loop_depth=loop_depth)
        return _lower_match_union(stmt, lc, declared, loc, pointers,
                                  hoist_decls,
                                  loop_depth=loop_depth)
    if kind in ("if_elif_record", "guarded_record"):
        return _lower_match_record(stmt, lc, declared, loc, pointers,
                                   hoist_decls, kind, loop_depth=loop_depth)
    if kind == "optional_partition":
        return _lower_match_optional(stmt, lc, declared, loc, pointers,
                                     predeclared, hoist_decls,
                                     loop_depth=loop_depth)
    if kind in ("if_elif_optional", "if_elif_optional_guarded"):
        return _lower_match_optional_chain(stmt, lc, declared, loc, pointers,
                                           hoist_decls, kind,
                                           loop_depth=loop_depth)
    if kind == "switch_str":
        return _lower_match_switch_str(stmt, lc, declared, loc, pointers,
                                       hoist_decls, loop_depth=loop_depth)
    subj_type = declared.get(stmt.subject.name)
    arms, default_goto, has_defaults = _lower_scalar_arms(
        stmt.cases, kind, lc, declared, pointers, subj_type,
        allow_facts=False, chain_guards_ok=True, bind_from_case_var=False,
        loop_depth=loop_depth, arm_body_hooks=arm_body_hooks)
    is_chain = kind in ("if_elif", "if_elif_guarded")
    synthetic_default = (not is_chain and not has_defaults
                         and not stmt.is_exhaustive)
    if synthetic_default:
        _witness("match.synthetic_default")
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy=kind,
        subject=_lower_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=synthetic_default,
        default_goto=default_goto,
        loc=loc,
    )

def _lower_scalar_arms(
        cases, kind: str, lc: _LowerCtx, declared: dict[str, TpyType],
        pointers: AbstractSet[str], bind_type: 'TpyType | None', *,
        allow_facts: bool, chain_guards_ok: bool, bind_from_case_var: bool,
        loop_depth: int = 0,
        arm_body_hooks: bool = False,
        ) -> 'tuple[list[THIRMatchArm], bool, bool]':
    """The scalar-tier arm walk shared by the top-level switch/chain tiers
    and the O2 optional inner dispatch: label rendering (or-patterns as
    stacked labels), whole-subject bindings, guards, switch-group
    regrouping (wildcard default last, guarded same-label merges,
    `default_goto` when a user default backs an all-guarded group), and the
    chain's source-order/wildcard-last rules. Returns (arms, default_goto,
    has_default_entries); the callers compose `synthetic_default` (the
    top-level tiers key it on exhaustiveness, the optional inner dispatch
    does not -- the AST's inner `_emit_switch_groups` call never passes
    is_exhaustive). `allow_facts` retypes the declared view per arm
    (optional inner: the subject narrows to the inner type; the value
    unwrap is the name arm's deref-on-narrow); the top-level tiers reject
    facts (a LiteralType fact can rewrite arm bodies). `chain_guards_ok`
    distinguishes if_elif_guarded's standalone-if chain from the optional
    inner chain, whose inline `cond && guard` shape is not mirrored."""
    is_chain = kind in ("if_elif", "if_elif_guarded")
    arms: list[THIRMatchArm] = []
    groups: dict[str, tuple[tuple[str, ...], list[THIRMatchArmEntry]]] = {}
    group_order: list[str] = []
    default_entries: list[THIRMatchArmEntry] = []
    always_match_arms = 0
    for i, case in enumerate(cases):
        facts = case.type_facts or {}
        if facts and not allow_facts:
            raise ThirUnsupported("stmt.match")
        if is_chain and case.guard is not None and not chain_guards_ok:
            raise ThirUnsupported("stmt.match")
        parts = _match_arm_parts(case)
        if parts is None:
            raise ThirUnsupported("stmt.match")
        test, bnode = parts
        if test is None or isinstance(test, TpyWildcardPattern):
            # A wildcard under `as` (`case _ as y:`) is the always arm with
            # a whole-subject binding -- `_unwrap_as_pattern` + the wildcard
            # arm on the AST path.
            always_match_arms += 1
            if kind == "if_elif" and (
                    always_match_arms > 1 or i != len(cases) - 1):
                raise ThirUnsupported("stmt.match")
            labels: tuple[str, ...] = ()
        elif isinstance(test, TpyOrPattern):
            if not all(_match_label_ok(alt, kind) for alt in test.patterns):
                raise ThirUnsupported("stmt.match")
            _witness("match.or_labels")
            labels = tuple(_match_case_label(alt, kind, lc.analyzer)
                           for alt in test.patterns)
        else:
            if not _match_label_ok(test, kind):
                raise ThirUnsupported("stmt.match")
            labels = (_match_case_label(test, kind, lc.analyzer),)
        if not is_chain:
            prior = (default_entries if not labels
                     else groups.get("|".join(labels), ((), []))[1])
            if prior and prior[-1].guard is None:
                raise ThirUnsupported("stmt.match")
        binding = None
        arm_declared = dict(declared)
        for name, fact in facts.items():
            arm_declared[name] = fact
        if bnode is not None:
            if (bnode.name in pointers or bnode.name in lc.narrow.narrowed
                    or bnode.name in lc.storage_tuple_locals):
                raise ThirUnsupported("stmt.match")
            mode = ("assign" if bnode.name in declared
                    else "copy" if bnode.bind_by_value else "ref")
            _witness(f"match.bind_{mode}")
            binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                       from_case_var=bind_from_case_var)
            arm_declared[bnode.name] = bind_type
        guard = None
        if case.guard is not None:
            _witness("match.guard_arm")
            guard = _lower_match_guard(case.guard, lc, arm_declared)
        if arm_body_hooks:
            # Dispatch-hook mode: the arm body is a BB chain the skeleton
            # walks (already seam-routed leaves); a whole-subject binding
            # would be a frame-field write, not gen_match's local decl --
            # its rung needs that duality mirrored.
            if binding is not None:
                raise ThirUnsupported("res.match_binding")
            entry = THIRMatchArmEntry(
                body=(), loc=case.loc, binding=None, guard=guard,
                body_key=id(case.body))
        else:
            entry = THIRMatchArmEntry(
                body=_statements._lower_scoped_stmts(
                    case.body, lc, arm_declared, loop_depth=loop_depth),
                loc=case.loc, binding=binding, guard=guard)
        if is_chain:
            # Source order, one entry per group (the always-match arm is
            # the plain chain's final `} else {` / a guarded standalone
            # block).
            if not labels and kind == "if_elif":
                _witness("match.if_elif_else")
            arms.append(THIRMatchArm(labels=labels, entries=(entry,)))
        elif not labels:
            _witness("match.wildcard_default")
            default_entries.append(entry)
        else:
            # _group_switch_arms: same-label cases (only guarded repeats
            # pass sema) merge into one group, first-appearance order.
            key = "|".join(labels)
            if key in groups:
                groups[key][1].append(entry)
            else:
                groups[key] = (labels, [entry])
                group_order.append(key)
    default_goto = False
    if not is_chain:
        for key in group_order:
            glabels, entries = groups[key]
            arms.append(THIRMatchArm(labels=glabels, entries=tuple(entries)))
        if any(len(a.entries) > 1 or a.entries[0].guard is not None
               for a in arms):
            _witness("match.switch_guard_chain")
        # _emit_switch_groups' needs_default_goto: a user default exists
        # and some labeled group is entirely guarded.
        default_goto = bool(default_entries) and any(
            all(e.guard is not None for e in a.entries) for a in arms)
        if default_goto:
            _witness("match.default_goto")
        if default_entries:
            arms.append(THIRMatchArm(labels=(),
                                     entries=tuple(default_entries)))
    return arms, default_goto, bool(default_entries)

def _lower_field_subpatterns(pattern: TpyClassPattern,
                             declared: dict[str, TpyType],
                             arm_declared: dict[str, TpyType],
                             lc: _LowerCtx, *, from_case_var: bool,
                             ) -> 'tuple[tuple, tuple]':
    """One class pattern's keyword sub-patterns -> (field_conds,
    field_bindings), in keyword order like `_record_field_conditions` /
    `_gen_match_field_bindings`. Captures join the arm scope typed to the
    field (the AST's `_record_capture_type` var_types registration)."""
    field_conds: list[tuple[str, str]] = []
    field_bindings: list[THIRMatchBinding] = []
    for fname, sub in pattern.keywords:
        if isinstance(sub, TpyLiteralPattern):
            pair = _match_field_cond(pattern, fname, sub.value, lc.analyzer)
            assert pair is not None, "ineligible field cond reached lowering"
            _witness("match.field_none" if sub.value is None
                     else "match.field_cond")
            field_conds.append(pair)
        elif isinstance(sub, TpyCapturePattern):
            mode = ("assign" if sub.name in declared
                    else "copy" if sub.bind_by_value else "ref")
            _witness("match.field_bind")
            field_bindings.append(THIRMatchBinding(
                name=sub.name, mode=mode, from_case_var=from_case_var,
                subject_suffix=f".{fname}"))
            ft = _match_record_field_type(pattern, fname, lc.analyzer)
            assert ft is not None, "ineligible field capture reached lowering"
            arm_declared[sub.name] = ft
        else:
            assert isinstance(sub, TpyWildcardPattern), \
                "ineligible field sub-pattern reached lowering"
    return tuple(field_conds), tuple(field_bindings)

def _lower_or_field_conds(test: TpyOrPattern, lc: _LowerCtx,
                          ) -> 'tuple[tuple, ...]':
    """An or-pattern record arm's alternative condition groups -- the
    `or_parts` build of the AST or-branches: a wildcard alternative clears
    everything (always-match), a condition-free class alternative
    contributes nothing (the AST's `if alt_conds:` skip -- mirrored, not
    endorsed: a conditional sibling alternative then wrongly constrains
    the arm on both paths)."""
    _witness("match.record_or")
    groups: list[tuple] = []
    for alt in test.patterns:
        if isinstance(alt, TpyWildcardPattern):
            return ()
        alt_conds = []
        for fname, sub in alt.keywords:
            if isinstance(sub, TpyLiteralPattern):
                pair = _match_field_cond(alt, fname, sub.value, lc.analyzer)
                assert pair is not None, \
                    "ineligible or-alt cond reached lowering"
                _witness("match.field_none" if sub.value is None
                         else "match.field_cond")
                alt_conds.append(pair)
        if alt_conds:
            groups.append(tuple(alt_conds))
    return tuple(groups)

def _lower_match_record(stmt: TpyMatch, lc: _LowerCtx,
                        declared: dict[str, TpyType], loc,
                        pointers: AbstractSet[str],
                        hoist_decls: 'list[tuple[str, str]]',
                        kind: str, *, loop_depth: int = 0) -> THIRMatch:
    """Lower a record-tier `match` (if_elif_record / guarded_record):
    source-order single-entry arms; per class arm the pre-rendered literal
    field conditions (`&&`-joined at emit around `__match_subject_N`) and
    keyword capture bindings, then the whole-subject `as`/capture binding;
    or-pattern arms carry condition groups only. The chain tier mirrors
    `_gen_match_if_elif_record` (`if`/`} else if`/`} else`); the guarded
    tier `_gen_match_guarded_record` (standalone blocks + the
    `__match_end_N` second counter draw; a class arm's guard nests INSIDE
    the block after the bindings, an or-arm's guard composes into the
    block condition)."""
    subj_type = declared.get(stmt.subject.name)
    arms: list[THIRMatchArm] = []
    always_arms = 0
    for i, case in enumerate(stmt.cases):
        if not _record_arm_ok(
                case, lc.analyzer, declared, pointers,
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                stmt.subject):
            raise ThirUnsupported("stmt.match")
        test, bnode = _match_arm_parts(case)
        if _match_record_arm_always(test):
            always_arms += 1
            if kind == "if_elif_record" and (
                    always_arms > 1 or i != len(stmt.cases) - 1):
                raise ThirUnsupported("stmt.match")
        arm_declared = dict(declared)
        field_conds: tuple = ()
        field_bindings: tuple = ()
        or_conds = None
        if isinstance(test, TpyClassPattern):
            field_conds, field_bindings = _lower_field_subpatterns(
                test, declared, arm_declared, lc, from_case_var=False)
        elif isinstance(test, TpyOrPattern):
            or_conds = _lower_or_field_conds(test, lc)
        binding = None
        if bnode is not None:
            mode = ("assign" if bnode.name in declared
                    else "copy" if bnode.bind_by_value else "ref")
            _witness(f"match.bind_{mode}")
            binding = THIRMatchBinding(name=bnode.name, mode=mode)
            arm_declared[bnode.name] = subj_type
        guard = None
        if case.guard is not None:
            _witness("match.guard_arm")
            guard = _lower_match_guard(case.guard, lc, arm_declared)
        arms.append(THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
            body=_statements._lower_scoped_stmts(
                case.body, lc, arm_declared, loop_depth=loop_depth),
            loc=case.loc, binding=binding, guard=guard,
            field_conds=field_conds, field_bindings=field_bindings,
            or_conds=or_conds),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy=kind,
        subject=_lower_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        loc=loc,
    )

def _lower_match_optional(stmt: TpyMatch, lc: _LowerCtx,
                          declared: dict[str, TpyType], loc,
                          pointers: AbstractSet[str],
                          predeclared: AbstractSet[str],
                          hoist_decls: 'list[tuple[str, str]]', *,
                          loop_depth: int = 0) -> THIRMatch:
    """Lower an optional_partition `match` (O1) -- see THIRMatch's
    `none_entry` block comment for the emit shape. The None arm's body/loc
    become `none_entry` (its comment renders at the OUTER indent, the AST's
    single-unguarded-None branch); the one inner arm becomes the single
    always-match THIRMatchArm, its capture/`as` binding vs the
    `__match_inner_N` alias -- mode folds like the scalar tiers ('copy' for
    sema's free-copy scalars, 'ref' otherwise; the record deref binds
    `auto&`), `from_case_var` says "bind the extracted value, not the
    subject". Bodies lower under narrowing-scope snapshots like every
    branch body."""
    partition = partition_optional_cases(stmt.cases)
    if partition is None:
        raise ThirUnsupported("stmt.match")
    none_cases, inner_cases = partition
    if not unwrap_readonly(stmt.subject_type).uses_pointer_repr():
        return _lower_optional_value_dispatch(
            stmt, lc, declared, loc, pointers, predeclared, hoist_decls,
            none_cases, inner_cases, loop_depth=loop_depth)
    inner_type = unwrap_readonly(stmt.subject_type).inner
    # The AST's record-inner dispatch keys on the RAW inner (a readonly-
    # wrapped record inner takes the if/elif chain there) -- mirrored.
    if isinstance(inner_type, NominalType) and inner_type.is_user_record:
        return _lower_optional_inner_record(
            stmt, lc, declared, loc, pointers, hoist_decls,
            none_cases, inner_cases, inner_type, loop_depth=loop_depth)
    if len(none_cases) > 1 or len(inner_cases) != 1:
        raise ThirUnsupported("stmt.match")
    none_entry = None
    if none_cases:
        _witness("match.optional_none_arm")
        ncase = none_cases[0]
        parts = _match_arm_parts(ncase)
        if parts is None:
            raise ThirUnsupported("stmt.match")
        test, bnode = parts
        if bnode is not None or not isinstance(test, TpyLiteralPattern):
            raise ThirUnsupported("stmt.match")
        none_entry = THIRMatchArmEntry(
            body=_statements._lower_scoped_stmts(
                ncase.body, lc, dict(declared), loop_depth=loop_depth),
            loc=ncase.loc)
    else:
        _witness("match.optional_value_only")
    case = inner_cases[0]
    if case.guard is not None:
        raise ThirUnsupported("stmt.match")
    parts = _match_arm_parts(case)
    if parts is None:
        raise ThirUnsupported("stmt.match")
    test, bnode = parts
    if isinstance(test, TpyClassPattern):
        if (test.keywords or test.positional
                or test.resolved_type is None
                or test.resolved_type != inner_type):
            raise ThirUnsupported("stmt.match")
    elif test is not None:
        raise ThirUnsupported("stmt.match")
    binding = None
    arm_declared = dict(declared)
    if bnode is not None:
        if (bnode.name in predeclared or bnode.name in pointers
                or bnode.name in lc.narrow.narrowed
                or bnode.name in lc.storage_tuple_locals):
            raise ThirUnsupported("stmt.match")
        mode = "copy" if bnode.bind_by_value else "ref"
        _witness(f"match.bind_{mode}")
        _witness("match.optional_inner_bind")
        binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                   from_case_var=True)
        arm_declared[bnode.name] = inner_type
    saved_forbidden = lc.forbidden_writes.copy()
    if bnode is not None:
        lc.forbidden_writes.add(bnode.name)
    try:
        body = _statements._lower_scoped_stmts(
            case.body, lc, arm_declared, loop_depth=loop_depth)
    finally:
        lc.forbidden_writes = saved_forbidden
    arm = THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
        body=body,
        loc=case.loc, binding=binding),))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="optional_partition",
        subject=_lower_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=(arm,),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        none_entry=none_entry,
        loc=loc,
    )

def _lower_optional_inner_record(
        stmt: TpyMatch, lc: _LowerCtx, declared: dict[str, TpyType], loc,
        pointers: AbstractSet[str],
        hoist_decls: 'list[tuple[str, str]]', none_cases, inner_cases,
        inner_type, *, loop_depth: int = 0) -> THIRMatch:
    """_gen_match_optimized_optional's record-inner dispatch (pointer-repr
    subjects): the null split, then `_emit_optional_inner_record`'s if/elif
    chain over the `__match_inner_N` deref alias -- byte-identical to the
    record tier's chain one level in, so the emit reuses
    `_emit_match_if_elif_record` (inner_strategy 'if_elif_record'). Guards
    stay rejected: the AST inlines them into the chain condition
    (`cond && guard`, or the bare guard), a shape the record chain emitter
    never produces. A cond-free class alternative inside an or-pattern also
    rejects -- the AST renders it as a literal `true` there, unlike the
    record tier's skip. The value-repr sibling (a ValueType-record inner)
    stays on the O2 reject: every value-repr `Optional[record]` name is
    still param/decl-gated upstream, so that leg would ship unwitnessed."""
    _witness("match.optional_inner_record")
    if len(none_cases) > 1:
        raise ThirUnsupported("stmt.match")
    none_entry = None
    if none_cases:
        _witness("match.optional_none_arm")
        ncase = none_cases[0]
        parts = _match_arm_parts(ncase)
        if parts is None:
            raise ThirUnsupported("stmt.match")
        ntest, nbnode = parts
        if nbnode is not None or not isinstance(ntest, TpyLiteralPattern):
            raise ThirUnsupported("stmt.match")
        none_entry = THIRMatchArmEntry(
            body=_statements._lower_scoped_stmts(
                ncase.body, lc, dict(declared), loop_depth=loop_depth),
            loc=ncase.loc)
    else:
        _witness("match.optional_value_only")
    arms: list[THIRMatchArm] = []
    always_arms = 0
    for i, case in enumerate(inner_cases):
        if case.guard is not None:
            raise ThirUnsupported("stmt.match")
        if not _record_arm_ok(
                case, lc.analyzer, declared, pointers,
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                stmt.subject):
            raise ThirUnsupported("stmt.match")
        test, bnode = _match_arm_parts(case)
        if isinstance(test, TpyClassPattern):
            # A mismatching class pattern would need an isinstance the AST's
            # bare has-value check never emits (the polymorphic tier's job).
            if test.resolved_type != inner_type:
                raise ThirUnsupported("stmt.match")
        elif isinstance(test, TpyOrPattern):
            for alt in test.patterns:
                if isinstance(alt, TpyWildcardPattern):
                    continue
                if alt.resolved_type != inner_type:
                    raise ThirUnsupported("stmt.match")
                if not any(isinstance(s, TpyLiteralPattern)
                           for _, s in alt.keywords):
                    raise ThirUnsupported("stmt.match")
        if _match_record_arm_always(test):
            always_arms += 1
            if always_arms > 1 or i != len(inner_cases) - 1:
                raise ThirUnsupported("stmt.match")
        arm_declared = dict(declared)
        field_conds: tuple = ()
        field_bindings: tuple = ()
        or_conds = None
        if isinstance(test, TpyClassPattern):
            field_conds, field_bindings = _lower_field_subpatterns(
                test, declared, arm_declared, lc, from_case_var=True)
        elif isinstance(test, TpyOrPattern):
            or_conds = _lower_or_field_conds(test, lc)
        binding = None
        if bnode is not None:
            mode = ("assign" if bnode.name in declared
                    else "copy" if bnode.bind_by_value else "ref")
            _witness(f"match.bind_{mode}")
            _witness("match.optional_inner_bind")
            binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                       from_case_var=True)
            arm_declared[bnode.name] = inner_type
        saved_forbidden = lc.forbidden_writes.copy()
        if bnode is not None:
            lc.forbidden_writes.add(bnode.name)
        try:
            body = _statements._lower_scoped_stmts(
                case.body, lc, arm_declared, loop_depth=loop_depth)
        finally:
            lc.forbidden_writes = saved_forbidden
        arms.append(THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding,
            field_conds=field_conds, field_bindings=field_bindings,
            or_conds=or_conds),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="optional_partition",
        subject=_lower_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        none_entry=none_entry,
        inner_strategy="if_elif_record",
        loc=loc,
    )


def _lower_optional_value_dispatch(
        stmt: TpyMatch, lc: _LowerCtx, declared: dict[str, TpyType], loc,
        pointers: AbstractSet[str], predeclared: AbstractSet[str],
        hoist_decls: 'list[tuple[str, str]]', none_cases, inner_cases, *,
        loop_depth: int = 0) -> THIRMatch:
    """_gen_match_optimized_optional's value-repr form (O2): the has_value
    split, then the multi-arm inner dispatch over the `__match_inner_N`
    deref alias -- the enum/primitive switch (`_emit_switch_groups` with
    the inner subject) or the unguarded literal `==` chain
    (`_emit_optional_inner_if_elif`). The inner arm walk mirrors the scalar
    tiers against the alias; per-arm subject narrowing (type_facts) only
    retypes the declared view -- the value unwrap on narrowed reads is the
    name arm's deref-on-narrow, decided from sema's per-node types. Record
    inners (unreachable today -- value-repr `Optional[record]` names are
    param/decl-gated upstream; see `_lower_optional_inner_record`),
    guarded/multi None arms, and the guarded `==` chain (inline
    `cond && guard`, a shape the scalar chain tier never emits) stay
    rejected."""
    inner_type = unwrap_readonly(unwrap_readonly(stmt.subject_type).inner)
    if _eligible_enum(inner_type, lc.analyzer) is not None:
        kind = "switch_enum"
    elif is_fixed_int_type(inner_type) or is_bool_type(inner_type):
        # The AST sends bool inners to the primitive switch (unlike the
        # top-level bool subject, which -Wswitch-bool keeps on the chain).
        kind = "switch_primitive"
    elif isinstance(inner_type, NominalType) and inner_type.is_user_record:
        raise ThirUnsupported("stmt.match")
    else:
        kind = "if_elif"
    _witness("match.optional_value_dispatch")
    none_entry = None
    if none_cases:
        if len(none_cases) > 1:
            raise ThirUnsupported("stmt.match")
        ncase = none_cases[0]
        parts = _match_arm_parts(ncase)
        if parts is None:
            raise ThirUnsupported("stmt.match")
        ntest, nbnode = parts
        if nbnode is not None or not isinstance(ntest, TpyLiteralPattern):
            raise ThirUnsupported("stmt.match")
        _witness("match.optional_none_arm")
        none_entry = THIRMatchArmEntry(
            body=_statements._lower_scoped_stmts(
                ncase.body, lc, dict(declared), loop_depth=loop_depth),
            loc=ncase.loc)
    else:
        _witness("match.optional_value_only")
    arms, default_goto, has_defaults = _lower_scalar_arms(
        inner_cases, kind, lc, declared, pointers, inner_type,
        allow_facts=True, chain_guards_ok=False, bind_from_case_var=True,
        loop_depth=loop_depth)
    # The AST's inner _emit_switch_groups call never passes is_exhaustive,
    # so the synthetic default keys only on a user default's absence.
    synthetic_default = kind != "if_elif" and not has_defaults
    if synthetic_default:
        _witness("match.synthetic_default")
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="optional_partition",
        # The match head reads the RAW optional into the subject binding
        # (the has_value split is the null check), so the bare read admits.
        subject=_lower_expr(stmt.subject, lc, declared,
                            allow_whole_optional=True),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=synthetic_default,
        default_goto=default_goto,
        none_entry=none_entry,
        optional_value_repr=True,
        inner_strategy=kind,
        loc=loc,
    )

def _optional_lit_piece(value) -> 'tuple[str, str] | None':
    """`_gen_literal_cond`'s four arms as a (prefix, suffix) piece around the
    subject, the compare running against the `(*subj)` deref."""
    if isinstance(value, bool):
        return ("(*", f") == {'true' if value else 'false'}")
    if isinstance(value, int):
        return ("(*", f") == {value}")
    if isinstance(value, float):
        return ("(*", f") == {value!r}")
    if isinstance(value, str):
        return ("(*", f") == {cpp_string_literal_expr(value)}")
    return None


def _optional_value_piece(pattern: TpyValuePattern, analyzer,
                          ) -> 'tuple[str, str] | None':
    """A value-pattern compare piece: only the enum-member shape pre-renders
    (`_enum_member_cpp` is the AST's gen_expr for it); a general value
    expression needs the emit-time gen_expr + temp flush -- not mirrored."""
    if (isinstance(pattern.expr, TpyFieldAccess)
            and pattern.expr.enum_member_of is not None):
        return ("(*", f") == {_enum_member_cpp(pattern.expr, analyzer)}")
    return None


def _optional_class_cond_ok(pattern: TpyClassPattern, inner_type,
                            analyzer) -> bool:
    """A class pattern the chain tier renders as a condition: the exact
    inner type (a mismatching pattern would need an isinstance the AST's
    bare has-value check does not emit -- the polymorphic tier's job), no
    positional sub-patterns."""
    return (pattern.resolved_type is not None
            and not pattern.positional
            and unwrap_readonly(pattern.resolved_type) == inner_type)


def _optional_class_alt_pieces(alt: TpyClassPattern, inner_type, analyzer,
                               hasval_piece: 'tuple[str, str]',
                               ) -> 'tuple[tuple[str, str], ...] | None':
    """One or-pattern class alternative's cond pieces (has-value + literal
    field compares over the deref). Or-arms carry no bindings on the AST
    path, so a capture sub-pattern would be silently dropped -- reject."""
    if not _optional_class_cond_ok(alt, inner_type, analyzer):
        return None
    if alt.keywords and not _f1_record(alt.resolved_type, analyzer):
        return None
    pieces: list[tuple[str, str]] = [hasval_piece]
    for fname, sub in alt.keywords:
        if isinstance(sub, TpyWildcardPattern):
            continue
        if not isinstance(sub, TpyLiteralPattern):
            return None
        pair = _match_field_cond(alt, fname, sub.value, analyzer)
        if pair is None:
            return None
        _witness("match.field_none" if sub.value is None
                 else "match.field_cond")
        pieces.append((pair[0] + "(*", ")" + pair[1]))
    return tuple(pieces)


def _lower_match_optional_chain(stmt: TpyMatch, lc: _LowerCtx,
                                declared: dict[str, TpyType], loc,
                                pointers: AbstractSet[str],
                                hoist_decls: 'list[tuple[str, str]]',
                                kind: str, *,
                                loop_depth: int = 0) -> THIRMatch:
    """Lower a chain-optional `match` (the non-partitioned Optional subject):
    per arm `_gen_match_optional_cond`'s pre-rendered condition groups (the
    null / has-value tests spelled per repr, compares and field conditions
    over the `(*subj)` deref, or-alternatives ||-joined -- see
    `THIRMatchArmEntry.opt_conds`) and `_emit_optional_arm_bindings`' binding
    set: class-arm field captures against the deref, the whole-subject
    capture/`as` binding against the deref (`from_case_var`) or -- sema's
    binds_full_optional -- the full Optional, which registers the name in
    `lc.value_opt_locals` so its body reads ride the value-opt param renders
    (the AST's `ctx.var_types` registration). Optional-subject arms carry no
    `type_facts` (sema narrows via `narrowed_types`, baked into body node
    types), so any fact here is an unmirrored shape. The unguarded tier keeps
    the plain chain's single-trailing-always-arm rule; the guarded tier's
    standalone blocks accept always arms anywhere."""
    subj_type = unwrap_readonly(stmt.subject_type)
    uses_ptr = subj_type.uses_pointer_repr()
    inner_type = subj_type.inner
    null_piece = (("", " == nullptr") if uses_ptr
                  else ("!", ".has_value()"))
    hasval_piece = (("", " != nullptr") if uses_ptr
                    else ("", ".has_value()"))
    arms: list[THIRMatchArm] = []
    always_arms = 0
    for i, case in enumerate(stmt.cases):
        if case.type_facts:
            raise ThirUnsupported("stmt.match")
        parts = _match_arm_parts(case)
        if parts is None:
            raise ThirUnsupported("stmt.match")
        test, bnode = parts
        arm_declared = dict(declared)
        field_bindings: tuple = ()
        opt_conds = None
        binds_full = False
        is_always = test is None or isinstance(test, TpyWildcardPattern)
        if is_always:
            binds_full = getattr(case.pattern, "binds_full_optional", False)
        elif isinstance(test, TpyLiteralPattern) and test.value is None:
            if bnode is not None:  # `case None as x:` is a sema error
                raise ThirUnsupported("stmt.match")
            opt_conds = ((False, (null_piece,)),)
        elif isinstance(test, TpyLiteralPattern):
            piece = _optional_lit_piece(test.value)
            if piece is None:
                raise ThirUnsupported("stmt.match")
            opt_conds = ((False, (hasval_piece, piece)),)
        elif isinstance(test, TpyValuePattern):
            piece = _optional_value_piece(test, lc.analyzer)
            if piece is None:
                raise ThirUnsupported("stmt.match")
            opt_conds = ((False, (hasval_piece, piece)),)
        elif isinstance(test, TpyClassPattern):
            if not _optional_class_cond_ok(test, inner_type, lc.analyzer):
                raise ThirUnsupported("stmt.match")
            if not _match_keywords_ok(
                    test, lc.analyzer, pointers, lc.narrow.narrowed.keys(),
                    lc.storage_tuple_locals, dict(arm_declared),
                    allow_conds=True):
                raise ThirUnsupported("stmt.match")
            if any(nm in lc.value_opt_locals
                   for nm in _match_pattern_captures(test)):
                raise ThirUnsupported("stmt.match")
            field_conds, field_bindings = _lower_field_subpatterns(
                test, declared, arm_declared, lc, from_case_var=True)
            pieces = (hasval_piece,) + tuple(
                (pre + "(*", ")" + suf) for pre, suf in field_conds)
            opt_conds = ((False, pieces),)
        elif isinstance(test, TpyOrPattern):
            if bnode is not None:  # or-arm bindings drop on the AST path
                raise ThirUnsupported("stmt.match")
            _witness("match.optional_chain_or")
            groups: list[tuple[bool, tuple]] = []
            for alt in test.patterns:
                if isinstance(alt, TpyWildcardPattern):
                    groups = None
                    break
                if isinstance(alt, TpyLiteralPattern) and alt.value is None:
                    groups.append((False, (null_piece,)))
                elif isinstance(alt, TpyLiteralPattern):
                    piece = _optional_lit_piece(alt.value)
                    if piece is None:
                        raise ThirUnsupported("stmt.match")
                    groups.append((True, (hasval_piece, piece)))
                elif isinstance(alt, TpyValuePattern):
                    piece = _optional_value_piece(alt, lc.analyzer)
                    if piece is None:
                        raise ThirUnsupported("stmt.match")
                    groups.append((True, (hasval_piece, piece)))
                elif isinstance(alt, TpyClassPattern):
                    pieces = _optional_class_alt_pieces(
                        alt, inner_type, lc.analyzer, hasval_piece)
                    if pieces is None:
                        raise ThirUnsupported("stmt.match")
                    groups.append((True, pieces))
                else:  # a capture alternative's binding would drop
                    raise ThirUnsupported("stmt.match")
            opt_conds = tuple(groups) if groups is not None else None
            is_always = opt_conds is None
        else:
            raise ThirUnsupported("stmt.match")
        if is_always or opt_conds is None:
            always_arms += 1
            if kind == "if_elif_optional" and (
                    always_arms > 1 or i != len(stmt.cases) - 1):
                raise ThirUnsupported("stmt.match")
        binding = None
        if bnode is not None:
            if (bnode.name in pointers or bnode.name in lc.narrow.narrowed
                    or bnode.name in lc.storage_tuple_locals
                    or (not binds_full
                        and bnode.name in lc.value_opt_locals)):
                raise ThirUnsupported("stmt.match")
            if binds_full:
                # A full-Optional binding needs the value-opt local renders;
                # only the scalar family has them (pointer-repr subjects
                # would bind the raw `T*`, an unmirrored shape).
                if (uses_ptr
                        or _value_opt_scalar(subj_type, lc.analyzer) is None):
                    raise ThirUnsupported("stmt.match")
                bind_type: 'TpyType | None' = subj_type
            else:
                bind_type = inner_type
            mode = ("assign" if bnode.name in declared
                    else "copy" if bnode.bind_by_value else "ref")
            _witness(f"match.bind_{mode}")
            binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                       from_case_var=not binds_full)
            arm_declared[bnode.name] = bind_type
            if binds_full:
                _witness("match.optional_full_bind")
                lc.value_opt_locals.add(bnode.name)
        saved_forbidden = lc.forbidden_writes.copy()
        if binding is not None and binding.mode != "assign":
            lc.forbidden_writes.add(binding.name)
        try:
            guard = None
            if case.guard is not None:
                _witness("match.guard_arm")
                guard = _lower_match_guard(case.guard, lc, arm_declared)
            body = _statements._lower_scoped_stmts(
                case.body, lc, arm_declared, loop_depth=loop_depth)
        finally:
            lc.forbidden_writes = saved_forbidden
        arms.append(THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding, guard=guard,
            field_bindings=field_bindings, opt_conds=opt_conds),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy=kind,
        subject=_lower_expr(stmt.subject, lc, declared,
                            allow_whole_optional=True),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        optional_value_repr=not uses_ptr,
        loc=loc,
    )


def _str_lit_cond_group(strs) -> 'tuple[tuple[bool, tuple], ...]':
    """The str-switch tier's literal condition groups: one bare
    `subj == "lit"` piece per alternative, `||`-joined without parens
    (`_gen_match_switch_str`'s guarded-arm join)."""
    return tuple(
        (False, (("", f" == {cpp_string_literal_expr(s)}"),)) for s in strs)


def _lower_match_switch_str(stmt: TpyMatch, lc: _LowerCtx,
                            declared: dict[str, TpyType], loc,
                            pointers: AbstractSet[str],
                            hoist_decls: 'list[tuple[str, str]]', *,
                            loop_depth: int = 0) -> THIRMatch:
    """Lower a switch_str `match` -- `_gen_match_switch_str`'s discriminator
    dispatch (see THIRMatch.str_disc_kind): cases partition into guarded
    str-literal arms (pre-switch standalone blocks, source order), unguarded
    literal arms (bucketed by `find_best_discriminator` over the UTF-8
    bytes, buckets in ascending disc-value order, an or-arm's body
    RE-LOWERED per alternative string like the union or-bind), and trailing
    wildcard/capture arms (post-switch blocks); anything else in trailing
    position is the AST's CodeGenError -- reject. The emit REORDERS
    (guarded first, buckets, trailing last), which is semantics-preserving
    only when literal conditions are disjoint from what ran earlier: a
    trailing arm followed by any literal arm, or a guarded literal sharing
    a string with an EARLIER unguarded arm, would run in the wrong order --
    both reject rather than mirror the reorder."""
    bind_type = declared.get(stmt.subject.name)
    guarded_src: list = []
    unguarded_src: list = []
    trailing_src: list = []
    for i, case in enumerate(stmt.cases):
        if case.type_facts:
            raise ThirUnsupported("stmt.match")
        parts = _match_arm_parts(case)
        if parts is None:
            raise ThirUnsupported("stmt.match")
        test, bnode = parts
        strs = None
        if isinstance(test, TpyLiteralPattern) and isinstance(test.value,
                                                              str):
            strs = (test.value,)
        elif (isinstance(test, TpyOrPattern)
              and all(isinstance(a, TpyLiteralPattern)
                      and isinstance(a.value, str) for a in test.patterns)):
            strs = tuple(a.value for a in test.patterns)
        if strs is not None:
            if case.guard is not None:
                guarded_src.append((i, case, strs, bnode))
            else:
                unguarded_src.append((i, case, strs, bnode))
        else:
            if not (test is None or isinstance(test, TpyWildcardPattern)):
                raise ThirUnsupported("stmt.match")
            trailing_src.append((i, case, bnode))
    if trailing_src:
        first_trailing = trailing_src[0][0]
        if any(i > first_trailing
               for i, _c, _s, _b in guarded_src + unguarded_src):
            raise ThirUnsupported("stmt.match")
    for gi, _case, gstrs, _b in guarded_src:
        for ui, _ucase, ustrs, _ub in unguarded_src:
            if ui < gi and set(ustrs) & set(gstrs):
                raise ThirUnsupported("stmt.match")

    def lower_binding(bnode, arm_declared) -> 'THIRMatchBinding | None':
        if bnode is None:
            return None
        if (bnode.name in pointers or bnode.name in lc.narrow.narrowed
                or bnode.name in lc.storage_tuple_locals
                or bnode.name in lc.value_opt_locals):
            raise ThirUnsupported("stmt.match")
        mode = ("assign" if bnode.name in declared
                else "copy" if bnode.bind_by_value else "ref")
        _witness(f"match.bind_{mode}")
        arm_declared[bnode.name] = bind_type
        return THIRMatchBinding(name=bnode.name, mode=mode)

    str_guarded: list[THIRMatchArmEntry] = []
    for _i, case, strs, bnode in guarded_src:
        _witness("match.str_guard_prefix")
        _witness("match.guard_arm")
        arm_declared = dict(declared)
        binding = lower_binding(bnode, arm_declared)
        guard = _lower_match_guard(case.guard, lc, arm_declared)
        body = _statements._lower_scoped_stmts(
            case.body, lc, arm_declared, loop_depth=loop_depth)
        str_guarded.append(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding, guard=guard,
            opt_conds=_str_lit_cond_group(strs)))

    all_strings = [s for _i, _c, strs, _b in unguarded_src for s in strs]
    kind, param, _buckets = find_best_discriminator(all_strings)
    bucket_map: 'dict[int, list]' = {}
    for _i, case, strs, bnode in unguarded_src:
        for s in strs:
            bucket_map.setdefault(
                discriminator_key(s, kind, param), []).append(
                    (case, s, bnode))
    arms: list[THIRMatchArm] = []
    for key in sorted(bucket_map):
        entries = []
        for case, s, bnode in bucket_map[key]:
            # One entry per (case, string): the or-arm body re-lowers per
            # alternative (emit-side counters advance per emission, like
            # the AST's repeated _emit_case_body runs).
            arm_declared = dict(declared)
            binding = lower_binding(bnode, arm_declared)
            body = _statements._lower_scoped_stmts(
                case.body, lc, arm_declared, loop_depth=loop_depth)
            entries.append(THIRMatchArmEntry(
                body=body, loc=case.loc, binding=binding,
                opt_conds=_str_lit_cond_group((s,))))
        arms.append(THIRMatchArm(labels=(case_label(key, kind),),
                                 entries=tuple(entries)))

    str_trailing: list[THIRMatchArmEntry] = []
    for _i, case, bnode in trailing_src:
        _witness("match.str_trailing_arm")
        arm_declared = dict(declared)
        binding = lower_binding(bnode, arm_declared)
        guard = None
        if case.guard is not None:
            _witness("match.guard_arm")
            guard = _lower_match_guard(case.guard, lc, arm_declared)
        body = _statements._lower_scoped_stmts(
            case.body, lc, arm_declared, loop_depth=loop_depth)
        str_trailing.append(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding, guard=guard))

    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="switch_str",
        subject=_lower_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        str_disc_kind=kind,
        str_disc_param=param,
        str_guarded=tuple(str_guarded),
        str_trailing=tuple(str_trailing),
        loc=loc,
    )


def _lower_match_union(stmt: TpyMatch, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc,
                       pointers: AbstractSet[str],
                       hoist_decls: 'list[tuple[str, str]]', *,
                       loop_depth: int = 0) -> THIRMatch:
    """Lower a switch_union `match` (M4a): arms in SOURCE order (no default
    regrouping -- `_gen_match_switch_union` emits `default:` in place),
    labels are numeric variant indices (`_variant_index` over the full
    member ordering), a class arm with sema's narrowing fact draws the
    `__case_{i}` extraction alias (i = source case index) and lowers its
    body with subject reads renamed to it (the U3 mechanic) and the
    subject retyped to the fact; `as` binds the alias (or the composed
    get). Or-pattern arms stack index labels over one shared block; `case
    None:` is the monostate index. `is_ptr_variant` folds the `*std::get`
    deref (type-level -- the admitted bare-name subjects are exactly the
    U3 slice's ptr/value split)."""
    u = unwrap_readonly(stmt.subject_type)
    _witness("match.switch_union")
    members = _union_index_members(u)
    if members is None:
        raise ThirUnsupported("stmt.match")
    subj_name = stmt.subject.name
    arms: list[THIRMatchArm] = []
    seen: set[int] = set()
    for i, case in enumerate(stmt.cases):
        if not _union_arm_ok(
                case, lc.analyzer, declared, pointers,
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                stmt.subject, members, seen):
            raise ThirUnsupported("stmt.match")
        test, bnode = _match_arm_parts(case)
        facts = case.type_facts or {}
        if (isinstance(test, TpyOrPattern)
                and any(isinstance(alt, TpyClassPattern) and alt.keywords
                        for alt in test.patterns)):
            # _gen_match_switch_union's binding or-arm: one case block PER
            # alternative (the capture binds a different variant member each
            # time), the body re-lowered per block like the AST's repeated
            # _emit_case_body runs; the source comment only on the first.
            _witness("match.union_or_bind")
            for j, alt in enumerate(test.patterns):
                alt_index = _union_member_index(members, alt.resolved_type)
                alt_alias = f"__case_{i}_{j}"
                alt_declared = dict(declared)
                alt_saved = lc.narrow.snapshot()
                try:
                    if facts:
                        lc.narrow.narrowed[subj_name] = alt_alias
                        alt_declared[subj_name] = facts[subj_name]
                    alt_bindings: tuple = ()
                    if alt.keywords:
                        conds, alt_bindings = _lower_field_subpatterns(
                            alt, declared, alt_declared, lc,
                            from_case_var=True)
                        assert not conds, \
                            "field condition reached the unguarded union tier"
                    alt_body = _statements._lower_stmts(
                        case.body, lc, alt_declared, in_branch=True,
                        loop_depth=loop_depth)
                finally:
                    lc.narrow = alt_saved
                arms.append(THIRMatchArm(
                    labels=(str(alt_index),),
                    entries=(THIRMatchArmEntry(
                        body=alt_body, loc=case.loc if j == 0 else None,
                        variant_index=alt_index, case_alias=alt_alias,
                        field_bindings=alt_bindings),)))
            continue
        binding = None
        variant_index = None
        case_alias = None
        member = None
        field_bindings: tuple = ()
        arm_declared = dict(declared)
        saved = lc.narrow.snapshot()
        try:
            if test is None:
                labels: tuple[str, ...] = ()
                _witness("match.union_default")
            elif isinstance(test, TpyClassPattern):
                member = test.resolved_type
                variant_index = _union_member_index(members, member)
                labels = (str(variant_index),)
                # _gen_match_switch_union draws the alias for keywords OR
                # narrowing facts; the subject rename applies to facts only.
                if facts or test.keywords:
                    case_alias = f"__case_{i}"
                    _witness("match.union_alias")
                if facts:
                    lc.narrow.narrowed[subj_name] = case_alias
                    arm_declared[subj_name] = facts[subj_name]
                if test.keywords:
                    conds, field_bindings = _lower_field_subpatterns(
                        test, declared, arm_declared, lc, from_case_var=True)
                    assert not conds, \
                        "field condition reached the unguarded union tier"
            elif isinstance(test, TpyOrPattern):
                _witness("match.or_labels")
                labels = tuple(
                    str(_union_member_index(members, alt.resolved_type))
                    for alt in test.patterns)
            else:  # `case None:`
                _witness("match.union_none_arm")
                variant_index = _union_member_index(members, NoneType())
                labels = (str(variant_index),)
            if bnode is not None:
                mode = ("assign" if bnode.name in declared
                        else "copy" if bnode.bind_by_value else "ref")
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=member is not None)
                arm_declared[bnode.name] = (member if member is not None
                                            else arm_declared[subj_name])
            body = _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                loop_depth=loop_depth)
        finally:
            lc.narrow = saved
        arms.append(THIRMatchArm(labels=labels, entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding,
            variant_index=variant_index, case_alias=case_alias,
            field_bindings=field_bindings),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="switch_union",
        subject=_lower_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        is_ptr_variant=is_ptr_variant_union(u),
        loc=loc,
    )

def _lower_match_guarded_union(stmt: TpyMatch, lc: _LowerCtx,
                               declared: dict[str, TpyType], loc,
                               pointers: AbstractSet[str],
                               hoist_decls: 'list[tuple[str, str]]',
                               *, loop_depth: int = 0,
                               ) -> THIRMatch:
    """Lower a guarded_union `match` (M4b) -- `_gen_match_guarded_union`'s
    per-index grouping: class arms land on their variant index, or-pattern
    class alternatives distribute per index, wildcard/capture arms
    broadcast to EVERY index (each list truncates after its first
    truly-unguarded entry -- one with neither a guard nor a field
    condition; later entries are unreachable), indices whose
    entries are all wildcard/capture coalesce into one trailing `default:`
    block (the first such list -- broadcast makes them identical). Groups
    emit in ascending index order. The group's `__case_{idx}` alias (idx =
    VARIANT index, unlike the unguarded tier's source-case numbering)
    draws when any class entry carries keywords, narrows, or `as`-binds;
    field conditions/captures pre-render against the alias (bindings emit
    before the guard when the entry has no conditions, else inside the
    `if (conds && guard)` block); class-entry bodies
    lower under the alias rename + fact retype. Broadcast copies of one
    source case lower once per hosting group (wildcard bodies never
    narrow, so the copies are identical; emit-side counters advance per
    emission like the AST's repeated gen_stmt runs)."""
    u = unwrap_readonly(stmt.subject_type)
    _witness("match.guarded_union")
    members = _union_index_members(u)
    if members is None:
        raise ThirUnsupported("stmt.match")
    n = len(members)
    subj_name = stmt.subject.name

    type_arms: dict[int, list] = {i: [] for i in range(n)}
    for case in stmt.cases:
        if not _guarded_union_arm_ok(
                case, lc.analyzer, declared, pointers,
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                stmt.subject, members):
            raise ThirUnsupported("stmt.match")
        test, bnode = _match_arm_parts(case)
        if test is None:
            for idx in range(n):
                type_arms[idx].append((case, "always", None, bnode, None))
        elif isinstance(test, TpyClassPattern):
            idx = _union_member_index(members, test.resolved_type)
            type_arms[idx].append((case, "class", test.resolved_type, bnode,
                                   test))
        elif isinstance(test, TpyOrPattern):
            for alt in test.patterns:
                idx = _union_member_index(members, alt.resolved_type)
                type_arms[idx].append((case, "class", alt.resolved_type,
                                       None, alt))
        else:  # `case None:`
            _witness("match.union_none_arm")
            idx = _union_member_index(members, NoneType())
            type_arms[idx].append((case, "none", None, None, None))
    for idx in range(n):
        truncated = []
        for entry in type_arms[idx]:
            truncated.append(entry)
            # A field condition keeps the list open like a guard (the AST's
            # has_field_guard) -- a failed field compare falls through.
            pat = entry[4]
            has_field_guard = (pat is not None and any(
                MatchGenerator._sub_has_field_condition(sub)
                for _, sub in pat.keywords))
            if entry[0].guard is None and not has_field_guard:
                break
        type_arms[idx] = truncated
    default_indices: set[int] = set()
    default_src = None
    for idx in range(n):
        entries = type_arms[idx]
        if entries and all(k == "always" for _, k, _, _, _ in entries):
            default_indices.add(idx)
            if default_src is None:
                default_src = entries

    def lower_entry(case, kind, member, bnode, pattern, alias, idx):
        arm_declared = dict(declared)
        saved = lc.narrow.snapshot()
        saved_reads = lc.forbidden_reads.copy()
        try:
            facts = case.type_facts or {}
            if kind == "class" and facts and alias is not None:
                lc.narrow.narrowed[subj_name] = alias
                arm_declared[subj_name] = facts[subj_name]
            field_conds: tuple = ()
            field_bindings: tuple = ()
            if kind == "class" and pattern is not None and pattern.keywords:
                field_conds, field_bindings = _lower_field_subpatterns(
                    pattern, declared, arm_declared, lc, from_case_var=True)
                if field_conds:
                    _witness("match.union_field_cond")
            binding = None
            if bnode is not None:
                mode = ("assign" if bnode.name in declared
                        else "copy" if bnode.bind_by_value else "ref")
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=kind == "class")
                arm_declared[bnode.name] = (member if kind == "class"
                                            else arm_declared[subj_name])
            guard = None
            if case.guard is not None:
                _witness("match.guard_arm")
                lc.forbidden_reads.add(subj_name)
                if field_conds and pattern is not None:
                    lc.forbidden_reads.update(
                        _match_pattern_captures(pattern))
                try:
                    guard = _lower_match_guard(case.guard, lc, arm_declared)
                finally:
                    lc.forbidden_reads = saved_reads.copy()
            body = _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                loop_depth=loop_depth)
        finally:
            lc.narrow = saved
            lc.forbidden_reads = saved_reads
        return THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding, guard=guard,
            variant_index=idx if kind == "class" else None,
            case_alias=alias if kind == "class" else None,
            field_conds=field_conds, field_bindings=field_bindings)

    arms: list[THIRMatchArm] = []
    for idx in range(n):
        if idx in default_indices:
            continue
        entries_src = type_arms[idx]
        if not entries_src:
            continue
        # _gen_match_guarded_union's needs_extraction: a class entry that
        # carries keywords (field conditions/bindings), narrows
        # (type_facts), or `as`-binds.
        needs_extraction = any(
            k == "class" and ((p is not None and p.keywords)
                              or bool(case.type_facts) or b is not None)
            for case, k, _m, b, p in entries_src)
        alias = f"__case_{idx}" if needs_extraction else None
        if alias is not None:
            _witness("match.union_alias")
        arms.append(THIRMatchArm(
            labels=(str(idx),),
            entries=tuple(lower_entry(case, k, m, b, p, alias, idx)
                          for case, k, m, b, p in entries_src)))
    if default_indices and default_src:
        _witness("match.union_default")
        arms.append(THIRMatchArm(
            labels=(),
            entries=tuple(lower_entry(case, k, m, b, p, None, None)
                          for case, k, m, b, p in default_src)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="guarded_union",
        subject=_lower_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        is_ptr_variant=is_ptr_variant_union(u),
        loc=loc,
    )
