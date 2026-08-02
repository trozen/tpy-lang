"""Match routing and lowering, with per-tier arm walkers for scalar
switch/chain, record, optional-partition, union, and guarded-union.
"""

from __future__ import annotations
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from ...parse.nodes import (
    TpyAsPattern,
    TpyCall,
    TpyCapturePattern,
    TpyClassPattern,
    TpyExpr,
    TpyFieldAccess,
    TpyLiteralPattern,
    TpyMatch,
    TpyName,
    TpyOrPattern,
    TpySubscript,
    iter_capture_bindings,
    TpyValuePattern,
    TpyWildcardPattern,
)
from ...typesys import (
    LiteralType,
    NominalType,
    NoneType,
    OptionalType,
    ReadonlyType,
    TpyType,
    UnionType,
    deref_dispatch_inner,
    polymorphic_source_inner,
    polymorphic_source_is_pointer,
    substitute_type_params_simple,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (is_bool_type, is_dict, is_fixed_int_type,
                                  is_list, is_set)
from ...codegen_cpp.forms import is_plain_nonvalue
from ...codegen_cpp.types import resolve_pending_container
from ...value_category import is_rvalue_source
from ...codegen_cpp.context import cpp_string_literal_expr, escape_cpp_name
from ...codegen_cpp.match import (
    MatchGenerator,
    _match_subject_is_lvalue,
    partition_optional_cases,
    pattern_has_field_condition,
)
from ...codegen_cpp.protocols import narrow_cast_rhs
from ...codegen_cpp.string_dispatch import (
    STRING_SWITCH_THRESHOLD,
    case_label,
    discriminator_key,
    find_best_discriminator,
)
from ...liveness import stmts_terminate
from ..faces import witness as _witness
from ..fallback import ThirUnsupported, note_detail, stmt_reject_reason
from ..nodes import (
    Form,
    THIRFoldedBlock,
    THIRMatchFoldBind,
    THIRExpr,
    THIRFormConvert,
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
    _poly_subject_const,
    _resolved_bytes_value,
    _resolved_str_value,
    _value_opt_scalar,
    _value_tuple,
)
from .context import (
    _ExprResultUse,
    _ExprUse,
    _LowerCtx,
    _Prescan,
    ValueOptKind,
)
from .expressions import (
    _lower_expr,
    _lower_field_source,
    _lower_truthy,
    _narrow_subject_is_ptr,
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
    subject dispatches on its BASE type (the arm below), its per-arm
    LiteralType narrowing (`case.type_facts`) registering as arm-scope
    literal facts; the AST's dead-branch folds stay un-mirrored, fenced at
    the compare/membership lowering (`match.literal_fold`).
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
        # The AST's Literal arm dispatches on the BASE type (LiteralType
        # delegates every codegen method): str base -> the discriminator
        # switch or the `==` chain, fixed-int base -> the primitive switch,
        # anything else (bool) -> the chain. Sema's per-arm narrowing
        # (`case.type_facts`) registers as arm-scope literal facts in
        # `_lower_scalar_arms`; the un-mirrored dead-branch folds are
        # fenced at the compare/membership lowering instead of here.
        base = unwrap_readonly(t.base_type)
        if _resolved_str_value(base, analyzer) is not None:
            if _str_switch_count(stmt) >= STRING_SWITCH_THRESHOLD:
                # The AST's threshold count is type-blind, so a 5+-literal
                # Literal subject takes the DISCRIMINATOR switch there;
                # that render against a Literal subject is its own row.
                return None
            if any(c.guard is not None for c in stmt.cases):
                return "if_elif_guarded"
            return "if_elif"
        if is_fixed_int_type(base):
            return "switch_primitive"
        if _eligible_scalar(base):
            if any(c.guard is not None for c in stmt.cases):
                return "if_elif_guarded"
            return "if_elif"
        return None
    if isinstance(t, UnionType):
        # A recursive-alias wrapper (bare `Tree` alias) dispatches on its
        # `.value` variant member -- the M4c slice admits NAME subjects on
        # the unguarded tier only (_route_match narrows); a generic
        # instance (`Tree[Int32]`) is a RecursiveAliasInstanceType, which
        # never reaches this branch and keeps rejecting.
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

def _str_switch_count(stmt: TpyMatch) -> int:
    """`_should_switch_str`'s unguarded str-literal alternative count --
    type-blind, exactly like the AST's."""
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
    return count


def _match_str_switches(stmt: TpyMatch, analyzer) -> bool:
    """Mirrors _should_switch_str: a str subject with enough unguarded
    str-literal alternatives takes the discriminator-switch strategy
    (switch_str), not the if/elif chain."""
    t = unwrap_readonly(stmt.subject_type)
    if _resolved_str_value(t, analyzer) is None:
        return False
    return _str_switch_count(stmt) >= STRING_SWITCH_THRESHOLD

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
    value scalars, Char, registered enums, resolved str, value tuples, and
    the non-value families whose binding is the plain `auto&` alias into the
    subject's field (an F1 record, a list/dict/set). An Optional field binds
    a hoisted pointer instead -- its own rung."""
    t = unwrap_readonly(ft)
    return (_eligible_scalar(t) or _eligible_char(t)
            or _eligible_enum(t, analyzer) is not None
            or _resolved_str_value(t, analyzer) is not None
            or _value_tuple(t, analyzer) is not None
            or _f1_record(t, analyzer)
            or is_list(t) or is_dict(t) or is_set(t))

def _match_field_type_subst(pattern: TpyClassPattern, field_name: str,
                            analyzer) -> 'TpyType | None':
    """`_match_record_field_type` with the pattern's type-arg substitution
    applied (sema's `_build_type_subst` mirror): the SUBSTITUTED type is
    what a compile-time type guard matched against (sema stamps
    `resolved_type` on a nested class sub only when it had field patterns
    or a union to resolve, so an exact-match guard's bound type must be
    re-derived here)."""
    ft = _match_record_field_type(pattern, field_name, analyzer)
    rt = pattern.resolved_type
    if ft is None or not isinstance(rt, NominalType) or not rt.type_args:
        return ft
    record = analyzer.registry.get_record_for_type(rt)
    if record is None or not record.type_params:
        return ft
    subst = {p: a for p, a in zip(record.type_params, rt.type_args)
             if isinstance(a, TpyType)}
    if not subst:
        return ft
    return substitute_type_params_simple(ft, subst)


def _union_guard_member_ok(t: 'TpyType | None', analyzer) -> bool:
    """A union-field guard's member spelling inside `std::holds_alternative
    <T>` / `std::get<T>`: an F1 record (`render_type` == `to_cpp`, incl.
    cross-module qualification), or a value scalar / resolved-str member
    whose C++ spelling is context-free."""
    if t is None:
        return False
    u = unwrap_readonly(t)
    return (_f1_record(u, analyzer) or _eligible_scalar(u)
            or _resolved_str_value(u, analyzer) is not None)


def _match_keywords_ok(
        pattern: TpyClassPattern, analyzer, pointers: AbstractSet[str],
        narrowed: AbstractSet[str], storage_tuple_locals: AbstractSet[str],
        arm_declared: dict[str, TpyType], *, allow_conds: bool,
        nested_ok: bool = False) -> bool:
    """The field sub-pattern slice for one class pattern: literal conditions
    (the record tiers and the guarded-union tier; the unconditional union
    switch has no `&&` position, so its walk passes allow_conds=False --
    defensive, `_match_union_route` already sends conditions to the guarded
    path), free-value captures (declared into the arm scope; the same
    pointer/narrowed/tuple-alias name rejects as a whole-subject binding),
    and wildcards. NESTED class / `as` sub-patterns admit on the RECORD
    tiers only (`nested_ok` -- the tiers whose emit threads the base-name
    map): a union-field guard (`holds_alternative` + `get` around an
    eligible member spelling), a compile-time type guard on a non-union
    field, an `as` bind of the (extracted) field value, and their recursive
    keyword walks -- `_record_field_conditions` / `_gen_match_field_
    bindings`' recursion. The union/poly/optional tiers keep rejecting
    nested forms (their emits compose bindings without the map).
    Keyword-bearing patterns require the F1 record for the registry
    field-type walk (and keep @native field renames out -- the AST spells
    the raw Python name)."""
    if pattern.keywords and not _f1_record(pattern.resolved_type, analyzer):
        return False
    for fname, sub in pattern.keywords:
        as_node = sub if isinstance(sub, TpyAsPattern) else None
        inner = sub.pattern if as_node is not None else sub
        if isinstance(inner, TpyWildcardPattern):
            if as_node is not None:
                # `_ as x` field form: the AST's double-bind shape, its own row.
                return False
            continue
        if isinstance(inner, TpyLiteralPattern):
            if as_node is not None or not allow_conds:
                return False
            if _match_field_cond(pattern, fname, inner.value, analyzer) is None:
                return False
            continue
        if isinstance(inner, TpyCapturePattern):
            if as_node is not None:
                return False
            if (inner.name in pointers or inner.name in narrowed
                    or inner.name in storage_tuple_locals):
                return False
            ft = _match_record_field_type(pattern, fname, analyzer)
            if ft is None or not _match_capture_field_ok(ft, analyzer):
                return False
            arm_declared[inner.name] = ft
            continue
        if isinstance(inner, TpyClassPattern):
            if not nested_ok or inner.positional:
                return False
            if inner.is_union_field_guard:
                # The guard renders a holds_alternative condition, so it
                # needs a cond position; a cond-free tier (the unguarded
                # union switch) admits only compile-time type guards, and
                # the recursion below rejects any nested literal there the
                # same way.
                if not allow_conds:
                    return False
                if not _union_guard_member_ok(inner.resolved_type, analyzer):
                    return False
                bound_t: 'TpyType | None' = inner.resolved_type
            else:
                # A compile-time type guard: sema validated the pattern
                # type against the SUBSTITUTED field type and stamps
                # `resolved_type` only when it had fields to resolve.
                bound_t = (inner.resolved_type
                           or _match_field_type_subst(pattern, fname,
                                                      analyzer))
            if inner.keywords:
                if inner.resolved_type is None:
                    return False
                if not _match_keywords_ok(
                        inner, analyzer, pointers, narrowed,
                        storage_tuple_locals, arm_declared,
                        allow_conds=allow_conds, nested_ok=nested_ok):
                    return False
            if as_node is not None:
                if (as_node.name in pointers or as_node.name in narrowed
                        or as_node.name in storage_tuple_locals):
                    return False
                if (bound_t is None
                        or not _match_capture_field_ok(bound_t, analyzer)):
                    return False
                arm_declared[as_node.name] = bound_t
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
        storage_tuple_locals: AbstractSet[str], subj_name: 'str | None',
        subj_type: 'TpyType | None', members: tuple) -> bool:
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
                storage_tuple_locals, arm_declared, allow_conds=True,
                nested_ok=True):
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
                    allow_conds=True, nested_ok=True):
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
        # Expression subjects never carry facts (sema narrows names only);
        # a keyed fact on one is a shape outside the slice.
        if subj_name is None or set(facts) != {subj_name} or member is None:
            return False
        arm_declared[subj_name] = facts[subj_name]
    if bnode is not None:
        if (bnode.name in pointers or bnode.name in narrowed
                or bnode.name in storage_tuple_locals):
            return False
        arm_declared[bnode.name] = (member if member is not None
                                    else subj_type)
    return True

def _union_arm_ok(
        case, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], subj_name: 'str | None',
        subj_type: 'TpyType | None', members: tuple, seen: set[int]) -> bool:
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
                storage_tuple_locals, arm_declared, allow_conds=False,
                nested_ok=True):
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
                    allow_conds=False, nested_ok=True):
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
        if subj_name is None or set(facts) != {subj_name} or member is None:
            return False
        arm_declared[subj_name] = facts[subj_name]
    if bnode is not None:
        if (bnode.name in pointers or bnode.name in narrowed
                or bnode.name in storage_tuple_locals):
            return False
        arm_declared[bnode.name] = (member if member is not None
                                    else subj_type)
    return True

def _match_record_arm_always(test) -> bool:
    """Whether an arm matches unconditionally on the record tiers: a
    wildcard/capture (test None), a class pattern with no condition-
    rendering field sub-patterns (the AST's own recursive
    `_sub_has_field_condition` -- literals, union-field guards, and nested
    records carrying either), or an or-pattern whose rendered condition
    list collapses empty (a wildcard alternative clears it; condition-free
    class alternatives contribute nothing)."""
    if test is None:
        return True
    if isinstance(test, TpyClassPattern):
        return not any(MatchGenerator._sub_has_field_condition(s)
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
        storage_tuple_locals: AbstractSet[str],
        subj_type: 'TpyType | None') -> bool:
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
                storage_tuple_locals, arm_declared, allow_conds=True,
                nested_ok=True):
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
        arm_declared[bnode.name] = subj_type
    return True

@dataclass(frozen=True)
class _MatchRoute:
    kind: str
    # (name, resolved type, hoist kind): 'value' is the plain-value predecl;
    # 'ptr' the borrow-only pointer-local (`T* name;` -- an aliasing
    # whole-subject capture of an lvalue subject); 'opt_storage' the owned
    # `std::optional<T> name;` slot (rvalue-bound -- a capture of a
    # materialized rvalue subject, or an arm-body first-decl).
    hoist_types: tuple[tuple[str, TpyType, str], ...]
    union_route: 'str | None' = None
    # A call/ctor F1-record rvalue subject on the record capture tier:
    # `auto __match_subject_N = <call>;` (subject_ref=False), captures move
    # out of the owned temporary.
    subject_rvalue: bool = False


def _match_expr_subject_ok(subj: TpyExpr, declared: dict[str, TpyType],
                           pointers: AbstractSet[str],
                           narrowed: AbstractSet[str],
                           storage_tuple_locals: AbstractSet[str]) -> bool:
    """A non-name subject the storage-form tiers admit: a field/subscript
    LVALUE chain (the shared `_match_subject_is_lvalue` fact -- `auto&`
    binds the storage) rooted at `self` or a declared name that renders
    direct (pointer/narrowed/tuple-alias roots spell indirect). Method-call
    lvalue roots (accessor chains) are their own rung."""
    if not _match_subject_is_lvalue(subj):
        return False
    e = subj
    while isinstance(e, (TpyFieldAccess, TpySubscript)):
        e = e.obj
    if not isinstance(e, TpyName):
        return False
    root = e.name
    if root == "self":
        return True
    if root not in declared:
        return False
    return not (root in pointers or root in narrowed
                or root in storage_tuple_locals)


def _route_hoists(stmt: TpyMatch, analyzer, declared: dict[str, TpyType],
                  prescan: _Prescan, lc: '_LowerCtx', *, in_branch: bool,
                  in_loop: bool, nonvalue_ok: bool = False,
                  ) -> 'tuple[tuple[str, TpyType, str], ...] | None':
    """The arm-declared hoist admission shared by every routed tier (the
    try arm's discipline): already-declared names skip, fresh plain-value
    names admit in straight-line function scope only. With `nonvalue_ok`
    (the record tiers), F1-record non-value hoists additionally classify
    into `_emit_branch_decls`' two non-value arms -- borrow-only names
    (aliasing whole-subject captures; sema's stmt-borrow fact) take the
    pointer form, single-bind rvalue names the owned optional slot. None
    rejects the whole match."""
    hoist_declared: list[tuple[str, TpyType, str]] = []
    borrow_decls = analyzer.function_stmt_borrow_decls.get(id(lc.func), {})
    ever_owned = analyzer.function_ever_owned_locals.get(id(lc.func), set())
    for name, raw in analyzer.if_branch_decls.get(id(stmt), {}).items():
        if name in declared:
            continue
        if name in prescan.native_globals:
            return None
        if in_branch or in_loop:
            return None
        vtype = unwrap_ref_type(raw)
        if _statements._try_hoist_type_ok(vtype, analyzer):
            hoist_declared.append((name, vtype, "value"))
            continue
        if _value_tuple(vtype, analyzer) is not None:
            # A VALUE tuple predecls through the same plain tail arm
            # (`std::tuple<...> t;`) and its branch writes are plain assigns.
            # Scoped to the match hoist rather than widening the shared
            # `_try_hoist_type_ok`, whose other four call sites (if / try /
            # with / for) would each need their own re-verification.
            hoist_declared.append((name, vtype, "value"))
            continue
        if not nonvalue_ok:
            return None
        vtype = resolve_pending_container(vtype, analyzer) or vtype
        if (isinstance(vtype, OptionalType) and vtype.uses_pointer_repr()
                and not isinstance(vtype.inner, ReadonlyType)
                and _f1_record(vtype.inner, analyzer)):
            # Pointer-repr Optional hoist: the bare inner `T* name;`
            # (nullable pointer-local, `_emit_branch_decls`' Optional arm) --
            # a full-Optional whole-subject capture of a pointer-repr
            # subject. Reads/writes deref via `pointers`; the declared entry
            # keeps the Optional so unproven writes draw deref_check.
            if lc.func.is_generator or lc.func.is_async:
                return None
            if (name in prescan.move_through
                    or name in prescan.rvalue_reassigned):
                return None
            hoist_declared.append((name, vtype, "opt_ptr"))
            continue
        if not (is_plain_nonvalue(vtype) and _f1_record(vtype, analyzer)):
            return None
        if lc.func.is_generator or lc.func.is_async:
            # Both non-value flavors hoist storage to function top; resumable
            # leaves cannot drain those lines (the if cascade's guard).
            return None
        if name in prescan.move_through:
            return None
        if borrow_decls.get(name, False):
            # The borrow-decl const bit is the const-indirect rung. NB it is
            # the Phase-1 verdict, which understates const for a capture of a
            # never-mutated param; the AST re-asks at the decl site
            # (`_match_capture_borrows_const`). Nested-reuse arms only avoid
            # divergence here because `forbidden_writes` rejects them first --
            # widening that gate without carrying the const rung would emit
            # `T*` against the AST's `const T*`.
            return None
        if name in borrow_decls and name not in ever_owned:
            if name in prescan.rvalue_reassigned:
                # Rvalue reseats need the function-top rebind slot rung.
                return None
            hoist_declared.append((name, vtype, "ptr"))
            continue
        if _statements._opt_storage_hoist_flavor(name, vtype, lc) is None:
            hoist_declared.append((name, vtype, "opt_storage"))
            continue
        return None
    return tuple(hoist_declared)


def _match_route(
        stmt: TpyMatch, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], prescan: _Prescan,
        lc: '_LowerCtx', *, in_branch: bool,
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
        subj = stmt.subject
        if isinstance(subj, TpyName):
            if subj.name not in declared:
                return None
            if (subj.name in pointers or subj.name in narrowed
                    or subj.name in storage_tuple_locals):
                return None
        hoist_types = _route_hoists(stmt, analyzer, declared, prescan, lc,
                                    in_branch=in_branch, in_loop=in_loop)
        if hoist_types is None:
            return None
        # Guarded-vs-chain dispatch shares the AST's field-condition fact;
        # an expression subject defers its shape admission to the lowerer.
        kind = ("poly_guarded" if any(
            c.guard is not None or pattern_has_field_condition(c.pattern)
            for c in stmt.cases) else "poly_if_elif")
        return _MatchRoute(kind=kind, hoist_types=hoist_types)
    kind = _match_strategy(stmt, analyzer)
    if kind is None:
        return None
    subject_rvalue = False
    subj = stmt.subject
    if isinstance(subj, TpyName):
        if subj.name not in declared:
            return None
        if (subj.name in pointers or subj.name in narrowed
                or subj.name in storage_tuple_locals):
            return None
    else:
        # Field/subscript LVALUE subjects (storage-form: value-variant
        # `std::get`, `auto&` bind) admit on the union and record tiers,
        # plus the pointer-repr O1 partition and the pointer-repr unguarded
        # optional chain (the `optional_to_ptr` lift, `auto` bind). The
        # scalar tiers and the guarded chain stay name-only.
        if kind not in ("switch_union", "if_elif_record", "guarded_record",
                        "optional_partition", "if_elif_optional"):
            return None
        if not _match_expr_subject_ok(subj, declared, pointers, narrowed,
                                      storage_tuple_locals):
            # Call/ctor F1-record RVALUE subjects admit on the unguarded
            # record tier: the subject materializes into an owned
            # dispatch-local (`auto __match_subject_N = <call>;`), captures
            # move out of it. Optional-typed rvalues stay out (an
            # `optional_to_ptr` lift on a temporary would dangle).
            if not (kind == "if_elif_record"
                    and isinstance(subj, TpyCall)
                    and is_rvalue_source(analyzer, subj)
                    and not isinstance(unwrap_readonly(stmt.subject_type),
                                       OptionalType)):
                return None
            subject_rvalue = True
        if (kind in ("optional_partition", "if_elif_optional")
                and not unwrap_readonly(stmt.subject_type).uses_pointer_repr()):
            return None
    hoist_types = _route_hoists(
        stmt, analyzer, declared, prescan, lc,
        in_branch=in_branch, in_loop=in_loop,
        nonvalue_ok=kind in ("if_elif_record", "guarded_record",
                             "if_elif_optional",
                             "if_elif_optional_guarded"))
    if hoist_types is None:
        return None
    if kind == "switch_union":
        u = unwrap_readonly(stmt.subject_type)
        union_route = _match_union_route(stmt, u)
        if u.needs_wrapper():
            # M4c: wrapper subjects are NAME-only (a field/subscript
            # source would compose `.value` over a member read -- out of
            # slice) and unguarded-tier only (the guarded emit's get
            # positions are not wrapper-aware).
            if (not isinstance(subj, TpyName)
                    or union_route != "switch_union"):
                return None
        return _MatchRoute(kind=kind, hoist_types=hoist_types,
                          union_route=union_route)
    if kind in ("if_elif_record", "guarded_record"):
        if not _f1_record(unwrap_readonly(stmt.subject_type), analyzer):
            return None
        return _MatchRoute(kind=kind, hoist_types=hoist_types,
                           subject_rvalue=subject_rvalue)
    if kind in ("optional_partition", "if_elif_optional",
                "if_elif_optional_guarded"):
        # Pointer-repr NAME subjects must be the admitted borrow form (the
        # tiers' null tests read the `T*` binding); an admitted
        # field/subscript source takes the `optional_to_ptr` lift instead
        # (`_lower_optional_subject`). Value-repr subjects take the O2
        # multi-arm dispatch or the chain, whose inner-shape rejects live
        # in the lowerer.
        if (isinstance(subj, TpyName)
                and unwrap_readonly(stmt.subject_type).uses_pointer_repr()
                and _optional_ptr_borrow_name(subj, declared, analyzer)
                is None):
            return None
        return _MatchRoute(kind=kind, hoist_types=hoist_types)
    return _MatchRoute(kind=kind, hoist_types=hoist_types)

def _select_match_route(
        stmt: TpyMatch, analyzer, declared: dict[str, TpyType],
        pointers: AbstractSet[str], narrowed: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str], prescan: _Prescan,
        lc: '_LowerCtx', *,
        in_branch: bool, in_loop: bool) -> _MatchRoute:
    """Select a match lowering strategy or reject at the lowering boundary."""
    route = _match_route(
        stmt, analyzer, declared, pointers, narrowed,
        storage_tuple_locals, prescan, lc,
        in_branch=in_branch, in_loop=in_loop)
    if route is None:
        raise ThirUnsupported("stmt.match")
    return route

def _scalar_bind_mode(bnode, declared: dict[str, TpyType]) -> str:
    """`_emit_binding`'s value-subject mode ternary, shared by every tier's
    whole-subject binding: pre-declared/hoisted names assign, free-copy
    scalars copy, everything else binds by reference."""
    return ("assign" if bnode.name in declared
            else "copy" if bnode.bind_by_value else "ref")


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
    # The AST renders every guard through gen_truthy_expr, so a non-bool guard
    # takes its type's truthiness wrap rather than rejecting.
    lowered = _lower_truthy(guard, lc, declared)
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
            "switch_enum", "switch_primitive", "if_elif", "if_elif_guarded",
            "switch_union")
            or (kind == "switch_union"
                and route.union_route == "guarded_union")
            or any(hk != "value" for _n, _t, hk in route.hoist_types)):
        # Dispatch-hook mode (a resumable MatchDispatch): the scalar tiers
        # and the unguarded union switch carry the skeleton hook at their
        # arm-body points; the guarded-union / record / optional tiers and
        # pointer/optional hoist kinds (frame-field pointer mechanics
        # unverified) stay their own rungs. VALUE-kind hoists are no-op
        # decls in a resumable -- every local is already a frame field --
        # so they admit with the decl suppressed below.
        raise ThirUnsupported("res.match_strategy")
    if kind != "switch_union":  # the union lowerers witness their route
        _witness(f"match.{kind}")
    predeclared = set(declared)
    hoist_decls: list[tuple[str, str]] = []
    hoist_kinds: dict[str, str] = {}
    for name, vtype, hkind in route.hoist_types:
        hoist_kinds[name] = hkind
        if hkind == "ptr":
            # Borrow-only pointer-local (`T* name;`): reads/writes deref via
            # `pointers`, reseats ride the hoisted-record arms.
            hoist_decls.append((name, f"{lc.render_type(vtype)}*"))
            lc.pointers.add(name)
            lc.branch_hoisted.add(name)
            declared[name] = vtype
            _witness("match.hoist_ptr_local")
            continue
        if hkind == "opt_storage":
            hoist_decls.append(_statements._optional_storage_hoist_entry(
                name, vtype, declared, lc))
            _witness("match.hoist_optional_storage")
            continue
        if hkind == "opt_ptr":
            # `T* name;` for a pointer-repr Optional binding: the declared
            # entry keeps the Optional so body reads classify as the
            # nullable borrow name (deref_check on unproven access).
            hoist_decls.append(
                (name, f"{lc.render_type(vtype.inner)}*"))
            lc.pointers.add(name)
            lc.branch_hoisted.add(name)
            declared[name] = vtype
            _witness("match.hoist_opt_ptr_local")
            continue
        declared[name] = vtype
        if arm_body_hooks:
            # Resumable hook mode: the frame struct already declares every
            # local, so the value hoist registers the name only -- no decl
            # line at the match site (the AST emits none either).
            _witness("match.hoist_value_frame")
            continue
        render_src = (_resolved_str_value(vtype, lc.analyzer)
                      or _resolved_bytes_value(vtype, lc.analyzer)
                      or vtype)
        hoist_decls.append((name, lc.render_type(render_src)))
    if hoist_decls:
        _witness("match.hoist_decl")
    if kind == "switch_union":
        if route.union_route == "guarded_union":
            return _lower_match_guarded_union(stmt, lc, declared, loc,
                                              pointers, hoist_decls,
                                              loop_depth=loop_depth)
        return _lower_match_union(stmt, lc, declared, loc, pointers,
                                  hoist_decls,
                                  loop_depth=loop_depth,
                                  arm_body_hooks=arm_body_hooks)
    if kind in ("poly_if_elif", "poly_guarded"):
        return _lower_match_poly(stmt, lc, declared, loc, pointers,
                                 hoist_decls, kind, loop_depth=loop_depth)
    if kind in ("if_elif_record", "guarded_record"):
        return _lower_match_record(stmt, lc, declared, loc, pointers,
                                   hoist_decls, kind, loop_depth=loop_depth,
                                   hoist_kinds=hoist_kinds,
                                   subject_rvalue=route.subject_rvalue)
    if kind == "optional_partition":
        return _lower_match_optional(stmt, lc, declared, loc, pointers,
                                     predeclared, hoist_decls,
                                     loop_depth=loop_depth)
    if kind in ("if_elif_optional", "if_elif_optional_guarded"):
        return _lower_match_optional_chain(stmt, lc, declared, loc, pointers,
                                           hoist_decls, kind,
                                           loop_depth=loop_depth,
                                           hoist_kinds=hoist_kinds)
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
        lit_facts = {n: f for n, f in facts.items()
                     if isinstance(f, LiteralType)}
        if facts and not allow_facts and not lit_facts:
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
            mode = _scalar_bind_mode(bnode, declared)
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
            # walks (already seam-routed leaves). An ASSIGN-mode binding is
            # the frame-field write (`v = __match_subject_N;`) both paths
            # emit before the body point -- admitted; the copy/ref modes
            # declare arm-block LOCALS, whose frame duality is unmirrored.
            # Literal facts stay rejected here: the skeleton's BB walk
            # would need the fact scoping the hooks do not carry.
            if lit_facts:
                raise ThirUnsupported("res.match_strategy")
            if binding is not None and binding.mode != "assign":
                raise ThirUnsupported("res.match_binding")
            entry = THIRMatchArmEntry(
                body=(), loc=case.loc, binding=binding, guard=guard,
                body_key=id(case.body))
        else:
            # Literal-subject arms register the facts for the body walk:
            # the AST consumes them for dead-branch folds, which THIR does
            # not mirror -- the compare/membership fence in expression
            # lowering rejects the foldable reads instead.
            saved_lf = lc.literal_facts
            if lit_facts:
                _witness("match.literal_facts")
                lc.literal_facts = {**saved_lf, **lit_facts}
            try:
                entry = THIRMatchArmEntry(
                    body=_statements._lower_scoped_stmts(
                        case.body, lc, arm_declared,
                        branch_decls_ok=True, loop_depth=loop_depth),
                    loc=case.loc, binding=binding, guard=guard)
            finally:
                lc.literal_facts = saved_lf
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

@dataclass(frozen=True)
class _SubAcc:
    """`_lower_field_subpatterns`' recursion state -- two independent
    compositions plus the binding base, mirroring the AST split:
    `cond_pre`/`cond_suf` compose CONDITIONS around the tier's runtime base
    (`_record_field_conditions` threads its `subject_expr` the same way),
    while `base`/`bind_pre`/`bind_suf` compose BINDINGS relative to the
    last bound name (`_gen_match_field_bindings` threads `case_var`).
    `bind_suf` doubles as the plain field path since the last base switch
    -- the piece the `__field_` temp NAME derivation needs."""
    cond_pre: str = ""
    cond_suf: str = ""
    base: 'str | None' = None
    bind_pre: str = ""
    bind_suf: str = ""


def _lower_field_subpatterns(pattern: TpyClassPattern,
                             declared: dict[str, TpyType],
                             arm_declared: dict[str, TpyType],
                             lc: _LowerCtx, *, from_case_var: bool,
                             _acc: '_SubAcc | None' = None,
                             _out: 'tuple | None' = None,
                             ) -> 'tuple[tuple, tuple]':
    """One class pattern's keyword sub-patterns -> (field_conds,
    field_bindings), in keyword order like `_record_field_conditions` /
    `_gen_match_field_bindings`. Captures join the arm scope typed to the
    field (the AST's `_record_capture_type` var_types registration).

    Nested sub-patterns recurse with two independent compositions, exactly
    the AST split: CONDITIONS always compose around the tier's runtime base
    (`_record_field_conditions` recursion -- a union-field guard wraps
    `std::get<T>(...)` INTO the pair), while BINDINGS switch base to the
    guard's `__field_` extraction temp or an `as` name
    (`_gen_match_field_bindings` recursion); `_SubAcc` carries both.
    `_out` shares the two ordered row lists across recursion levels so the
    interleaving matches the AST's single keyword walk."""
    top = _out is None
    if top:
        _out = ([], [])
        _acc = _SubAcc()
    field_conds, field_bindings = _out
    cpre, csuf, base, bpre, bsuf = (_acc.cond_pre, _acc.cond_suf, _acc.base,
                                    _acc.bind_pre, _acc.bind_suf)
    for fname, sub in pattern.keywords:
        as_node = sub if isinstance(sub, TpyAsPattern) else None
        inner = sub.pattern if as_node is not None else sub
        if isinstance(inner, TpyLiteralPattern):
            assert as_node is None, \
                "ineligible as-literal sub-pattern reached lowering"
            pair = _match_field_cond(pattern, fname, inner.value, lc.analyzer)
            assert pair is not None, "ineligible field cond reached lowering"
            _witness("match.field_none" if inner.value is None
                     else "match.field_cond")
            field_conds.append((pair[0] + cpre, csuf + pair[1]))
        elif isinstance(inner, TpyCapturePattern):
            if inner.name in lc.pointers:
                # A capture the match hoisted into a POINTER local binds by
                # address (`q = &(__match_subject_1.inner);`) -- its own rung.
                # Beside node construction, where every rejection check lives:
                # the hoist registers `lc.pointers` only after the arm gate has
                # read its snapshot, so the gate could not see this anyway.
                raise ThirUnsupported("match.field_bind_ptr_hoist")
            mode = ("assign" if inner.name in declared
                    else "copy" if inner.bind_by_value else "ref")
            _witness("match.field_bind")
            field_bindings.append(THIRMatchBinding(
                name=inner.name, mode=mode, from_case_var=from_case_var,
                subject_prefix=bpre, subject_suffix=f"{bsuf}.{fname}",
                base_name=base))
            ft = _match_record_field_type(pattern, fname, lc.analyzer)
            assert ft is not None, "ineligible field capture reached lowering"
            arm_declared[inner.name] = ft
        elif isinstance(inner, TpyClassPattern):
            if inner.is_union_field_guard:
                t_cpp = lc.render_type(unwrap_readonly(inner.resolved_type))
                _witness("match.field_union_guard")
                field_conds.append(
                    (f"std::holds_alternative<{t_cpp}>({cpre}",
                     f"{csuf}.{fname})"))
                n_cpre = f"std::get<{t_cpp}>({cpre}"
                n_csuf = f"{csuf}.{fname})"
                a_pre = f"std::get<{t_cpp}>({bpre}"
                a_suf = f"{bsuf}.{fname})"
            else:
                n_cpre, n_csuf = cpre, f"{csuf}.{fname}"
                a_pre, a_suf = bpre, f"{bsuf}.{fname}"
            if as_node is not None:
                mode = ("assign" if as_node.name in declared
                        else "copy" if as_node.bind_by_value else "ref")
                _witness("match.field_guard_as")
                field_bindings.append(THIRMatchBinding(
                    name=as_node.name, mode=mode, from_case_var=from_case_var,
                    subject_prefix=a_pre, subject_suffix=a_suf,
                    base_name=base))
                bound_t = (inner.resolved_type
                           or _match_field_type_subst(pattern, fname,
                                                      lc.analyzer))
                assert bound_t is not None, \
                    "ineligible as-bind reached lowering"
                arm_declared[as_node.name] = bound_t
                n_acc = _SubAcc(n_cpre, n_csuf, as_node.name)
            elif inner.is_union_field_guard and inner.keywords:
                # The AST draws the `__field_{parent}_{f}` extraction temp
                # whenever a keyword-bearing union guard has no `as` name;
                # its spelled name derives at emit from the runtime base
                # PLUS the plain path walked since the last base switch
                # (`alias_path` -- the AST's case_var threading).
                _witness("match.field_alias")
                field_bindings.append(THIRMatchBinding(
                    name=fname, mode="field_alias",
                    from_case_var=from_case_var,
                    subject_prefix=a_pre, subject_suffix=a_suf,
                    base_name=base, alias_path=bsuf))
                n_acc = _SubAcc(n_cpre, n_csuf, fname)
            else:
                n_acc = _SubAcc(n_cpre, n_csuf, base, a_pre, a_suf)
            if inner.keywords:
                _witness("match.field_nested")
                _lower_field_subpatterns(
                    inner, declared, arm_declared, lc,
                    from_case_var=from_case_var, _acc=n_acc, _out=_out)
        else:
            assert (isinstance(inner, TpyWildcardPattern)
                    and as_node is None), \
                "ineligible field sub-pattern reached lowering"
    if not top:
        return (), ()
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
                        kind: str, *, loop_depth: int = 0,
                        hoist_kinds: 'dict[str, str] | None' = None,
                        subject_rvalue: bool = False) -> THIRMatch:
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
    subj_type = (declared.get(stmt.subject.name)
                 if isinstance(stmt.subject, TpyName)
                 else stmt.subject_type)
    if hoist_kinds is None:
        hoist_kinds = {}
    # Mirrors the AST's `_subject_is_pointer` over the routed subject slice:
    # the only admitted name subject rendering as a `T*` is the pointer-repr
    # Optional borrow name (pointer/narrowed/tuple-alias names reject at the
    # route). A capture aliasing it assigns the pointer directly -- `&subject`
    # would yield `T**`.
    subject_is_ptr = (isinstance(stmt.subject, TpyName)
                      and _optional_ptr_borrow_name(
                          stmt.subject, declared, lc.analyzer) is not None)
    arms: list[THIRMatchArm] = []
    always_arms = 0
    for i, case in enumerate(stmt.cases):
        if not _record_arm_ok(
                case, lc.analyzer, declared, pointers,
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                subj_type):
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
            hkind = hoist_kinds.get(bnode.name)
            if hkind == "ptr":
                # `_emit_binding`'s declared pointer-local arm: a pointer-repr
                # subject assigns the pointer directly, a value lvalue subject
                # aliases via address-of. An rvalue subject never reaches this
                # form (sema records no borrow fact for it) -- defensive.
                if subject_rvalue:
                    raise ThirUnsupported("match.capture_shape", detail=True)
                mode = "assign" if subject_is_ptr else "assign_addr"
            elif hkind == "opt_storage":
                # The owned optional slot moves from the MATERIALIZED rvalue
                # subject only; an lvalue subject or a guarded arm (later
                # arms could re-read the moved-from subject) stays AST.
                if not subject_rvalue or case.guard is not None:
                    raise ThirUnsupported("match.capture_shape", detail=True)
                mode = "assign_move"
            elif bnode.name in declared:
                mode = "assign"
            else:
                mode = "copy" if bnode.bind_by_value else "ref"
            _witness(f"match.bind_{mode}")
            binding = THIRMatchBinding(name=bnode.name, mode=mode)
            if hkind is None:
                arm_declared[bnode.name] = subj_type
        guard = None
        if case.guard is not None:
            _witness("match.guard_arm")
            guard = _lower_match_guard(case.guard, lc, arm_declared)
        arms.append(THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
            body=_statements._lower_scoped_stmts(
                case.body, lc, arm_declared,
                branch_decls_ok=True, loop_depth=loop_depth),
            loc=case.loc, binding=binding, guard=guard,
            field_conds=field_conds, field_bindings=field_bindings,
            or_conds=or_conds),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    if subject_rvalue:
        _witness("match.subject_rvalue")
    return THIRMatch(
        strategy=kind,
        subject=_lower_subject_expr(stmt.subject, lc, declared),
        subject_ref=not subject_rvalue,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        loc=loc,
    )

def _poly_cast_pair(cpp_type: str, narrowed_t, source_inner, depth: int,
                    is_const: bool, analyzer) -> tuple[str, str]:
    """The `narrow_cast_rhs` render as a (prefix, suffix) pair around the
    emit-time subject spelling (`__match_subject_N` is numbered at emit).
    The cast arg -- `&subject`, or `&(subject.__deref__()...)` for an
    owning-wrapper deref view -- composes into the shared chokepoint's
    output, split on a placeholder no type render can contain."""
    mark = "\x00"
    rhs = narrow_cast_rhs(cpp_type, narrowed_t, source_inner, mark,
                          is_const=is_const, analyzer=analyzer)
    pre, suf = rhs.split(mark)
    if depth > 0:
        return f"{pre}&(", f"{'.__deref__()' * depth}){suf}"
    return f"{pre}&", suf


def _lower_match_poly(stmt: TpyMatch, lc: _LowerCtx,
                      declared: dict[str, TpyType], loc,
                      pointers: AbstractSet[str],
                      hoist_decls: 'list[tuple[str, str]]',
                      kind: str, *, loop_depth: int = 0) -> THIRMatch:
    """Lower a polymorphic-dispatch `match` (poly_if_elif / poly_guarded),
    mirroring `_gen_match_polymorphic`: the subject bound once, per class
    arm the C++17 if-init cast (`Sub* __mpoly_i = <cast>`) plus the
    `Sub& __case_i = *__mpoly_i;` alias, arm bodies lowered with subject
    reads renamed to the alias (the union tier's mechanic over the AST's
    `_poly_narrow_save`); or-pattern arms are `||`-joined null tests
    (type-test only), wildcard/capture the chain's else. The guarded tier
    mirrors `_gen_match_polymorphic_guarded`: standalone-if arms, field
    conditions composed around the ALIAS, guards lowered inside the
    narrowed window, `goto __match_end_N` (the second counter draw) and
    the INDENTED end label. Casts render through the shared
    `narrow_cast_rhs` chokepoint, keeping dynamic_cast vs dyn_adapter_cast
    and const-ness in lockstep with the AST emit."""
    analyzer = lc.analyzer
    if lc.resumable_leaf_mode:
        # The AST refuses poly dispatch inside a resumable frame region
        # (no arm routing through the state machine) -- stay AST.
        raise ThirUnsupported("match.poly_resumable", detail=True)
    subj = stmt.subject
    subj_name = subj.name if isinstance(subj, TpyName) else None
    # A reference-type param's declared entry keeps its Ref wrapper; the
    # AST's `lookup_var_type` sees the bare type -- peel to match.
    var_decl = unwrap_ref_type(unwrap_send_sync(
        declared[subj_name] if subj_name is not None else stmt.subject_type))
    if polymorphic_source_is_pointer(var_decl):
        # Pointer-repr sources (Ptr / pointer-repr Optional) spell the cast
        # arg bare -- an excluded rung, like the dyn-isinstance if arm.
        raise ThirUnsupported("match.poly_ptr_source", detail=True)
    source_inner = polymorphic_source_inner(var_decl, analyzer.registry)
    depth = 0
    if source_inner is None:
        deref = deref_dispatch_inner(var_decl, analyzer.type_ops,
                                     analyzer.registry)
        if deref is None:
            raise ThirUnsupported("match.poly_source", detail=True)
        source_inner, depth = deref
    is_const = _poly_subject_const(subj, lc)
    arms: list[THIRMatchArm] = []
    for i, case in enumerate(stmt.cases):
        parts = _match_arm_parts(case)
        if parts is None:
            raise ThirUnsupported("match.poly_arm_shape", detail=True)
        test, bnode = parts
        facts = case.type_facts or {}
        if any(k != subj_name or isinstance(ft, LiteralType)
               for k, ft in facts.items()):
            # Literal facts feed the AST's dead-branch elimination; facts
            # on other names come from compound patterns -- both unmirrored.
            raise ThirUnsupported("match.poly_facts", detail=True)
        if bnode is not None and (
                bnode.name in pointers
                or bnode.name in lc.narrow.narrowed
                or bnode.name in lc.storage_tuple_locals):
            raise ThirUnsupported("match.poly_bind_name", detail=True)
        arm_declared = dict(declared)
        entry_extra: dict = {}
        bind_from_alias = False
        bind_base_type: 'TpyType | None' = None
        with lc.branch_scope():
            if isinstance(test, TpyClassPattern):
                narrowed_t = test.resolved_type
                if narrowed_t is None or test.positional:
                    raise ThirUnsupported("match.poly_pattern", detail=True)
                cpp_type = lc.render_type(narrowed_t)
                const_pfx = "const " if is_const else ""
                pre, suf = _poly_cast_pair(cpp_type, narrowed_t, source_inner,
                                           depth, is_const, analyzer)
                ref_local = f"__case_{i}"
                entry_extra["poly_cast"] = (
                    f"{const_pfx}{cpp_type}* __mpoly_{i} = {pre}", suf)
                entry_extra["poly_ref_decl"] = (
                    f"{const_pfx}{cpp_type}& {ref_local} = *__mpoly_{i};")
                entry_extra["case_alias"] = ref_local
                if not _match_keywords_ok(
                        test, analyzer, pointers, lc.narrow.narrowed.keys(),
                        lc.storage_tuple_locals, dict(arm_declared),
                        allow_conds=(kind == "poly_guarded")):
                    raise ThirUnsupported("match.poly_fields", detail=True)
                field_conds, field_bindings = _lower_field_subpatterns(
                    test, declared, arm_declared, lc, from_case_var=True)
                if kind == "poly_if_elif":
                    assert not field_conds, \
                        "field condition reached the poly chain tier"
                entry_extra["field_conds"] = field_conds
                entry_extra["field_bindings"] = field_bindings
                bind_from_alias = True
                bind_base_type = narrowed_t
                if subj_name is not None:
                    lc.narrow.narrowed[subj_name] = ref_local
                    arm_declared[subj_name] = narrowed_t
            elif isinstance(test, TpyOrPattern):
                if bnode is not None:
                    # The AST binds an or-arm `as` at the base subject type;
                    # sema types it per-alternative -- an unmirrored seam.
                    raise ThirUnsupported("match.poly_or_bind", detail=True)
                conds: list[tuple[str, str]] = []
                for alt in test.patterns:
                    if (not isinstance(alt, TpyClassPattern)
                            or alt.resolved_type is None or alt.positional
                            or alt.keywords):
                        raise ThirUnsupported("match.poly_or_alt",
                                              detail=True)
                    alt_cpp = lc.render_type(alt.resolved_type)
                    pre, suf = _poly_cast_pair(
                        alt_cpp, alt.resolved_type, source_inner, depth,
                        is_const, analyzer)
                    conds.append((f"({pre}", f"{suf} != nullptr)"))
                _witness("match.poly_or_arm")
                entry_extra["poly_or_conds"] = tuple(conds)
            elif test is None:
                if kind == "poly_if_elif" and i != len(stmt.cases) - 1:
                    # The chain's always-match arm is the final `} else {`.
                    raise ThirUnsupported("match.poly_wildcard_pos",
                                          detail=True)
                bind_base_type = (declared[subj_name]
                                  if subj_name is not None
                                  else stmt.subject_type)
            else:
                raise ThirUnsupported("match.poly_pattern", detail=True)
            binding = None
            if bnode is not None:
                mode = _scalar_bind_mode(bnode, declared)
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=bind_from_alias)
                arm_declared[bnode.name] = bind_base_type
            guard = None
            if case.guard is not None:
                _witness("match.guard_arm")
                guard = _lower_match_guard(case.guard, lc, arm_declared)
            body = _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                branch_decls_ok=True, loop_depth=loop_depth)
        arms.append(THIRMatchArm(entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding, guard=guard,
            **entry_extra),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy=kind,
        subject=_lower_expr(subj, lc, declared),
        subject_ref=_match_subject_is_lvalue(subj),
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        loc=loc,
    )


def _lower_subject_expr(subj: TpyExpr, lc: _LowerCtx,
                        declared: dict[str, TpyType]) -> THIRExpr:
    """The union/record-tier subject render. An admitted field/subscript
    lvalue chain lowers PRECHECKED: the route already vetted the shape
    (`_match_expr_subject_ok`), and the raw storage read is exactly what
    `auto& __match_subject_N = <subj>;` binds -- the result-type ladders
    gate sink positions, not the subject bind."""
    if isinstance(subj, TpyFieldAccess):
        return _lower_expr(subj, lc, declared, field_prechecked=True)
    if isinstance(subj, TpySubscript):
        return _lower_expr(subj, lc, declared, subscript_prechecked=True)
    return _lower_expr(subj, lc, declared)


def _lower_optional_subject(stmt: TpyMatch, lc: _LowerCtx,
                            declared: dict[str, TpyType],
                            ) -> 'tuple[THIRExpr, bool]':
    """The O1 subject binding: a borrow-form name passes bare (`auto&`
    reads the `T*` binding); a storage-form field/subscript source lifts
    via `optional_to_ptr` and binds by value (`auto`) -- the match sibling
    of the var-decl auto-lift (`_gen_match_dispatch`'s storage-form
    branch)."""
    if isinstance(stmt.subject, TpyName):
        return _lower_expr(stmt.subject, lc, declared), True
    if not isinstance(stmt.subject, TpyFieldAccess):
        # Subscript optional sources have no witness; the field lift's
        # `_lower_field_source` is field-only.
        raise ThirUnsupported("match.optional_subject_shape", detail=True)
    # An Optional-typed intermediate link rejects inside
    # `_lower_field_source` (`field.opt_receiver` -- the AST draws a
    # deref_optional_check there that the lift's prechecked recursion
    # cannot mirror).
    _witness("match.optional_subject_lift")
    lowered = _lower_field_source(stmt.subject, lc, declared)
    return THIRFormConvert(result_type=stmt.subject_type, value=lowered,
                           form=Form.BORROW,
                           loc=getattr(stmt.subject, "loc", None)), False


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
                ncase.body, lc, dict(declared),
                branch_decls_ok=True, loop_depth=loop_depth),
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
    with lc.branch_scope():
        if bnode is not None:
            lc.forbidden_writes.add(bnode.name)
        body = _statements._lower_stmts(
            case.body, lc, arm_declared, in_branch=True,
            branch_decls_ok=True, loop_depth=loop_depth)
    arm = THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
        body=body,
        loc=case.loc, binding=binding),))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    o1_subject, o1_ref = _lower_optional_subject(stmt, lc, declared)
    return THIRMatch(
        strategy="optional_partition",
        subject=o1_subject,
        subject_ref=o1_ref,
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
                ncase.body, lc, dict(declared),
                branch_decls_ok=True, loop_depth=loop_depth),
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
                inner_type):
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
            mode = _scalar_bind_mode(bnode, declared)
            _witness(f"match.bind_{mode}")
            _witness("match.optional_inner_bind")
            binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                       from_case_var=True)
            arm_declared[bnode.name] = inner_type
        with lc.branch_scope():
            if bnode is not None:
                lc.forbidden_writes.add(bnode.name)
            body = _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                branch_decls_ok=True, loop_depth=loop_depth)
        arms.append(THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding,
            field_conds=field_conds, field_bindings=field_bindings,
            or_conds=or_conds),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    o1_subject, o1_ref = _lower_optional_subject(stmt, lc, declared)
    return THIRMatch(
        strategy="optional_partition",
        subject=o1_subject,
        subject_ref=o1_ref,
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
                ncase.body, lc, dict(declared),
                branch_decls_ok=True, loop_depth=loop_depth),
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
                                loop_depth: int = 0,
                                hoist_kinds: 'dict[str, str] | None' = None,
                                ) -> THIRMatch:
    """Lower a chain-optional `match` (the non-partitioned Optional subject):
    per arm `_gen_match_optional_cond`'s pre-rendered condition groups (the
    null / has-value tests spelled per repr, compares and field conditions
    over the `(*subj)` deref, or-alternatives ||-joined -- see
    `THIRMatchArmEntry.opt_conds`) and `_emit_optional_arm_bindings`' binding
    set: class-arm field captures against the deref, the whole-subject
    capture/`as` binding against the deref (`from_case_var`) or -- sema's
    binds_full_optional -- the full Optional, which registers the name in
    the SCALAR-kind value-opt binding so its body reads ride the param renders
    (the AST's `ctx.var_types` registration). Optional-subject arms carry no
    `type_facts` (sema narrows via `narrowed_types`, baked into body node
    types), so any fact here is an unmirrored shape. The unguarded tier keeps
    the plain chain's single-trailing-always-arm rule; the guarded tier's
    standalone blocks accept always arms anywhere."""
    subj_type = unwrap_readonly(stmt.subject_type)
    uses_ptr = subj_type.uses_pointer_repr()
    inner_type = subj_type.inner
    if hoist_kinds is None:
        hoist_kinds = {}
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
            if any(lc.value_opt_bindings.get(nm) is ValueOptKind.SCALAR
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
                        and lc.value_opt_bindings.get(bnode.name)
                        is ValueOptKind.SCALAR)):
                raise ThirUnsupported("stmt.match")
            hkind = hoist_kinds.get(bnode.name)
            if hkind not in (None, "value") and not (
                    hkind == "opt_ptr" and binds_full):
                # A non-value hoisted capture on this tier is routed only as
                # the full-Optional pointer bind; inner-binding hoisted
                # captures (`case Box() as bb:` leaked) stay AST. Value
                # hoists keep the existing declared-assign paths.
                raise ThirUnsupported("stmt.match")
            if binds_full and hkind == "opt_ptr" and uses_ptr:
                # The hoisted `T* q;` binds the pointer subject whole
                # (`q = __match_subject_N;` -- _emit_binding's declared
                # pointer-local arm over a pointer-repr subject). The
                # declared Optional entry keeps body reads on the nullable
                # borrow model (deref_check on unproven access).
                _witness("match.bind_assign")
                binding = THIRMatchBinding(name=bnode.name, mode="assign",
                                           from_case_var=False)
            elif binds_full:
                # A full-Optional binding needs the value-opt local renders;
                # only the scalar family has them (pointer-repr subjects
                # without the hoist bind the raw `T*`, an unmirrored shape).
                if (uses_ptr
                        or _value_opt_scalar(subj_type, lc.analyzer) is None):
                    raise ThirUnsupported("stmt.match")
                mode = _scalar_bind_mode(bnode, declared)
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=False)
                arm_declared[bnode.name] = subj_type
                _witness("match.optional_full_bind")
                lc.value_opt_bindings[bnode.name] = ValueOptKind.SCALAR
            else:
                mode = _scalar_bind_mode(bnode, declared)
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=True)
                arm_declared[bnode.name] = inner_type
        with lc.branch_scope():
            if binding is not None and binding.mode != "assign":
                lc.forbidden_writes.add(binding.name)
            guard = None
            if case.guard is not None:
                _witness("match.guard_arm")
                guard = _lower_match_guard(case.guard, lc, arm_declared)
            body = _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                branch_decls_ok=True, loop_depth=loop_depth)
        arms.append(THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding, guard=guard,
            field_bindings=field_bindings, opt_conds=opt_conds),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    if not isinstance(stmt.subject, TpyName):
        # An admitted storage-form field source takes the O1 partition's
        # `optional_to_ptr` lift and binds by value (`auto`).
        subject, subject_ref = _lower_optional_subject(stmt, lc, declared)
    else:
        subject = _lower_expr(stmt.subject, lc, declared,
                              allow_whole_optional=True)
        subject_ref = True
    return THIRMatch(
        strategy=kind,
        subject=subject,
        subject_ref=subject_ref,
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
                or lc.value_opt_bindings.get(bnode.name)
                is ValueOptKind.SCALAR):
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
            case.body, lc, arm_declared,
            branch_decls_ok=True, loop_depth=loop_depth)
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
                case.body, lc, arm_declared,
                branch_decls_ok=True, loop_depth=loop_depth)
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
            case.body, lc, arm_declared,
            branch_decls_ok=True, loop_depth=loop_depth)
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
                       loop_depth: int = 0,
                       arm_body_hooks: bool = False) -> THIRMatch:
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
    if u.needs_wrapper():
        _witness("match.union_wrapper_value")
    members = _union_index_members(u)
    if members is None:
        raise ThirUnsupported("stmt.match")
    subj_name = (stmt.subject.name if isinstance(stmt.subject, TpyName)
                 else None)
    subj_type = (declared.get(subj_name) if subj_name is not None
                 else stmt.subject_type)
    arms: list[THIRMatchArm] = []
    seen: set[int] = set()
    for i, case in enumerate(stmt.cases):
        if not _union_arm_ok(
                case, lc.analyzer, declared, pointers,
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                subj_name, subj_type, members, seen):
            raise ThirUnsupported("stmt.match")
        test, bnode = _match_arm_parts(case)
        facts = case.type_facts or {}
        if arm_body_hooks:
            # Hook mode: the arm body lowers as skeleton-walked BB leaves
            # under the arm's stamped entry_narrowings (the `__case_{i}`
            # alias env in `_resume_narrow_envs` mirrors the extraction
            # this tier draws below). Field sub-patterns and or-bind blocks
            # declare case-block locals the BB walk can't see; whole-subject
            # bindings are their own rung, like the scalar tiers.
            if isinstance(test, TpyClassPattern) and test.keywords:
                raise ThirUnsupported("res.match_strategy")
            if (isinstance(test, TpyOrPattern)
                    and any(isinstance(alt, TpyClassPattern) and alt.keywords
                            for alt in test.patterns)):
                raise ThirUnsupported("res.match_strategy")
            if bnode is not None:
                raise ThirUnsupported("res.match_binding")
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
                with lc.branch_scope():
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
                        branch_decls_ok=True, loop_depth=loop_depth)
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
        with lc.branch_scope():
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
                mode = _scalar_bind_mode(bnode, declared)
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=member is not None)
                arm_declared[bnode.name] = (member if member is not None
                                            else subj_type)
            body = (() if arm_body_hooks else _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                branch_decls_ok=True, loop_depth=loop_depth))
        arms.append(THIRMatchArm(labels=labels, entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding,
            variant_index=variant_index, case_alias=case_alias,
            field_bindings=field_bindings,
            body_key=id(case.body) if arm_body_hooks else None),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="switch_union",
        subject=_lower_subject_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        # A field/subscript subject stores VALUE-variant regardless of the
        # type's primary repr (`is_ptr_variant_source`'s field/subscript
        # arms). A NAME's verdict is its BINDING's, not its type's -- the
        # gate above rejected the bindings THIR cannot classify.
        is_ptr_variant=(_narrow_subject_is_ptr(subj_name, u, lc)
                        if subj_name is not None else False),
        wrapper_value=u.needs_wrapper(),
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
    subj_name = (stmt.subject.name if isinstance(stmt.subject, TpyName)
                 else None)
    subj_type = (declared.get(subj_name) if subj_name is not None
                 else stmt.subject_type)

    type_arms: dict[int, list] = {i: [] for i in range(n)}
    for case in stmt.cases:
        if not _guarded_union_arm_ok(
                case, lc.analyzer, declared, pointers,
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                subj_name, subj_type, members):
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
        with lc.branch_scope():
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
                mode = _scalar_bind_mode(bnode, declared)
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=kind == "class")
                arm_declared[bnode.name] = (member if kind == "class"
                                            else subj_type)
            guard = None
            if case.guard is not None:
                _witness("match.guard_arm")
                # The guard-only forbidden_reads must not survive into the
                # arm body -- an inner scope pops them after the guard.
                with lc.branch_scope():
                    if subj_name is not None:
                        lc.forbidden_reads.add(subj_name)
                    if field_conds and pattern is not None:
                        lc.forbidden_reads.update(
                            _match_pattern_captures(pattern))
                    guard = _lower_match_guard(case.guard, lc, arm_declared)
            body = _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                branch_decls_ok=True, loop_depth=loop_depth)
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
        subject=_lower_subject_expr(stmt.subject, lc, declared),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        # Field/subscript subjects store VALUE-variant (the unguarded
        # tier's fold, mirrored -- `is_ptr_variant_source`); a NAME reads
        # its BINDING's verdict, the gate above having rejected the
        # bindings THIR cannot classify.
        is_ptr_variant=(_narrow_subject_is_ptr(subj_name, u, lc)
                        if subj_name is not None else False),
        loc=loc,
    )


def _lower_overload_folded_match(stmt: TpyMatch, lc, declared, loc, *,
                                 in_branch: bool, loop_depth: int):
    """Mirror of _gen_match_overload_specialized's witnessed slice: a match
    on a union param narrowed to a concrete per-@overload-stub type folds to
    the matching arm -- capture field bindings (`auto`/`auto&` name =
    subject.field) followed by the arm body, spliced flat. The match stmt's
    loc rides the block (its `// match ...` source comment emits), and the
    fold burns one `__match_subject_N` counter slot exactly like the AST's
    `gen_match` pre-dispatch bump.

    Returns None when the AST guard would not take the fold (non-union
    subject / non-name subject / subject not narrowed) -- the regular match
    routes take over. Un-mirrored fold shapes reject: guards (the AST fold
    silently drops them), literal/value/nested sub-patterns, as-names,
    whole-subject captures, non-value-typed field captures, and by-ref
    capture binds."""
    if not isinstance(stmt.subject, TpyName) or lc.overload_narrowing is None:
        return None
    subject_type = (unwrap_readonly(stmt.subject_type)
                    if stmt.subject_type is not None else None)
    if not isinstance(subject_type, UnionType):
        return None
    concrete = lc.overload_narrowing.get(stmt.subject.name)
    if concrete is None:
        return None
    subject_name = stmt.subject.name

    def _reject(detail: str):
        note_detail(detail)
        raise ThirUnsupported(stmt_reject_reason(stmt))

    def _splice(pattern: TpyClassPattern, case) -> 'THIRFoldedBlock':
        bind_modes = {n.name: n.bind_by_value
                      for n in iter_capture_bindings(case.pattern)}
        binds: list = []
        for field_name, sub_pattern in pattern.keywords:
            if isinstance(sub_pattern, TpyWildcardPattern):
                continue
            if not isinstance(sub_pattern, TpyCapturePattern):
                # Literal sub-patterns silently DROP their field condition
                # on the AST fold path; nested class/as sub-patterns are
                # un-mirrored renders. All fall back.
                _reject("match.overload_fold_subpattern")
            ft = _match_record_field_type(pattern, field_name, lc.analyzer)
            if ft is None or not ft.is_value_type():
                # A non-value capture aliases record storage (`auto&`) --
                # the body's reads would need the alias binding class.
                _reject("match.overload_fold_capture")
            by_value = bind_modes.get(sub_pattern.name)
            if by_value is None:
                _reject("match.overload_fold_bind_mode")
            if not by_value and _statements._body_writes_name(
                    case.body, sub_pattern.name, match_binds=False):
                # The AST's `auto&` capture would write through to the
                # field; reads-only bodies render identically either way.
                # match_binds=False mirrors sema: a nested match REBINDS the
                # name without writing through this arm's alias.
                _reject("match.overload_fold_ref_write")
            if (sub_pattern.name in declared
                    or sub_pattern.name in lc.prescan.hoisted):
                # Pre-declared/hoisted targets take the assignment forms.
                _reject("match.overload_fold_predeclared")
            binds.append(THIRMatchFoldBind(
                name_cpp=escape_cpp_name(sub_pattern.name),
                source_cpp=f"{subject_name}.{field_name}",
                by_value=bool(by_value),
                no_source_comment=True))
            declared[sub_pattern.name] = ft
        body = _statements._lower_stmts(case.body, lc, declared,
                                        in_branch=in_branch,
                                        loop_depth=loop_depth)
        return THIRFoldedBlock(stmts=tuple(binds) + body, loc=loc,
                               burns_match_counter=True)

    for case in stmt.cases:
        if case.guard is not None:
            _reject("match.overload_fold_guard")
        pattern = case.pattern
        if isinstance(pattern, TpyAsPattern):
            _reject("match.overload_fold_as")
        if isinstance(pattern, TpyClassPattern):
            if pattern.resolved_type is not None \
                    and pattern.resolved_type == concrete:
                return _splice(pattern, case)
        elif isinstance(pattern, TpyOrPattern):
            for alt in pattern.patterns:
                if (isinstance(alt, TpyClassPattern)
                        and alt.resolved_type == concrete):
                    return _splice(alt, case)
        elif isinstance(pattern, TpyWildcardPattern):
            body = _statements._lower_stmts(case.body, lc, declared,
                                            in_branch=in_branch,
                                            loop_depth=loop_depth)
            return THIRFoldedBlock(stmts=body, loc=loc,
                                   burns_match_counter=True)
        else:
            # Capture patterns bind the whole subject; value/literal
            # patterns compare it -- both un-mirrored on this tier.
            _reject("match.overload_fold_pattern")
    # No arm matched: the AST fold raises AssertionError here; falling back
    # keeps that failure on the emission path.
    _reject("match.overload_fold_arm")
