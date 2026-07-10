"""Match gate + lowering: the _match_strategy tier discriminant shared by
_match_eligible (gate) and _lower_match (emit mirror), with the per-tier
arm walkers (scalar switch/chain, record, optional-partition, union,
guarded-union).
"""

from __future__ import annotations
from dataclasses import fields as dataclass_fields
from ...parse.nodes import (
    TpyAsPattern,
    TpyAssign,
    TpyAugAssign,
    TpyCall,
    TpyCapturePattern,
    TpyClassPattern,
    TpyDelItem,
    TpyDelVar,
    TpyExpr,
    TpyFieldAccess,
    TpyLiteralPattern,
    TpyMatch,
    TpyMethodCall,
    TpyName,
    TpyOrPattern,
    TpyStmt,
    TpySubscript,
    TpyTupleUnpack,
    TpyValuePattern,
    TpyVarDecl,
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
from ...type_def_registry import (
    is_bool_type,
    is_fixed_int_type,
)
from ...codegen_cpp.forms import is_ptr_variant_union
from ...codegen_cpp.context import cpp_string_literal_expr
from ...codegen_cpp.match import MatchGenerator, partition_optional_cases
from ...codegen_cpp.string_dispatch import STRING_SWITCH_THRESHOLD
from ...liveness import stmts_terminate
from ..faces import witness as _witness
from ..nodes import (
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
)
from .context import (
    _LowerCtx,
    _Prescan,
    _WalkState,
)
from .expr_gates import (
    _expr_eligible,
)
from .expressions import (
    _lower_expr,
)
from . import statements as _statements

def _match_strategy(stmt: TpyMatch, analyzer) -> 'str | None':
    """The routed-tier discriminant -- the SINGLE complete mirror of
    _gen_match_dispatch's scalar routing, shared by the gate and lowering
    (an M3b bug came from each holding its own copy of the guard
    promotion; when both sides must agree on a routing fact, one function
    computes it and the byte-diff is only the backstop): a registered enum
    subject -> switch_enum, a plain fixed-int subject -> switch_primitive,
    and the remaining value scalars (bool -- -Wswitch-bool keeps it off
    the switch -- BigInt, floats) plus resolved-str subjects -> the `==`
    chain, if_elif or (any guard) if_elif_guarded -- EXCEPT a str subject
    at or above the switch-dispatch threshold, which the AST routes to
    the discriminator switch REGARDLESS of guards (deferred tier; the
    threshold counts only unguarded literal alternatives). A `Literal[...]`
    subject also dispatches on the AST path, but sema narrows the subject
    to a per-arm LiteralType (`case.type_facts`) whose dead-branch
    elimination can rewrite arm bodies that re-read the subject --
    rejected until THIR expression lowering learns the literal-fact fold.
    Union guard/shared-index/field-condition routing is
    `_match_union_route`; a user-record subject takes the record tiers
    (if_elif_record, or guarded_record when any arm has a guard) --
    polymorphic dispatch is checked at the gate before strategy, mirroring
    `_gen_match_dispatch`'s early poly branch. An Optional subject routes
    on the shared partition fact (optional_partition vs the deferred
    if/elif-optional tier); polymorphic subjects are their own tier
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
        # function-gated, so no wrapper match can route until that form
        # lands (verified: a bare-arm Tree match rejects at the function
        # gate, never reaching this arm).
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
        # second name); anything else is the if/elif-optional tier
        # (deferred). Which subject/arm shapes the slice mirrors is the
        # gate's business (`_match_optional_arms_ok`).
        if partition_optional_cases(stmt.cases) is not None:
            return "optional_partition"
        return None
    if _eligible_scalar(t) or _resolved_str_value(t, analyzer) is not None:
        if _match_str_switches(stmt, analyzer):
            return None
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
    (deferred tier), not the if/elif chain."""
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

def _expr_any(e, pred) -> bool:
    """Whether `pred` holds for any node of the expression tree (generic
    dataclass walk over the TpyExpr children)."""
    stack = [e]
    while stack:
        cur = stack.pop()
        if pred(cur):
            return True
        for f in dataclass_fields(cur):
            v = getattr(cur, f.name)
            if isinstance(v, TpyExpr):
                stack.append(v)
            elif isinstance(v, (list, tuple)):
                stack.extend(x for x in v if isinstance(x, TpyExpr))
    return False

def _expr_contains_call(e) -> bool:
    return _expr_any(e, lambda n: isinstance(n, (TpyCall, TpyMethodCall)))

def _expr_mentions_name(e, name: str) -> bool:
    return _expr_any(e, lambda n: isinstance(n, TpyName) and n.name == name)

def _match_guard_ok(guard, declared: dict[str, TpyType], analyzer) -> bool:
    """A guard the slice renders: the AST emits `if ({gen_expr(guard)})` raw
    -- no truthy wrap -- so only a bool-TYPED expression is mirror-safe (a
    non-bool guard leans on C++ contextual conversion / operator bool).
    Call-free keeps arg temps out: the guard sits inside the arm block with
    no statement flush point (the same reason while-conditions gate them)."""
    t = analyzer.get_expr_type(guard)
    if t is None or not is_bool_type(unwrap_readonly(t)):
        return False
    if _expr_contains_call(guard):
        return False
    return _expr_eligible(guard, declared, analyzer)

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

def _match_keywords_ok(pattern: TpyClassPattern, analyzer, ws: _WalkState,
                       arm_ws: _WalkState, *, allow_conds: bool) -> bool:
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
            if (sub.name in ws.pointers or sub.name in ws.narrowed
                    or sub.name in ws.storage_tuple_locals):
                return False
            ft = _match_record_field_type(pattern, fname, analyzer)
            if ft is None or not _match_capture_field_ok(ft, analyzer):
                return False
            arm_ws.declared[sub.name] = ft
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

def _match_guarded_union_arms_ok(stmt: TpyMatch, analyzer, ws: _WalkState,
                                 prescan: _Prescan, subj: TpyName,
                                 u: UnionType, hoist_declared: dict, *,
                                 in_loop: bool) -> bool:
    """The guarded_union (M4b) arm walk -- `_gen_match_guarded_union`'s
    per-index grouping over class patterns (bare, or with the field
    condition/capture slice -- `_match_keywords_ok`), or-patterns of BARE
    class alternatives (wildcard ALTERNATIVES inside an or-pattern -- the
    broadcast-to-uncovered machinery -- stay out, and so do or-alt
    keywords), `case None:`, and wildcard/capture arms (broadcast to every
    index; all-wildcard indices coalesce into `default:`). Guards are the
    M3b rule (bool-typed, call-free) plus a subject-name reject: the AST
    renders the guard BEFORE the arm's narrowing applies, so a guard
    reading the subject would spell the raw variant name. A field-COND
    entry whose guard reads one of its own captures also rejects: the AST
    composes `if (conds && guard)` and only then declares the captures
    inside the block -- ill-formed C++ (BUGS.md). Bodies walk once per
    source case (broadcast copies re-emit the same lowered body;
    truncated-away entries were still walked -- conservative)."""
    members = _union_index_members(u)
    if members is None:
        return False
    for case in stmt.cases:
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        arm_ws = ws.branch_copy()
        arm_ws.declared.update(hoist_declared)
        member = None
        has_field_conds = False
        capture_names: list[str] = []
        if test is None:
            pass
        elif isinstance(test, TpyClassPattern):
            member = test.resolved_type
            if member is None or test.positional:
                return False
            if _union_member_index(members, member) is None:
                return False
            if not _match_keywords_ok(test, analyzer, ws, arm_ws,
                                      allow_conds=True):
                return False
            has_field_conds = MatchGenerator._pattern_has_field_condition(test)
            capture_names = _match_pattern_captures(test)
        elif isinstance(test, TpyOrPattern):
            if bnode is not None:
                return False
            for alt in test.patterns:
                if not (isinstance(alt, TpyClassPattern)
                        and alt.resolved_type is not None
                        and not alt.keywords and not alt.positional):
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
            arm_ws.declared[subj.name] = facts[subj.name]
            arm_ws.narrowed.add(subj.name)
        if bnode is not None:
            if (bnode.name in ws.pointers or bnode.name in ws.narrowed
                    or bnode.name in ws.storage_tuple_locals):
                return False
            arm_ws.declared[bnode.name] = (member if member is not None
                                           else arm_ws.declared[subj.name])
        if case.guard is not None:
            if not _match_guard_ok(case.guard, arm_ws.declared, analyzer):
                return False
            if _expr_mentions_name(case.guard, subj.name):
                return False
            if has_field_conds and any(
                    _expr_mentions_name(case.guard, n)
                    for n in capture_names):
                return False
    return True

def _match_union_arms_ok(stmt: TpyMatch, analyzer, ws: _WalkState,
                         prescan: _Prescan, subj: TpyName, u: UnionType,
                         hoist_declared: dict, *, in_loop: bool) -> bool:
    """The switch_union (M4a) arm walk: class patterns (sema resolved the
    member; keyword field CAPTURES bind off the `__case_{i}` alias -- field
    value conditions route to the guarded path via `_match_union_route`, so
    the walk passes allow_conds=False), or-patterns of BARE class
    alternatives, `case None:` (the monostate index), and the
    wildcard/capture default; `as` binds the extracted member. Guards and
    shared variant indices route to `_gen_match_guarded_union` on the AST
    path -- rejected (M4b). Each class arm's body walks with the subject
    retyped to sema's arm fact and marked narrowed (reads rename to the
    `__case_{i}` alias at lowering, the U3 mechanic)."""
    if any(c.guard is not None for c in stmt.cases):
        return False
    members = _union_index_members(u)
    if members is None:
        return False
    seen: set[int] = set()
    for case in stmt.cases:
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        arm_ws = ws.branch_copy()
        arm_ws.declared.update(hoist_declared)
        member = None
        if test is None:
            pass  # wildcard/capture -> the `default:` arm
        elif isinstance(test, TpyClassPattern):
            member = test.resolved_type
            if member is None or test.positional:
                return False
            idx = _union_member_index(members, member)
            if idx is None or idx in seen:
                return False
            seen.add(idx)
            if not _match_keywords_ok(test, analyzer, ws, arm_ws,
                                      allow_conds=False):
                return False
        elif isinstance(test, TpyOrPattern):
            if bnode is not None:
                # The AST or-pattern branch never emits the `as` binding.
                return False
            for alt in test.patterns:
                if not (isinstance(alt, TpyClassPattern)
                        and alt.resolved_type is not None
                        and not alt.keywords and not alt.positional):
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
        # The arm's narrowing fact must be exactly sema's {subject: member}
        # (or filtered away); any other shape is out of the slice.
        facts = case.type_facts or {}
        if facts:
            if set(facts) != {subj.name} or member is None:
                return False
            arm_ws.declared[subj.name] = facts[subj.name]
            arm_ws.narrowed.add(subj.name)
        if bnode is not None:
            if (bnode.name in ws.pointers or bnode.name in ws.narrowed
                    or bnode.name in ws.storage_tuple_locals):
                return False
            arm_ws.declared[bnode.name] = (member if member is not None
                                           else arm_ws.declared[subj.name])
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

def _match_record_arms_ok(stmt: TpyMatch, analyzer, ws: _WalkState,
                          prescan: _Prescan, subj: TpyName, kind: str,
                          hoist_declared: dict, *, in_loop: bool) -> bool:
    """The record tiers' (if_elif_record / guarded_record) arm walk: class
    patterns over the concrete record with the field condition/capture
    slice (`_match_keywords_ok`), `as`/capture/wildcard whole-subject
    bindings, or-patterns of CONDITION-ONLY class alternatives, and the
    M3b guard rule (guards render after the arm's bindings on this tier,
    so a guard reading a field capture is fine). An `as` over an
    or-pattern, or a capture inside an or-alternative, is silently dropped
    by the AST or-branches (ill-formed C++, BUGS.md) -- both reject. The
    plain chain (if_elif_record) emits source order, so an always-matching
    arm anywhere but last would emit `} else {` mid-chain -- rejected
    defensively, mirroring the scalar chain's rule (sema's
    unreachable-case check already refuses the shape)."""
    always_arms = 0
    for i, case in enumerate(stmt.cases):
        if case.type_facts:
            return False
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        arm_ws = ws.branch_copy()
        arm_ws.declared.update(hoist_declared)
        if test is None:
            pass
        elif isinstance(test, TpyClassPattern):
            if test.resolved_type is None or test.positional:
                return False
            if not _match_keywords_ok(test, analyzer, ws, arm_ws,
                                      allow_conds=True):
                return False
        elif isinstance(test, TpyOrPattern):
            if bnode is not None:
                return False  # `as` over an or-pattern: dropped on the AST
            for alt in test.patterns:
                if isinstance(alt, TpyWildcardPattern):
                    continue
                if (not isinstance(alt, TpyClassPattern)
                        or alt.resolved_type is None or alt.positional):
                    return False
                if alt.keywords and not _f1_record(alt.resolved_type,
                                                   analyzer):
                    return False
                for fname, sub in alt.keywords:
                    if isinstance(sub, TpyWildcardPattern):
                        continue
                    if not isinstance(sub, TpyLiteralPattern):
                        return False  # or-alt captures: dropped on the AST
                    if _match_field_cond(alt, fname, sub.value,
                                         analyzer) is None:
                        return False
        else:
            return False
        if _match_record_arm_always(test):
            always_arms += 1
            if kind == "if_elif_record" and (always_arms > 1
                                             or i != len(stmt.cases) - 1):
                return False
        if bnode is not None:
            if (bnode.name in ws.pointers or bnode.name in ws.narrowed
                    or bnode.name in ws.storage_tuple_locals):
                return False
            arm_ws.declared[bnode.name] = arm_ws.declared[subj.name]
        if case.guard is not None and not _match_guard_ok(
                case.guard, arm_ws.declared, analyzer):
            return False
    return True

def _stmts_write_name(body, name: str) -> bool:
    """Whether any (nested) statement writes through `name`: an
    assign / aug-assign / var-decl / tuple-unpack / del whose target chain
    roots at it. The optional-capture gate rejects such captures: sema's
    const verdict for the SUBJECT ignores mutations made through a
    match-capture alias, so the AST path renders the write against the
    `const` deref ref -- ill-formed C++ (BUGS.md). Read-only captures are
    unaffected."""
    def _root(e) -> 'str | None':
        while isinstance(e, (TpyFieldAccess, TpySubscript)):
            e = e.obj
        return e.name if isinstance(e, TpyName) else None
    stack = list(body)
    while stack:
        s = stack.pop()
        if isinstance(s, (TpyAssign, TpyAugAssign)):
            if _root(s.target) == name:
                return True
        elif isinstance(s, TpyVarDecl):
            if s.name == name:
                return True
        elif isinstance(s, TpyTupleUnpack):
            if name in s.targets:
                return True
        elif isinstance(s, TpyDelVar):
            if name in s.names:
                return True
        elif isinstance(s, TpyDelItem):
            if any(_root(t) == name for t in s.targets):
                return True
        for f in dataclass_fields(s):
            v = getattr(s, f.name)
            if isinstance(v, TpyStmt):
                stack.append(v)
            elif isinstance(v, (list, tuple)):
                stack.extend(x for x in v if isinstance(x, TpyStmt))
    return False

def _match_optional_arms_ok(stmt: TpyMatch, analyzer, ws: _WalkState,
                            prescan: _Prescan, subj: TpyName,
                            hoist_declared: dict, *,
                            in_loop: bool) -> bool:
    """The optional_partition (O1) arm walk -- `_gen_match_optimized_optional`
    over a pointer-repr `Optional[F1-record]` name subject (an `A | None`
    param or OPTIONAL_TO_PTR local; a value-repr subject's std::optional
    local/param form is outside the slice, so it never gets here). Admitted:
    at most ONE unguarded, binding-free `case None:` prefix arm, then
    EXACTLY ONE always-matching inner arm -- a bare class pattern over the
    inner record (`_emit_optional_inner_record`'s no-field `{ }` block;
    keyword/positional sub-patterns are the record-pattern tier), a
    wildcard, or a capture/`as` binding the dereferenced inner
    (`auto& v = __match_inner_N;`). Guards reject: the partition admits
    some, but the guarded inner chains (`_emit_switch_groups` fallbacks /
    the `cond && guard` folds) are unwitnessed shapes -- and sema's
    unreachable-arm rule caps the UNGUARDED inner dispatch at one arm, so
    the single-arm restriction loses nothing. The None body walks
    un-narrowed; the inner body walks like an `x is not None` branch (no
    rename -- reads keep the pointer name; sema's per-node facts drive the
    deref render on both paths)."""
    partition = partition_optional_cases(stmt.cases)
    if partition is None:
        return False  # defensive -- the strategy fact checked it
    none_cases, inner_cases = partition
    if len(none_cases) > 1 or len(inner_cases) != 1:
        return False
    inner_type = unwrap_readonly(stmt.subject_type).inner
    for case in none_cases:
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        # Or-pattern None spellings (`case None | None:`) and bindings stay
        # out; the partition already bars None-arm guards.
        if bnode is not None or not isinstance(test, TpyLiteralPattern):
            return False
    case = inner_cases[0]
    if case.guard is not None:
        return False
    parts = _match_arm_parts(case)
    if parts is None:
        return False
    test, bnode = parts
    if test is None:
        pass  # wildcard/capture -- always-matching, like the bare class arm
    elif isinstance(test, TpyClassPattern):
        if test.keywords or test.positional:
            return False
        if test.resolved_type is None or test.resolved_type != inner_type:
            return False
    else:
        return False
    arm_ws = ws.branch_copy()
    arm_ws.declared.update(hoist_declared)
    if bnode is not None:
        # Only the fresh `auto&` deref binding is in the slice: a
        # pre-declared name would take `_emit_binding`'s assign/pointer
        # arms against the deref (and its record-typed hoist already
        # rejects upstream -- defensive).
        if (bnode.name in ws.declared or bnode.name in ws.pointers
                or bnode.name in ws.narrowed
                or bnode.name in ws.storage_tuple_locals):
            return False
        # A write through the capture is ILL-FORMED on the AST path when
        # the subject binding is const (sema's const verdict never sees
        # capture-alias mutations -- BUGS.md); reject rather than mirror
        # the broken emit. Direct subject writes (`x.n = ...`) are fine --
        # sema attributes those.
        if _stmts_write_name(case.body, bnode.name):
            return False
        arm_ws.declared[bnode.name] = inner_type
    return True

def _match_eligible(stmt: TpyMatch, analyzer, ws: _WalkState,
                    prescan: _Prescan, *, in_branch: bool,
                    in_loop: bool) -> bool:
    """The `match` gate arm -- M1: the unguarded scalar switch tiers
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
        return False
    kind = _match_strategy(stmt, analyzer)
    if kind is None:
        return False
    subj = stmt.subject
    if not isinstance(subj, TpyName) or subj.name not in ws.declared:
        return False
    if (subj.name in ws.pointers or subj.name in ws.narrowed
            or subj.name in ws.storage_tuple_locals):
        return False
    if not _expr_eligible(subj, ws.declared, analyzer):
        return False
    hoist_declared: dict[str, TpyType] = {}
    for name, raw in analyzer.if_branch_decls.get(id(stmt), {}).items():
        if name in ws.declared:
            continue
        if name in prescan.native_globals:
            return False
        if in_branch or in_loop:
            return False
        vtype = unwrap_ref_type(raw)
        if not _statements._try_hoist_type_ok(vtype, analyzer):
            return False
        hoist_declared[name] = vtype
    if kind == "switch_union":
        u = unwrap_readonly(stmt.subject_type)
        arms_ok = (_match_guarded_union_arms_ok
                   if _match_union_route(stmt, u) == "guarded_union"
                   else _match_union_arms_ok)
        if not arms_ok(stmt, analyzer, ws, prescan, subj, u,
                       hoist_declared, in_loop=in_loop):
            return False
        ws.declared.update(hoist_declared)
        return True
    if kind in ("if_elif_record", "guarded_record"):
        if not _f1_record(unwrap_readonly(stmt.subject_type), analyzer):
            return False
        if not _match_record_arms_ok(stmt, analyzer, ws, prescan, subj,
                                     kind, hoist_declared, in_loop=in_loop):
            return False
        ws.declared.update(hoist_declared)
        return True
    if kind == "optional_partition":
        # Only the pointer-repr Optional[F1-record] subject form is in the
        # slice; a value-repr subject's std::optional local/param binding
        # is itself function-gated, so this reject is the (defensive)
        # boundary for expression-position subjects that ever widen in.
        if _optional_ptr_borrow_name(subj, ws.declared, analyzer) is None:
            return False
        if not _match_optional_arms_ok(stmt, analyzer, ws, prescan, subj,
                                       hoist_declared, in_loop=in_loop):
            return False
        ws.declared.update(hoist_declared)
        return True
    always_match_arms = 0
    group_guards: dict[str, list[bool]] = {}
    for i, case in enumerate(stmt.cases):
        if case.type_facts:
            return False
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        if test is None:
            # An always-matching arm: the switch `default:` / the plain
            # chain's final `} else {`. The plain chain emits source order,
            # so a non-final one would strand later arms. The guarded
            # chain's standalone blocks take any position and any count (a
            # failed guard falls through); the switch tiers merge them into
            # one default group, order-checked below.
            always_match_arms += 1
            if kind == "if_elif" and (always_match_arms > 1
                                      or i != len(stmt.cases) - 1):
                return False
            key = ""
        elif isinstance(test, TpyOrPattern):
            if not all(_match_label_ok(alt, kind) for alt in test.patterns):
                return False
            key = "|".join(_match_case_label(alt, kind, analyzer)
                           for alt in test.patterns)
        elif not _match_label_ok(test, kind):
            return False
        else:
            key = _match_case_label(test, kind, analyzer)
        if kind in ("switch_enum", "switch_primitive"):
            group_guards.setdefault(key, []).append(case.guard is not None)
        arm_ws = ws.branch_copy()
        arm_ws.declared.update(hoist_declared)
        if bnode is not None:
            # The binding aliases the whole subject; only _emit_binding's
            # value arms are in the slice -- a pointer/narrowed/tuple-alias
            # name would take the aliasing arms.
            if (bnode.name in ws.pointers or bnode.name in ws.narrowed
                    or bnode.name in ws.storage_tuple_locals):
                return False
            arm_ws.declared[bnode.name] = arm_ws.declared[subj.name]
        if case.guard is not None and not _match_guard_ok(
                case.guard, arm_ws.declared, analyzer):
            return False
    # Within each switch group, an unguarded entry only in the final
    # position (sema's duplicate-case rule guarantees it; a violated order
    # would emit an else-chain with no opened if) -- defensive.
    for guards in group_guards.values():
        if any(not g for g in guards[:-1]):
            return False
    ws.declared.update(hoist_declared)
    return True

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

def _lower_match(stmt: TpyMatch, lc: _LowerCtx, declared: dict[str, TpyType],
                 loc, *, loop_depth: int = 0) -> THIRMatch:
    """Lower a scalar-tier `match` (see `THIRMatch` for the emit shapes).
    Labels/condition-RHS pre-render here. Switch tiers regroup the wildcard
    arm LAST regardless of source position (`_group_switch_arms` appends
    default_entries after the label groups); the if/elif tier keeps source
    order (its wildcard is the final `} else {` -- gate-enforced last). Arm
    bodies lower under narrowing-scope snapshots like every branch body
    (the AST's `_emit_case_body` narrowed_vars/alias restores); the hoisted
    predecls follow the try arm exactly and enter the caller's `declared`.
    `emit_unreachable` folds the AST's `_emit_match_unreachable_tail`
    condition at lowering; `synthetic_default` its `default: break;` rule
    (switch tiers only -- the if/elif chain has no default)."""
    kind = _match_strategy(stmt, lc.analyzer)
    assert kind is not None, "ineligible match reached lowering"
    if kind != "switch_union":  # the union lowerers witness their route
        _witness(f"match.{kind}")
    hoist_decls: list[tuple[str, str]] = []
    for name, raw in lc.analyzer.if_branch_decls.get(id(stmt), {}).items():
        if name in declared:
            continue
        vtype = unwrap_ref_type(raw)
        render_src = (_resolved_str_value(vtype, lc.analyzer)
                      or _resolved_bytes_value(vtype, lc.analyzer)
                      or vtype)
        hoist_decls.append((name, lc.render_type(render_src)))
        declared[name] = vtype
    if hoist_decls:
        _witness("match.hoist_decl")
    if kind == "switch_union":
        u = unwrap_readonly(stmt.subject_type)
        if _match_union_route(stmt, u) == "guarded_union":
            return _lower_match_guarded_union(stmt, lc, declared, loc,
                                              hoist_decls,
                                              loop_depth=loop_depth)
        return _lower_match_union(stmt, lc, declared, loc, hoist_decls,
                                  loop_depth=loop_depth)
    if kind in ("if_elif_record", "guarded_record"):
        return _lower_match_record(stmt, lc, declared, loc, hoist_decls,
                                   kind, loop_depth=loop_depth)
    if kind == "optional_partition":
        return _lower_match_optional(stmt, lc, declared, loc, hoist_decls,
                                     loop_depth=loop_depth)
    subj_type = declared.get(stmt.subject.name)
    is_chain = kind in ("if_elif", "if_elif_guarded")
    arms: list[THIRMatchArm] = []
    groups: dict[str, tuple[tuple[str, ...], list[THIRMatchArmEntry]]] = {}
    group_order: list[str] = []
    default_entries: list[THIRMatchArmEntry] = []
    for case in stmt.cases:
        test, bnode = _match_arm_parts(case)
        binding = None
        arm_declared = dict(declared)
        if bnode is not None:
            mode = ("assign" if bnode.name in declared
                    else "copy" if bnode.bind_by_value else "ref")
            _witness(f"match.bind_{mode}")
            binding = THIRMatchBinding(name=bnode.name, mode=mode)
            arm_declared[bnode.name] = subj_type
        guard = None
        if case.guard is not None:
            _witness("match.guard_arm")
            guard = _lower_expr(case.guard, lc)
        entry = THIRMatchArmEntry(
            body=_statements._lower_scoped_stmts(
                case.body, lc, arm_declared, loop_depth=loop_depth),
            loc=case.loc, binding=binding, guard=guard)
        if test is None:
            labels: tuple[str, ...] = ()
        elif isinstance(test, TpyOrPattern):
            _witness("match.or_labels")
            labels = tuple(_match_case_label(alt, kind, lc.analyzer)
                           for alt in test.patterns)
        else:
            labels = (_match_case_label(test, kind, lc.analyzer),)
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
            labels, entries = groups[key]
            arms.append(THIRMatchArm(labels=labels, entries=tuple(entries)))
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
    synthetic_default = (not is_chain and not default_entries
                         and not stmt.is_exhaustive)
    if synthetic_default:
        _witness("match.synthetic_default")
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy=kind,
        subject=_lower_expr(stmt.subject, lc),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=synthetic_default,
        default_goto=default_goto,
        loc=loc,
    )

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
    for case in stmt.cases:
        test, bnode = _match_arm_parts(case)
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
            guard = _lower_expr(case.guard, lc)
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
        subject=_lower_expr(stmt.subject, lc),
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
    none_cases, inner_cases = partition_optional_cases(stmt.cases)
    inner_type = unwrap_readonly(stmt.subject_type).inner
    none_entry = None
    if none_cases:
        _witness("match.optional_none_arm")
        ncase = none_cases[0]
        none_entry = THIRMatchArmEntry(
            body=_statements._lower_scoped_stmts(
                ncase.body, lc, dict(declared), loop_depth=loop_depth),
            loc=ncase.loc)
    else:
        _witness("match.optional_value_only")
    case = inner_cases[0]
    _test, bnode = _match_arm_parts(case)
    binding = None
    arm_declared = dict(declared)
    if bnode is not None:
        mode = "copy" if bnode.bind_by_value else "ref"
        _witness(f"match.bind_{mode}")
        _witness("match.optional_inner_bind")
        binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                   from_case_var=True)
        arm_declared[bnode.name] = inner_type
    arm = THIRMatchArm(labels=(), entries=(THIRMatchArmEntry(
        body=_statements._lower_scoped_stmts(
            case.body, lc, arm_declared, loop_depth=loop_depth),
        loc=case.loc, binding=binding),))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="optional_partition",
        subject=_lower_expr(stmt.subject, lc),
        subject_ref=True,
        arms=(arm,),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        none_entry=none_entry,
        loc=loc,
    )

def _lower_match_union(stmt: TpyMatch, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc,
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
    subj_name = stmt.subject.name
    arms: list[THIRMatchArm] = []
    for i, case in enumerate(stmt.cases):
        test, bnode = _match_arm_parts(case)
        facts = case.type_facts or {}
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
        subject=_lower_expr(stmt.subject, lc),
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
    n = len(members)
    subj_name = stmt.subject.name

    type_arms: dict[int, list] = {i: [] for i in range(n)}
    for case in stmt.cases:
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
                guard = _lower_expr(case.guard, lc)
            body = _statements._lower_stmts(
                case.body, lc, arm_declared, in_branch=True,
                loop_depth=loop_depth)
        finally:
            lc.narrow = saved
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
        subject=_lower_expr(stmt.subject, lc),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        is_ptr_variant=is_ptr_variant_union(u),
        loc=loc,
    )
