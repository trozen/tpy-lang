"""
TurboPython Match/Case Semantic Analysis

Semantic analysis for match/case statements and pattern matching.
"""

from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING, Iterator

from ..typesys import (
    TpyType,
    NominalType, AliasRef, RecursiveAliasInstanceType, ReadonlyType,
    NoneType, OptionalType, UnionType, PendingStrType, TupleType,
    LiteralType, LiteralValue, LiteralTag, TypeParamRef,
    unwrap_readonly, unwrap_ref_type, unwrap_own, unwrap_qualifiers,
    is_any_str_type, is_numeric_type, is_protocol_type,
    polymorphic_source_inner, deref_dispatch_inner,
    same_nominal_symbol_loose,
)
from ..modules import _resolve_concrete_type_name
from .flow_facts import FlowFacts
from .protocols import dynamic_dispatch_type_conforms
from ..type_def_registry import (
    is_bool_type, is_fixed_int_type, is_big_int_type,
    is_str_category, is_char_type, is_float_category, is_bytes_category,
    is_enum_type, is_int_enum_type, enum_info_of, is_free_copy_scalar,
    int_traits_of,
)
from .numeric_lattice import fixed_int_range_contains
from ..parse import (
    TpyName, TpyFieldAccess, TpySubscript, TpyMethodCall, TpyIntLiteral,
    TpyAssign, TpyAugAssign, TpyNestedDef, TpyExpr, TpyStmt,
    TpyMatch, TpyMatchCase, TpyPattern, TpyWildcardPattern, TpyCapturePattern,
    TpyClassPattern, TpyLiteralPattern, TpyValuePattern, TpyOrPattern, TpyAsPattern,
)
from ..parse.nodes import (
    stmt_has_any_suspension, iter_capture_bindings, body_writes_name,
)
from ..value_category import is_rvalue_source
from .context import record_stmt_borrow_binding, register_capture_alias_borrow
from .receiver_calls import is_invalidating_method

if TYPE_CHECKING:
    from ..typesys import RecordInfo
    from .context import SemanticContext
    from .expressions import ExpressionAnalyzer
    from .statements import StatementAnalyzer


def _expr_path_parts(expr: TpyExpr) -> tuple[str, ...] | None:
    """Syntactic key for a name/field/subscript chain; None for anything
    else. Non-literal indices key as '[*]' (may alias any index)."""
    if isinstance(expr, TpyName):
        return (expr.name,)
    if isinstance(expr, TpyFieldAccess):
        base = _expr_path_parts(expr.obj)
        return None if base is None else base + (f".{expr.field}",)
    if isinstance(expr, TpySubscript):
        base = _expr_path_parts(expr.obj)
        if base is None:
            return None
        if isinstance(expr.index, TpyIntLiteral):
            return base + (f"[{expr.index.value}]",)
        return base + ("[*]",)
    return None


def _parts_may_alias(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        if x.startswith("[") and y.startswith("[") and "[*]" in (x, y):
            continue
        return False
    return True


def _peel_match_qualifiers(typ: TpyType) -> tuple[TpyType, bool]:
    """The dispatch type behind `typ`'s qualifier wrappers, and whether any of
    them was `readonly[...]`.

    Every wrapper here renders as the same C++ value (or a reference to it),
    so none of them changes what a pattern tests. Between them the two unwrap
    orders below take any two-wrapper nesting apart in a single pass; the loop
    is what covers a deeper stack, where one pass can leave a layer behind.
    The readonly flag is reported rather than merely dropped: a capture that
    aliases readonly storage is readonly too, and the alias is a fresh name
    that has no other way to learn it.
    """
    readonly = False
    while True:
        readonly = readonly or isinstance(typ, ReadonlyType)
        stripped = unwrap_own(
            unwrap_ref_type(unwrap_readonly(unwrap_qualifiers(typ))))
        if stripped == typ:
            return typ, readonly
        typ = stripped


def _strip_match_qualifiers(typ: TpyType) -> TpyType:
    """Drop the qualifier wrappers a match dispatch or comparison sees through."""
    return _peel_match_qualifiers(typ)[0]


def _matches_every_value(pat: TpyPattern) -> bool:
    """Whether `pat` matches every subject value, so no later arm is reachable.

    A wildcard or capture alternative subsumes the alternatives beside it, so an
    or-group holding one is a catch-all exactly like a bare wildcard arm. The
    dispatch cannot be trusted to make the later arms dead on its own: an
    irrefutable group lowers to the switch default, and a C++ switch ignores
    source order, so the arms after it would stay live case labels.
    """
    if isinstance(pat, TpyAsPattern):
        pat = pat.pattern
    if isinstance(pat, TpyOrPattern):
        return any(_matches_every_value(alt) for alt in pat.patterns)
    return isinstance(pat, (TpyWildcardPattern, TpyCapturePattern))


def _subst_type_params(typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
    """Substitute TypeParamRef instances in a type according to subst map."""
    if isinstance(typ, TypeParamRef) and typ.name in subst:
        return subst[typ.name]
    return typ.map_inner_types(lambda t: _subst_type_params(t, subst))


class OrPatternKind(Enum):
    UNION = "union"
    NONUNION = "nonunion"
    RECORD = "record"
    OPTIONAL = "optional"


class MatchAnalyzer:
    """Semantic analysis for match/case statements."""

    def __init__(self, ctx: SemanticContext, stmts: StatementAnalyzer,
                 expr: ExpressionAnalyzer):
        self.ctx = ctx
        self.stmts = stmts
        self.expr = expr

    def analyze_match(self, stmt: TpyMatch) -> None:
        """Analyze a match/case statement."""
        subject_type = self.expr.analyze_expr(stmt.subject)
        # `unwrap_own` handles an rvalue subject returned by value
        # (`match make_box():` -> `Own[Box[T]]`); the match reads the owned
        # value, so dispatch sees its inner type. Reachable only since
        # expression subjects were allowed (a name local is never Own-typed).
        effective_type, subject_readonly = _peel_match_qualifiers(subject_type)
        # Expand recursive union alias placeholder to its underlying
        # UnionType so match dispatch sees the variant arms.
        if isinstance(effective_type, AliasRef):
            alias = self.ctx.registry.resolve_alias_ref(effective_type)
            if alias is not None:
                effective_type = alias
        stmt.subject_type = effective_type
        # A generic recursive alias instance (Tree[int]) stays the codegen
        # subject -- its wrapper_info() drives .value variant dispatch. For the
        # (UnionType-centric) pattern arm-analysis below, stand in a synthesized
        # union of its substituted alternatives so the existing union path
        # applies unchanged. The alternatives are taken verbatim, NOT through
        # `make_union`: the wrapper struct's std::variant arms are exactly this
        # tuple, so re-canonicalizing (sorting, flattening a union alternative)
        # would let an arm name a type that has no variant to dispatch on.
        if isinstance(effective_type, RecursiveAliasInstanceType):
            effective_type = UnionType(effective_type.alternatives())
        is_union = isinstance(effective_type, UnionType)
        is_enum = is_enum_type(effective_type)
        is_literal = isinstance(effective_type, LiteralType)
        is_primitive = is_literal or (
            is_fixed_int_type(effective_type) or is_big_int_type(effective_type)
            or is_float_category(effective_type) or is_bool_type(effective_type)
            or is_str_category(effective_type) or is_char_type(effective_type)
            or isinstance(effective_type, PendingStrType)
        )
        is_record = (
            isinstance(effective_type, NominalType)
            and effective_type.is_user_record
            and self.ctx.registry.get_record(effective_type.name) is not None
        )
        is_optional = isinstance(effective_type, OptionalType)
        # @dynamic-protocol / polymorphic-class subject: dispatch by runtime
        # type via dynamic_cast, the match-statement sibling of isinstance
        # subclass narrowing. Reuses the isinstance source detection so bare
        # `Pet`, `Ptr[Pet]`, and owning-wrapper deref views all qualify. Takes
        # precedence over the record/wrapper paths (a `Box[Pet]` is itself a
        # record, but the arms dispatch on the deref payload's dynamic type).
        poly_inner, poly_depth = self._poly_dispatch_source(effective_type)
        is_polymorphic = poly_inner is not None
        if not (is_union or is_enum or is_primitive or is_record
                or is_optional or is_polymorphic):
            raise self.ctx.error(
                f"match subject must be a union, enum, primitive, record, "
                f"or Optional type, got '{effective_type}'", stmt
            )
        stmt.polymorphic_dispatch = is_polymorphic

        # Subject variable name for narrowing (only if simple name). A
        # polymorphic match on an expression subject (subscript / field /
        # rvalue call / wrapper deref) dispatches on a once-bound
        # `__match_subject`, so no name is required -- unlike isinstance, which
        # narrows a name in place. The arm reaches the narrowed value through
        # `as` / a capture; subject-name narrowing below applies only to names.
        subject_name: str | None = None
        if isinstance(stmt.subject, TpyName):
            subject_name = stmt.subject.name
            # isinstance narrowing strips ReadonlyType off the expr type while
            # the scope binding keeps it, so the qualifier peel above can miss
            # a readonly name reached through an earlier narrowing.
            subject_readonly = (subject_readonly
                                or self.ctx.is_readonly_name(subject_name))

        # Resumable frame (H1): when a generator/async `match` carries a
        # suspension, its arm bodies become separate states, so pattern
        # bindings must be frame fields (see the per-arm binding loop).
        # `current_function` may be a module-init sentinel for a top-level
        # `match` (no `is_generator`/`is_async`); a suspension cannot appear
        # at module scope anyway, so guard with getattr.
        cur_fn = self.ctx.func.current_function
        needs_frame_field = (
            (getattr(cur_fn, "is_generator", False)
             or getattr(cur_fn, "is_async", False))
            and stmt_has_any_suspension(stmt)
        )

        had_wildcard = False
        # Separate from `had_wildcard`, which also grants exhaustiveness credit:
        # a union subject's coverage is counted from the variant labels its arms
        # claim, and an or-group's wildcard names none, so crediting it here
        # would silently widen coverage as a side effect of the arm-order rule.
        saw_irrefutable_arm = False
        # Optional subjects have two coverage sides: a class pattern matches
        # every non-None value but never None, while wildcard/capture match
        # both. Exhaustiveness and unreachable-arm checks track each side.
        covers_none = False
        covers_value = False
        seen_types: set[str] = set()
        seen_values: set[object] = set()
        # Polymorphic dispatch: resolved arm types, in source order, for the
        # subclass-shadowing unreachable check (a later arm whose type is a
        # subclass-or-equal of an earlier arm's type can never be reached).
        seen_poly: list[TpyType] = []

        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        assigned_before = frozenset(self.ctx.func.definitely_assigned)
        bindings_before = dict(self.ctx.func.current_scope.bindings)
        ns_types_before = self.stmts._save_ns_var_types()
        before = self.stmts.init.save()

        arm_states: list[FlowFacts] = []
        arm_bindings: list[dict[str, TpyType]] = []
        consumed_before = self.ctx.func.current_consumed_own_params.copy()
        arm_consumed: list[tuple[set[str], bool]] = []  # (consumed_set, terminated)
        rebound_captures: set[str] = set()
        capture_bind_types: dict[str, list[TpyType | None]] = {}

        for case in stmt.cases:
            if had_wildcard or saw_irrefutable_arm:
                raise self.ctx.error(
                    "unreachable case after wildcard pattern", case.pattern
                )
            if is_optional and covers_value:
                pat_top = case.pattern
                if isinstance(pat_top, TpyAsPattern):
                    pat_top = pat_top.pattern
                is_value_only = (
                    isinstance(pat_top, (TpyClassPattern, TpyValuePattern))
                    or (isinstance(pat_top, TpyLiteralPattern)
                        and pat_top.value is not None)
                    or (isinstance(pat_top, TpyOrPattern)
                        and not any(
                            (isinstance(a, TpyLiteralPattern)
                             and a.value is None)
                            or isinstance(a, (TpyWildcardPattern,
                                              TpyCapturePattern))
                            for a in pat_top.patterns))
                )
                if is_value_only:
                    raise self.ctx.error(
                        "unreachable case: every non-None value is already "
                        "matched by an earlier arm", case.pattern
                    )
            # Restore state to pre-match for each arm
            self.stmts.init.restore(before)
            self.ctx.func.current_consumed_own_params = consumed_before.copy()
            self.ctx.func.current_scope.bindings = dict(bindings_before)
            self.stmts._restore_ns_var_types(ns_types_before)

            pattern_bindings: dict[str, TpyType] = {}
            # Guarded cases don't consume types/values for duplicate detection,
            # because the guard may fail and fall through.
            has_guard = case.guard is not None
            saved_seen_types = set(seen_types) if has_guard else None
            saved_seen_values = set(seen_values) if has_guard else None
            saved_seen_poly = list(seen_poly) if has_guard else None
            if is_polymorphic:
                assert poly_inner is not None
                self._analyze_pattern_polymorphic(
                    case.pattern, poly_inner, seen_types, seen_poly,
                    pattern_bindings, stmt,
                )
            elif is_union:
                self._analyze_pattern(case.pattern, effective_type, seen_types, pattern_bindings, stmt)
            elif is_record:
                self._analyze_pattern_record(
                    case.pattern, effective_type, pattern_bindings, stmt,
                )
            elif is_optional:
                self._analyze_pattern_optional(
                    case.pattern, effective_type, seen_values, pattern_bindings, stmt,
                    none_covered=covers_none,
                )
            else:
                self._analyze_pattern_nonunion(
                    case.pattern, effective_type, seen_values, pattern_bindings, stmt,
                )
            if has_guard:
                seen_types.clear()
                seen_types.update(saved_seen_types)  # type: ignore[arg-type]
                seen_values.clear()
                seen_values.update(saved_seen_values)  # type: ignore[arg-type]
                seen_poly[:] = saved_seen_poly  # type: ignore[index]

            for name, ty in pattern_bindings.items():
                # A capture that binds the full Optional subject rebinds any
                # same-named existing local to a wider type; the pre-declared
                # C++ slot can't hold it, so reject at the TPy level.
                existing = bindings_before.get(name)
                if (existing is not None and isinstance(ty, OptionalType)
                        and existing != ty):
                    raise self.ctx.error(
                        f"match capture '{name}' binds the full Optional "
                        f"subject ('{ty}') but '{name}' already has type "
                        f"'{existing}'; add a 'case None:' arm before it or "
                        f"use a fresh name", case.pattern)
                # A capture that ALIASES readonly storage is readonly too. The
                # isinstance twin needs no such step (it narrows the subject's
                # own binding, which already carries the qualifier); a capture
                # is a fresh name, and without this the write through it is
                # accepted by sema and rejected only by the C++ compiler. A
                # free-copy scalar capture is a durable copy, not an alias, so
                # it keeps the bare type.
                if subject_readonly and not self._capture_binds_by_value(ty):
                    ty = ReadonlyType(ty)
                    pattern_bindings[name] = ty
                self.ctx.func.current_scope.define(name, ty)
                self.ctx.func.nonstmt_bound_names.add(name)
                self.stmts.init.mark_assigned(name)
                # A capture rebinding a str/bytes view local reads the subject.
                for fam, vid in self.ctx.func.view_ids_by_name.get(name, ()):
                    self.stmts.deduction.note_view_binding(fam, vid, stmt.subject)
                if name not in self.ctx.func.var_scope_depth:
                    self.ctx.func.var_scope_depth[name] = self.ctx.func.current_scope.depth
                # Resumable frame (H1): a `match` carrying a suspension
                # decomposes into per-arm body states, so a pattern binding
                # read in an arm body must live in the frame rather than as a
                # dispatch-local that vanishes at the state split -- that is
                # what `frame_exempt=False` buys here. Without a suspension the
                # binding is registered for resolution only.
                if self.ctx.func.current_ns is not None:
                    self.ctx.func.current_ns.bind_capture(
                        name, ty, frame_exempt=not needs_frame_field)

            # Narrow subject variable for class patterns. For polymorphic
            # dispatch the fact lets the arm body resolve `subject.method()`
            # against the matched subclass (same as isinstance narrowing).
            narrowing_facts = self._match_case_narrowing_facts(
                case.pattern, subject_name, effective_type,
            ) if (is_union or is_polymorphic) else {}
            if narrowing_facts:
                case.type_facts = self.stmts._filter_union_codegen_facts(narrowing_facts)
                self.ctx.func.narrowed_types.update(narrowing_facts)

            # Narrow Optional subject to inner type in arms that cannot
            # match None. A wildcard/capture arm DOES match None unless an
            # earlier arm already took the None path.
            if is_optional and subject_name is not None:
                pat = case.pattern
                if isinstance(pat, TpyAsPattern):
                    pat = pat.pattern
                is_none_arm = isinstance(pat, TpyLiteralPattern) and pat.value is None
                may_match_none = (
                    isinstance(pat, (TpyWildcardPattern, TpyCapturePattern))
                    and not covers_none
                )
                if not is_none_arm and not may_match_none:
                    self.ctx.func.narrowed_types[subject_name] = effective_type.inner

            # Narrow Literal subject to matched value(s)
            if is_literal and subject_name is not None:
                matched = self._extract_literal_pattern_values(case.pattern, effective_type)
                if matched is not None:
                    narrowed = LiteralType(effective_type.base_type, tuple(matched))
                    self.ctx.func.narrowed_types[subject_name] = narrowed
                    facts = {subject_name: narrowed}
                    case.type_facts = self.stmts._filter_union_codegen_facts(facts)

            # One fact (bind_by_value per capture) drives both codegen's
            # binding form and the dangle warning below, so the two cannot
            # disagree (a scalar is never silently aliased). A free-copy scalar
            # is copied; everything else borrows (copying str/BigInt pessimizes
            # the common path, a view still dangles, a reference type diverges
            # from CPython aliasing). Returns whether any binding aliases.
            # Runs BEFORE the arm body: the loan registered below has to be in
            # the borrow graph while the body is walked, or a mutation through
            # the capture cannot climb back to the subject. Every input is
            # syntactic (the pattern and an unanalyzed body scan).
            aliasing_bindings, arm_rebinds = self._annotate_capture_bind_modes(
                case.pattern, pattern_bindings, case.body)
            # A capture the arm REBINDS must be hoisted (see the predecl block
            # below): CPython's `match` is not its own scope, so the rebind
            # targets the same local the capture bound. Record each capture's
            # bound type too -- the hoist needs every arm to agree on it.
            for cap_name, is_rebound in arm_rebinds.items():
                capture_bind_types.setdefault(cap_name, []).append(
                    pattern_bindings.get(cap_name))
                if is_rebound:
                    rebound_captures.add(cap_name)
            # An aliasing capture of an lvalue subject borrows it (pointer form),
            # exactly like `q = subject` -- record the stmt-borrow fact so the
            # branch-decl hoist picks the alias (T*) form rather than copying
            # into owned std::optional storage, and register the loan itself so
            # a mutation through the capture climbs the borrow graph back to
            # the subject (the param's const verdict, the method's is_readonly
            # verdict and the ptr-variant deep-const verdict all read it). An
            # rvalue subject must own: the temporary dies at the match block,
            # so a leaked alias would dangle, and there is no caller storage a
            # mutation could reach.
            if aliasing_bindings and not is_rvalue_source(self.ctx, stmt.subject):
                for node in iter_capture_bindings(case.pattern):
                    if not node.bind_by_value:
                        record_stmt_borrow_binding(
                            self.ctx, node.name,
                            pattern_bindings.get(node.name), stmt.subject)
                        self.ctx.func.nonstmt_borrow_bindings.add(node.name)
                        register_capture_alias_borrow(
                            self.ctx, node.name, stmt.subject)
            # Analyze guard expression (pattern bindings are in scope)
            if case.guard is not None:
                self.expr.analyze_condition(case.guard)

            for s in case.body:
                self.stmts.analyze_stmt(s)

            # Stays AFTER the body: the scan reads the arm's cached expression
            # types to tell an invalidating receiver method from one whose
            # readonly verdict only settles in Phase 2.
            if (aliasing_bindings
                    and isinstance(stmt.subject, (TpyFieldAccess, TpySubscript))):
                self._warn_arm_subject_mutation(case, stmt.subject)

            arm_states.append(self.stmts.init.save())
            arm_consumed.append((self.ctx.func.current_consumed_own_params.copy(), self.ctx.func.init_terminated))
            arm_bindings.append(dict(self.ctx.func.current_scope.bindings))

            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            if case.guard is None and _matches_every_value(pat):
                saw_irrefutable_arm = True
            if is_optional:
                if case.guard is None:
                    if isinstance(pat, (TpyWildcardPattern, TpyCapturePattern)):
                        covers_none = True
                        covers_value = True
                    elif (isinstance(pat, TpyClassPattern)
                          and not any(self._is_constraining_sub_pattern(sub)
                                      for _, sub in pat.keywords)):
                        # Matches every non-None value but never None.
                        covers_value = True
                    elif (isinstance(pat, TpyLiteralPattern)
                          and pat.value is None):
                        covers_none = True
                    elif isinstance(pat, TpyOrPattern):
                        for alt in pat.patterns:
                            if isinstance(alt, TpyAsPattern):
                                alt = alt.pattern
                            if (isinstance(alt, TpyLiteralPattern)
                                    and alt.value is None):
                                covers_none = True
                            elif _matches_every_value(alt):
                                covers_none = True
                                covers_value = True
                            elif (isinstance(alt, TpyClassPattern)
                                  and not any(
                                      self._is_constraining_sub_pattern(sub)
                                      for _, sub in alt.keywords)):
                                covers_value = True
                if covers_none and covers_value:
                    had_wildcard = True
            elif isinstance(pat, (TpyWildcardPattern, TpyCapturePattern)) and case.guard is None:
                had_wildcard = True
            # Class pattern on concrete record with no conditions is always-matching.
            # Excludes polymorphic dispatch -- there `case Sub()` is a runtime
            # type test, not an always-true field match, so only a root-type arm
            # (handled below) is a catch-all.
            elif (not is_polymorphic
                  and is_record and isinstance(pat, TpyClassPattern)
                  and case.guard is None
                  and not any(self._is_constraining_sub_pattern(sub)
                              for _, sub in pat.keywords)):
                had_wildcard = True
            # Polymorphic dispatch: a bare class pattern naming the subject's
            # own (concrete) root type matches every value -- `dynamic_cast` to
            # the root always succeeds -- so it acts as a catch-all.
            elif (is_polymorphic and isinstance(pat, TpyClassPattern)
                  and case.guard is None and pat.resolved_type == poly_inner
                  and not any(self._is_constraining_sub_pattern(sub)
                              for _, sub in pat.keywords)):
                had_wildcard = True

        # Exhaustiveness check for finite-valued types. A polymorphic subject
        # ranges over an open set of subclasses, so it is exhaustive only with
        # an unconditional catch-all (wildcard or root-type arm).
        if is_polymorphic:
            missing = [] if had_wildcard else [None]  # type: ignore[list-item]
        elif is_optional:
            # Each side must be covered independently: a class/literal arm
            # never matches None, and a None arm never matches a value.
            missing = []
            if not covers_none:
                missing.append("None")
            if not covers_value:
                inner_missing = self._match_missing_cases(
                    unwrap_readonly(effective_type.inner), seen_types, seen_values)
                if inner_missing == [None]:
                    missing.append(str(effective_type.inner))
                else:
                    missing.extend(inner_missing)
        else:
            missing = (
                [] if had_wildcard
                else self._match_missing_cases(effective_type, seen_types, seen_values)
            )
        stmt.is_exhaustive = not missing
        if missing:
            if missing == [None]:
                msg = (
                    f"non-exhaustive match on '{effective_type}'; "
                    f"no unconditional catch-all arm "
                    f"(add 'case _: pass' to suppress)"
                )
            else:
                msg = (
                    f"non-exhaustive match on '{effective_type}'; "
                    f"missing: {', '.join(missing)} "
                    f"(add 'case _: pass' to suppress)"
                )
            self.ctx.warning(msg, stmt)

        # Merge flow states across all arms. A non-exhaustive match can fall
        # through with no arm taken, so the pre-match state joins the merge.
        self._merge_match_arms(arm_states, before,
                               can_fall_through=bool(missing))

        # Merge consumed Own[T] params: intersect non-terminated arms.
        # Terminated arms don't affect live continuation (same as if/else).
        if arm_consumed:
            live_sets = [s for s, terminated in arm_consumed if not terminated]
            dead_sets = [s for s, terminated in arm_consumed if terminated]
            if missing:
                live_sets.append(consumed_before)
            if live_sets:
                merged_consumed = live_sets[0]
                for s in live_sets[1:]:
                    merged_consumed = merged_consumed & s
                self.ctx.func.current_consumed_own_params = merged_consumed
            elif dead_sets:
                self.ctx.func.current_consumed_own_params = set().union(*dead_sets)
            else:
                self.ctx.func.current_consumed_own_params = consumed_before

        # Restore scope bindings, merging types from arms
        self.ctx.func.current_scope.bindings = dict(bindings_before)
        self.stmts._restore_ns_var_types(ns_types_before)
        for arm_b in arm_bindings:
            for name, ty in arm_b.items():
                if name not in bindings_before:
                    self.ctx.func.current_scope.define(name, ty)
                    self.ctx.func.nonstmt_bound_names.add(name)

        self.stmts._sync_promoted_var_types(
            set().union(*(set(b) for b in arm_bindings))
        )

        # Pre-declare variables first declared inside match arms
        if not self.ctx.func.init_terminated:
            branch_new = set(self.ctx.func.current_scope.bindings.keys()) - scope_before
            newly_assigned = self.ctx.func.definitely_assigned - assigned_before
            predecl = (branch_new & newly_assigned) - self.ctx.func.global_declarations
        else:
            predecl = set()
        arm_new = set().union(*(set(b) for b in arm_bindings)) - scope_before
        # A rebound capture hoists on its own account, whatever the arms'
        # termination: the hoist above serves post-match reads, but a rebind
        # needs the binding to be an ASSIGNMENT within its own arm -- a
        # block-scoped decl would redeclare (a loud collision at arm top level,
        # a silent shadow in a nested block). A rebound reference capture is
        # rejected in _annotate_capture_bind_modes unless a nested match is
        # what rebinds it (that re-seats the binding rather than writing
        # through it), so hoisting only relocates the declaration.
        # Only a name every binding arm agrees on the TYPE for can share one
        # hoisted slot; a name bound at different types per arm (`case
        # Cat(lives=v)` int / `case Dog(nick=v)` str) must keep its per-arm
        # block-scoped binding, or both arms would assign into one wrongly
        # typed decl.
        hoistable = {
            n for n in rebound_captures
            if all(t == capture_bind_types[n][0] for t in capture_bind_types[n])
        }
        self._reject_nested_capture_retype(stmt, hoistable, capture_bind_types)
        predecl |= ((hoistable & set(self.ctx.func.current_scope.bindings))
                    - self.ctx.func.global_declarations)
        if predecl:
            self.ctx.record_branch_decls(stmt, {
                name: self.ctx.func.current_scope.lookup(name)
                for name in sorted(predecl)
            })
            self.stmts.deduction.promote_hoisted_views(predecl, arm_new - predecl)
        self.ctx.block_locals_of[stmt] = arm_new - predecl

    def _reject_nested_capture_retype(
        self, stmt: TpyMatch, hoistable: set[str],
        capture_bind_types: dict[str, list[TpyType | None]],
    ) -> None:
        """Reject a nested match that rebinds a hoisted capture at a DIFFERENT
        type. The arms' own disagreement keeps a name block-scoped, but a
        nested match binds the same enclosing local, so the two would share one
        slot: C++ then truncates silently wherever an implicit conversion
        exists (int32 <- int64) instead of failing. The cross-arm shape is
        already rejected, so this closes the same hole on the nested route.
        """
        def nested_binds(
            body: list[TpyStmt],
        ) -> Iterator[TpyCapturePattern | TpyAsPattern]:
            for s in body:
                if isinstance(s, TpyMatch):
                    for case in s.cases:
                        yield from iter_capture_bindings(case.pattern)
                for inner in s.sub_bodies():
                    yield from nested_binds(inner)

        for case in stmt.cases:
            for node in nested_binds(case.body):
                if node.name not in hoistable:
                    continue
                outer = capture_bind_types[node.name][0]
                # An untyped side is sema's own gap, not a disagreement --
                # claiming one would reject on a message naming no type.
                if (outer is None or node.bound_type is None
                        or node.bound_type == outer):
                    continue
                raise self.ctx.error(
                    f"match capture '{node.name}' is bound as "
                    f"'{outer}' by the enclosing arm and '{node.bound_type}' "
                    f"here; a nested match rebinds the same local (a 'match' "
                    f"is not its own scope), so one binding cannot hold both. "
                    f"Bind a different name", node)

    @staticmethod
    def _capture_binds_by_value(ty: TpyType | None) -> bool:
        """A capture is copied (a durable by-value snapshot) iff its type is a
        free-copy scalar -- a trivial register move with no heap/ownership/
        view/aliasing consequence. Everything else borrows by reference."""
        if ty is None:
            return False
        bare = unwrap_readonly(ty)
        if isinstance(bare, LiteralType):
            bare = bare.base_type  # a narrowed scalar copies as freely as its base
        return is_free_copy_scalar(bare)

    def _annotate_capture_bind_modes(
        self, pattern: TpyPattern, bindings: dict[str, TpyType],
        arm_body: list[TpyStmt],
    ) -> tuple[bool, dict[str, bool]]:
        """Set `bind_by_value` on every capture node in `pattern` from its
        bound type. The flag is the single source of truth read by both codegen
        (auto vs auto&) and the dangle warning, so they cannot drift. Returns
        whether any capture binds by reference (the warning's aliasing verdict)
        and, per capture name, whether the arm rebinds it (the hoist fact) --
        both from this one walk, so the arm body is scanned once.

        A by-reference capture aliases the subject storage (`auto&`); if the
        arm REBINDS that name (`for v in xs`, `v = ...`, walrus, ...), the C++
        write goes THROUGH the alias into the subject, where CPython rebinds a
        fresh local. A value-typed capture is forced by-value (a copy the
        rebind mutates harmlessly); a reference-typed one is rejected -- the
        parity-correct render is an aliasing-then-reseated pointer local, not
        yet modeled, and neither a copy (silent divergence: pre-rebind
        mutation-through would be lost) nor the alias (write-through) matches.
        """
        aliases = False
        rebound: dict[str, bool] = {}
        for node in iter_capture_bindings(pattern):
            ty = bindings.get(node.name)
            by_value = self._capture_binds_by_value(ty)
            # Two distinct questions, one walk each. `writes` is the
            # write-through hazard (a rebind that lands in the subject via the
            # `auto&`); `rebound` is the weaker "the arm binds this name
            # again", which is all the hoist needs. A nested match's capture
            # re-seats/assigns the binding itself, so it hoists without being
            # a hazard -- forcing it by-value (or rejecting a reference
            # capture) would break shapes that render correctly today.
            writes = body_writes_name(arm_body, node.name, match_binds=False)
            rebound[node.name] = (
                writes or body_writes_name(arm_body, node.name))
            if not by_value and writes:
                bare = (unwrap_readonly(unwrap_ref_type(ty))
                        if ty is not None else None)
                # A tuple whose elements are reference/pointer-repr is
                # is_value_type() True but a blind copy would deep-copy (or
                # mis-render) the aliased elements, so it aliases like a
                # reference type -- reject it, don't copy.
                copy_safe = (
                    bare is not None and bare.is_value_type()
                    and not (isinstance(bare, TupleType)
                             and (bare.has_ref_elements()
                                  or bare.has_pointer_repr_element())))
                if copy_safe:
                    by_value = True
                else:
                    raise self.ctx.error(
                        f"match capture '{node.name}' aliases the matched "
                        f"object (a reference type, or a tuple with reference "
                        f"elements) and is rebound in this arm; rebinding it "
                        f"would corrupt that object (CPython rebinds a local). "
                        f"Bind a different name, or copy before rebinding",
                        node)
            node.bind_by_value = by_value
            node.bound_type = ty
            if not by_value:
                aliases = True
        return aliases, rebound

    def _warn_arm_subject_mutation(
        self, case: TpyMatchCase, subject: TpyExpr,
    ) -> None:
        """Warn when an arm with pattern bindings mutates the storage the
        bindings borrow (field reassign, container realloc): the C++
        bindings dangle where CPython would keep the old object alive.
        Syntactic check only -- aliases, calls that mutate the root
        indirectly, and user-record methods (whose invalidation verdict
        covers structural mutation only) are not seen.
        """
        subj_parts = _expr_path_parts(subject)
        if subj_parts is None:
            return
        offender, deferred_calls = self._find_subject_mutation(
            case.body, case.guard, subj_parts)
        if offender is not None:
            self.ctx.warning(
                f"'{''.join(subj_parts)}' is mutated in this arm while "
                f"pattern bindings borrow its storage; the bindings dangle "
                f"(undefined behavior). Copy the bound values before "
                f"mutating", offender)
            return
        # No syntactically-visible mutation, but a non-invalidating method call
        # on the subject root/prefix may still reassign the subject storage
        # (e.g. h.swap() doing self.pet = ...). Its readonly verdict only
        # settles in Phase 2, so defer the check to finalize_borrow_checks.
        subj_str = ''.join(subj_parts)
        for mcall in deferred_calls:
            self.expr.calls.pending_match_subject_checks.append(
                (mcall, subj_str, case))

    def _find_subject_mutation(
        self, body: list[TpyStmt], guard: TpyExpr | None,
        subj_parts: tuple[str, ...],
    ) -> 'tuple[TpyStmt | TpyExpr | None, list[TpyMethodCall]]':
        """Walk the arm once. Returns (offender, deferred_calls):
        - offender: the first statement/expression that overwrites the subject
          path (or an owner prefix) or calls an INVALIDATING method on the
          subject's base -- a syntactically-decidable dangle, warned now.
        - deferred_calls: non-invalidating method calls whose receiver
          prefix-aliases the subject; whether they reassign the subject
          (non-readonly) settles in Phase 2, so the caller defers them.
        """
        offender: 'TpyStmt | TpyExpr | None' = None
        deferred: list[TpyMethodCall] = []

        def prefix_aliases(parts: tuple[str, ...] | None) -> bool:
            if parts is None or len(parts) > len(subj_parts):
                return False
            return _parts_may_alias(parts, subj_parts[:len(parts)])

        def assign_clashes(parts: tuple[str, ...] | None) -> bool:
            # Rebinding the root NAME re-points a slot-model local/param;
            # the old storage stays alive, so bindings do not dangle.
            # Field/element targets overwrite the borrowed storage in place.
            if parts is not None and len(parts) == 1:
                return False
            return prefix_aliases(parts)

        def note_offender(node: 'TpyStmt | TpyExpr') -> None:
            nonlocal offender
            if offender is None:
                offender = node

        def scan_expr(expr: TpyExpr) -> None:
            if isinstance(expr, TpyMethodCall):
                recv = _expr_path_parts(expr.obj)
                if recv is not None and prefix_aliases(recv):
                    recv_type = self.ctx.get_expr_type(expr.obj)
                    if (recv_type is not None
                            and is_invalidating_method(
                                self.ctx,
                                unwrap_readonly(unwrap_ref_type(recv_type)),
                                expr.method)):
                        note_offender(expr)
                    else:
                        deferred.append(expr)
            for child in expr.children():
                scan_expr(child)

        def scan_stmts(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if isinstance(s, (TpyAssign, TpyAugAssign)):
                    if assign_clashes(_expr_path_parts(s.target)):
                        note_offender(s)
                if isinstance(s, TpyNestedDef):
                    continue  # different frame; deferred execution
                for e in s.exprs():
                    scan_expr(e)
                for sub in s.sub_bodies():
                    scan_stmts(sub)

        scan_stmts(body)
        if guard is not None:
            scan_expr(guard)
        return offender, deferred

    def _poly_dispatch_source(
        self, effective_type: TpyType,
    ) -> tuple[TpyType | None, int]:
        """If the match subject carries a @dynamic vtable reachable for
        runtime dispatch, return (inner_root, deref_depth); else (None, 0).

        Reuses the isinstance source detection: bare `Pet` / `Ptr[Pet]` resolve
        via ``polymorphic_source_inner`` (depth 0); an owning wrapper
        (`Box[Pet]` / `Rc[Pet]`) resolves through its `__deref__` view. Optional
        is deliberately excluded -- `Optional[Pet]` dispatch additionally needs
        a `None` arm, which is a separate (unimplemented) path."""
        if isinstance(effective_type, OptionalType):
            return None, 0
        inner = polymorphic_source_inner(effective_type, self.ctx.registry)
        if inner is not None:
            return inner, 0
        deref = deref_dispatch_inner(
            effective_type, self.expr.type_ops, self.ctx.registry)
        if deref is not None:
            return deref[0], deref[1]
        return None, 0

    def _analyze_pattern_polymorphic(
        self, pattern: TpyPattern, source_inner: TpyType,
        seen_types: set[str], seen_poly: list[TpyType],
        bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Analyze a pattern against a @dynamic / polymorphic subject."""
        if isinstance(pattern, TpyWildcardPattern):
            return

        elif isinstance(pattern, TpyCapturePattern):
            # A bare capture does not narrow, so it binds the base type.
            bindings[pattern.name] = source_inner

        elif isinstance(pattern, TpyAsPattern):
            self._analyze_pattern_polymorphic(
                pattern.pattern, source_inner, seen_types, seen_poly,
                bindings, stmt,
            )
            if (isinstance(pattern.pattern, TpyClassPattern)
                    and pattern.pattern.resolved_type is not None):
                bindings[pattern.name] = pattern.pattern.resolved_type
            else:
                bindings[pattern.name] = source_inner

        elif isinstance(pattern, TpyClassPattern):
            self._analyze_class_pattern_polymorphic(
                pattern, source_inner, seen_types, seen_poly, bindings, stmt,
            )

        elif isinstance(pattern, TpyOrPattern):
            self._analyze_or_pattern_polymorphic(
                pattern, source_inner, seen_types, seen_poly, bindings, stmt,
            )

        else:
            raise self.ctx.error(
                "unsupported pattern for @dynamic / polymorphic subject", pattern
            )

    def _analyze_class_pattern_polymorphic(
        self, pattern: TpyClassPattern, source_inner: TpyType,
        seen_types: set[str], seen_poly: list[TpyType],
        bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Validate a class pattern names a subclass / conformer of the
        polymorphic root, bind its fields, and flag duplicate / unreachable
        arms."""
        if not isinstance(pattern.cls, TpyName):
            raise self.ctx.error("class pattern must use a simple name", pattern)
        cls_name = pattern.cls.name

        resolved = self._resolve_polymorphic_pattern_type(
            cls_name, source_inner, pattern)
        pattern.resolved_type = resolved

        record = self.ctx.registry.get_record(cls_name)
        if record is not None:
            self._resolve_class_pattern_fields(pattern, record, bindings)
        elif pattern.keywords or pattern.positional:
            raise self.ctx.error(
                f"type '{cls_name}' does not support field patterns", pattern
            )

        # A literal field sub-pattern (`Dog(legs=4)`) makes the arm
        # conditional: the dynamic_cast may succeed yet the field check fail,
        # so it neither duplicates a bare `Dog()` arm nor renders a later one
        # unreachable. Fold the constraint into the dedup key and keep the arm
        # out of `seen_poly` (the catch-all set for the unreachable check).
        constraint = MatchAnalyzer._field_constraint_key(pattern)
        type_key = str(resolved) + constraint
        if type_key in seen_types:
            raise self.ctx.error(
                f"duplicate case for '{cls_name}' in match statement", pattern
            )
        # A later arm whose type is a subclass-or-equal of an earlier
        # unconditional arm's type is dead: the earlier (broader) dynamic_cast
        # already caught it.
        for prior in seen_poly:
            if self.ctx.registry.is_subclass_of_or_equal(resolved, prior):
                raise self.ctx.error(
                    f"unreachable case for '{cls_name}': already matched by "
                    f"the earlier '{prior}' arm", pattern
                )
        seen_types.add(type_key)
        if not constraint:
            seen_poly.append(resolved)

    def _resolve_polymorphic_pattern_type(
        self, name: str, source_inner: TpyType, pattern: TpyClassPattern,
    ) -> NominalType:
        """Resolve a class-pattern name and validate it can match the
        polymorphic root (C++ inheritance, structural conformance, or
        subclass), mirroring isinstance's dispatch validation."""
        resolved = _resolve_concrete_type_name(name)
        record = self.ctx.registry.get_record(name)
        if resolved is None and record is not None:
            resolved = NominalType(name, _module_qname=record.qualified_name())
        if resolved is None or not isinstance(resolved, NominalType):
            raise self.ctx.error(
                f"unknown type '{name}' in match pattern", pattern
            )

        if not dynamic_dispatch_type_conforms(
                resolved, source_inner, self.stmts.protocols, self.ctx.registry):
            if is_protocol_type(source_inner):
                raise self.ctx.error(
                    f"type '{name}' does not conform to the @dynamic protocol "
                    f"'{source_inner}', so the case can never match; it must "
                    f"inherit '{source_inner}' or structurally implement its "
                    f"methods", pattern
                )
            raise self.ctx.error(
                f"type '{name}' is not a subclass of '{source_inner}'", pattern
            )
        return resolved

    def _analyze_or_pattern_polymorphic(
        self, pattern: TpyOrPattern, source_inner: TpyType,
        seen_types: set[str], seen_poly: list[TpyType],
        bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Or-pattern over polymorphic class patterns (type-test only)."""
        if len(pattern.patterns) < 2:
            raise self.ctx.error(
                "or-pattern must have at least 2 alternatives", pattern
            )
        for alt in pattern.patterns:
            if not isinstance(alt, TpyClassPattern):
                raise self.ctx.error(
                    "or-pattern alternatives in a @dynamic / polymorphic match "
                    "must be class patterns", alt
                )
            # The alternatives narrow to distinct subclasses, so a binding
            # would have no single well-typed value -- disallow rather than
            # silently dropping it.
            if alt.keywords or alt.positional:
                raise self.ctx.error(
                    "field bindings are not supported in or-pattern "
                    "alternatives of a @dynamic / polymorphic match", alt
                )
            self._analyze_class_pattern_polymorphic(
                alt, source_inner, seen_types, seen_poly, bindings, stmt,
            )

    def _match_case_narrowing_facts(
        self, pattern: TpyPattern, subject_name: str | None,
        subject_type: TpyType,
    ) -> dict[str, TpyType]:
        """Compute narrowing facts for a match case pattern."""
        if subject_name is None:
            return {}
        # Unwrap as-pattern to get inner
        inner = pattern
        if isinstance(inner, TpyAsPattern):
            inner = inner.pattern
        if isinstance(inner, TpyClassPattern) and inner.resolved_type is not None:
            return {subject_name: inner.resolved_type}
        return {}

    def _merge_match_arms(
        self, arm_states: list['FlowFacts'], before: 'FlowFacts',
        can_fall_through: bool = False,
    ) -> None:
        """Merge flow states from multiple match arms.

        Uses the same logic as merge_branches: intersect definitely_assigned
        across non-terminated arms, union across terminated arms. When the
        match is non-exhaustive (`can_fall_through`), the pre-match state is
        one of the merged paths.
        """
        states = list(arm_states)
        if can_fall_through:
            states.append(before)
        if not states:
            self.stmts.init.restore(before)
            return
        if len(states) == 1:
            self.stmts.init.restore(states[0])
            return
        # Pairwise merge: merge first two, then merge result with next, etc.
        self.stmts.init.restore(states[0])
        for i in range(1, len(states)):
            current = self.stmts.init.save()
            self.stmts.init.restore(before)
            self.stmts.init.merge_branches(current, states[i])

    def _match_missing_cases(
        self, subject_type: TpyType,
        seen_types: set[str], seen_values: set[object],
    ) -> list[str]:
        """Return human-readable names of uncovered cases for finite-valued types."""
        if isinstance(subject_type, UnionType):
            return [
                "None" if isinstance(m, NoneType) else str(m)
                for m in subject_type.members
                if str(m) not in seen_types
            ]

        if is_enum_type(subject_type):
            einfo = enum_info_of(subject_type)
            return [
                f"{subject_type.name}.{name}"
                for name in einfo.members
                if (subject_type.qualified_name(), name) not in seen_values
            ]

        if is_bool_type(subject_type):
            missing: list[str] = []
            if True not in seen_values:
                missing.append("True")
            if False not in seen_values:
                missing.append("False")
            return missing

        if isinstance(subject_type, LiteralType):
            all_values = {v.value for v in subject_type.values}
            missing = sorted(
                (str(v) if not isinstance(v, str) else f'"{v}"')
                for v in all_values if v not in seen_values
            )
            return missing

        if isinstance(subject_type, NominalType) and subject_type.is_user_record:
            return [None]  # type: ignore[list-item]  # sentinel: no enumerable missing cases

        # Non-enumerable subjects (int/str/float/char/...): exhaustiveness
        # cannot be proven from literal arms, so a match without a catch-all
        # must not be marked exhaustive (codegen would emit an unreachable
        # tail on the fall-through path).
        return [None]  # type: ignore[list-item]

    def _analyze_pattern(
        self, pattern: TpyPattern, subject_type: UnionType,
        seen_types: set[str], bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Analyze a pattern against the subject type and collect bindings."""
        if isinstance(pattern, TpyWildcardPattern):
            return

        elif isinstance(pattern, TpyCapturePattern):
            bindings[pattern.name] = subject_type

        elif isinstance(pattern, TpyAsPattern):
            # `case None as x:` has no value to bind (NoneType is monostate);
            # reject rather than silently dropping the binding.
            if (isinstance(pattern.pattern, TpyLiteralPattern)
                    and pattern.pattern.value is None):
                raise self.ctx.error(
                    "'as' binding not allowed on 'case None:'", pattern,
                )
            self._analyze_pattern(pattern.pattern, subject_type, seen_types, bindings, stmt)
            # Bind as-variable to narrowed type when inner pattern is a class
            if isinstance(pattern.pattern, TpyClassPattern) and pattern.pattern.resolved_type is not None:
                bindings[pattern.name] = pattern.pattern.resolved_type
            else:
                bindings[pattern.name] = subject_type

        elif isinstance(pattern, TpyClassPattern):
            self._analyze_class_pattern(pattern, subject_type, seen_types, bindings, stmt)

        elif isinstance(pattern, TpyOrPattern):
            self._analyze_or_pattern(pattern, subject_type, seen_types, bindings, stmt, kind=OrPatternKind.UNION)

        elif isinstance(pattern, TpyLiteralPattern) and pattern.value is None:
            if not subject_type.has_none_member():
                raise self.ctx.error(
                    f"'case None:' requires None to be a member of the "
                    f"union subject; got '{subject_type}'", pattern,
                )
            none_key = str(NoneType())
            if none_key in seen_types:
                raise self.ctx.error(
                    "duplicate 'case None:' arm", pattern,
                )
            seen_types.add(none_key)
            pattern.resolved_type = NoneType()

        else:
            raise self.ctx.error(
                "Unsupported pattern type in match on union", pattern
            )

    def _analyze_class_pattern(
        self, pattern: TpyClassPattern, subject_type: UnionType,
        seen_types: set[str], bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Analyze a class pattern: validate union membership and field bindings."""
        if not isinstance(pattern.cls, TpyName):
            raise self.ctx.error(
                "class pattern must use a simple name", pattern
            )
        cls_name = pattern.cls.name

        # Resolve to a type and find matching union member.
        # Try exact match first (records, primitives), then fall back to
        # searching union members by base name (handles parameterized types
        # like list[Tree], Box[str] matched by bare list(), Box()).
        record = self.ctx.registry.get_record(cls_name)
        resolved_type = self._resolve_pattern_type(
            cls_name, subject_type, record is not None, pattern, stmt)

        pattern.resolved_type = resolved_type

        if record is not None:
            self._resolve_class_pattern_fields(pattern, record, bindings)
        elif pattern.keywords:
            raise self.ctx.error(
                f"type '{cls_name}' does not support field patterns", pattern
            )

        # Build type key for duplicate detection.
        # Include union field guards so that e.g. Box(value=Cat()) and
        # Box(value=Dog()) on Box[Cat|Dog] are distinct cases.
        type_key = self._build_pattern_type_key(pattern, resolved_type)
        if type_key in seen_types:
            raise self.ctx.error(
                f"duplicate case for '{cls_name}' in match statement", pattern
            )
        seen_types.add(type_key)

    def _resolve_pattern_type(
        self, name: str, subject_type: UnionType,
        is_record: bool, pattern: TpyClassPattern, stmt: TpyMatch,
    ) -> TpyType:
        """Resolve a type name in a match class pattern against a union subject.

        Resolution order:
        1. Exact match: record NominalType or primitive (int32, str, bool, ...)
        2. Name-based member search: find the union member whose base name
           matches (handles parameterized types like list[T], Box[str])

        `subject_type` may be a stand-in union synthesized from a wrapper's
        alternatives, which has no user-facing spelling, so rejections name
        `stmt.subject_type` -- the type as written at the match.
        """
        resolved = _resolve_concrete_type_name(name)
        if resolved is None and is_record:
            # Mint qname so exact-match against union members (line 441
            # below) works under strict NominalType equality.  Without
            # the qname, the match fails for cross-module records and
            # we fall through to the short-name fuzzy path.
            info = self.ctx.registry.get_record(name)
            qname = info.qualified_name() if info else None
            resolved = NominalType(name, _module_qname=qname)
        # Check exact match against union members
        if resolved is not None:
            if any(m == resolved for m in subject_type.members):
                return resolved
            same_name = [m for m in subject_type.members
                         if getattr(m, 'name', None) == name]
            if not same_name:
                raise self.ctx.error(
                    f"'{name}' is not a member of union "
                    f"'{stmt.subject_type}'", pattern
                )
            # A same-bare-named member that is NOT parameterized is a distinct
            # (e.g. alias-imported) record: exact qname identity already failed
            # above, so a bare-name match here would bind the wrong record.
            # Only the base-name fuzzy path below -- for parameterized members
            # like `Box[str]` under a bare `Box` pattern -- may match by name.
            if not any(getattr(m, 'type_args', ()) for m in same_name):
                member = same_name[0]
                pat_disp = (resolved.qualified_name()
                            if isinstance(resolved, NominalType) else None) or name
                subj_disp = (member.qualified_name()
                             if isinstance(member, NominalType) else None) or name
                raise self.ctx.error(
                    f"class pattern '{pat_disp}' does not match union member "
                    f"'{subj_disp}' (different type, same name)", pattern
                )

        # Fall back: search union members by base name (for parameterized types)
        matches = [m for m in subject_type.members
                   if getattr(m, 'name', None) == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Try disambiguation using field type sub-patterns
            # (e.g. Box(value=str()) narrows Box[str] | Box[int] to Box[str])
            disambiguated = self._disambiguate_by_field_types(
                name, matches, pattern)
            if disambiguated is not None:
                return disambiguated
            raise self.ctx.error(
                f"ambiguous '{name}' pattern: union has multiple {name} members",
                pattern,
            )
        raise self.ctx.error(f"unknown type '{name}' in match pattern", pattern)

    def _analyze_class_pattern_record(
        self, pattern: TpyClassPattern, subject_type: NominalType,
        bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Analyze a class pattern on a concrete record subject (field-value matching)."""
        if not isinstance(pattern.cls, TpyName):
            raise self.ctx.error(
                "class pattern must use a simple name", pattern
            )
        cls_name = pattern.cls.name

        record = self.ctx.registry.get_record(cls_name)
        if record is None:
            raise self.ctx.error(f"unknown type '{cls_name}' in match pattern", pattern)

        # Compare by qualified identity, not bare name: a record sharing the
        # subject's canonical name (one imported under an alias) is a distinct
        # type, so its class pattern must not bind fields against the subject.
        # The pattern name is a source spelling (maybe an alias), so compare
        # the resolved record's qname to the subject's rather than names.
        pat_qname = record.qualified_name()
        subj_qname = subject_type.qualified_name()
        mismatch = (pat_qname != subj_qname if pat_qname and subj_qname
                    else cls_name != subject_type.name)
        if mismatch:
            same_bare = cls_name == subject_type.name
            pat_disp = (pat_qname or cls_name) if same_bare else cls_name
            subj_disp = (subj_qname or subject_type.name) if same_bare else subject_type.name
            raise self.ctx.error(
                f"class pattern '{pat_disp}' does not match "
                f"subject type '{subj_disp}'", pattern
            )

        pattern.resolved_type = subject_type
        self._resolve_class_pattern_fields(pattern, record, bindings)

    def _resolve_class_pattern_fields(
        self, pattern: TpyClassPattern, record: RecordInfo,
        bindings: dict[str, TpyType],
    ) -> None:
        """Resolve positional patterns and validate keyword field bindings."""
        cls_name = pattern.cls.name if isinstance(pattern.cls, TpyName) else "?"

        # Use match_args for positional resolution (if set by macro), own fields otherwise.
        # For field type lookup, include inherited fields.
        all_fields = self.ctx.registry.get_all_fields(record)
        match_args = record.match_args if record.match_args is not None else tuple(
            f.name for f in record.fields
        )

        # Resolve positional patterns to keyword patterns via match_args order
        if pattern.positional:
            n = len(match_args)
            if len(pattern.positional) > n:
                noun = "positional pattern" if n == 1 else "positional patterns"
                raise self.ctx.error(
                    f"'{cls_name}' accepts {n} {noun} "
                    f"but {len(pattern.positional)} were given", pattern
                )
            kwd_names = {name for name, _ in pattern.keywords}
            resolved: list[tuple[str, TpyPattern]] = []
            for i, sub_pat in enumerate(pattern.positional):
                field_name = match_args[i]
                if field_name in kwd_names:
                    raise self.ctx.error(
                        f"field '{field_name}' is bound both positionally and by keyword "
                        f"in pattern for '{cls_name}'", pattern
                    )
                resolved.append((field_name, sub_pat))
            resolved.extend(pattern.keywords)
            pattern.keywords = resolved
            pattern.positional = []

        # Build type param substitution map for generic records
        type_subst = self._build_type_subst(record, pattern.resolved_type)

        # Validate keyword field bindings
        for field_name, sub_pattern in pattern.keywords:
            field_info = None
            for f in all_fields:
                if f.name == field_name:
                    field_info = f
                    break
            if field_info is None:
                raise self.ctx.error(
                    f"'{cls_name}' has no field '{field_name}'", pattern
                )
            # Resolve field type with type param substitution
            field_type = _subst_type_params(field_info.type, type_subst) if type_subst else field_info.type
            # Sub-pattern bindings
            if isinstance(sub_pattern, TpyCapturePattern):
                bindings[sub_pattern.name] = field_type
            elif isinstance(sub_pattern, TpyWildcardPattern):
                pass
            elif isinstance(sub_pattern, TpyLiteralPattern):
                if sub_pattern.value is None:
                    self._check_none_field_nullable(field_name, field_type, sub_pattern)
                else:
                    self._validate_literal_pattern(
                        sub_pattern, field_type,
                        where=f"field '{field_name}' of type")
            elif isinstance(sub_pattern, TpyValuePattern):
                # Named-constant field comparison (e.g. `size=Size.BIG`) is not
                # yet emitted; only literal field comparisons are. Point at the
                # guard form, which expresses the same test.
                raise self.ctx.error(
                    f"comparing field '{field_name}' against a named constant "
                    f"is not supported in a class pattern; use a guard instead, "
                    f"e.g. `case {cls_name}() if <subject>.{field_name} == ...:`",
                    sub_pattern,
                )
            elif isinstance(sub_pattern, TpyClassPattern):
                # Type sub-pattern (e.g., value=str()) -- type guard
                matched_type = self._validate_field_type_pattern(sub_pattern, field_type, pattern)
                # Recursively validate nested field patterns
                if sub_pattern.keywords or sub_pattern.positional:
                    self._resolve_nested_class_fields(sub_pattern, matched_type, bindings)
            elif isinstance(sub_pattern, TpyAsPattern):
                inner_sub = sub_pattern.pattern
                if isinstance(inner_sub, TpyClassPattern):
                    # Type sub-pattern with capture (e.g., value=str() as v)
                    matched_type = self._validate_field_type_pattern(inner_sub, field_type, pattern)
                    bindings[sub_pattern.name] = matched_type
                    # Recursively validate nested field patterns
                    if inner_sub.keywords or inner_sub.positional:
                        self._resolve_nested_class_fields(inner_sub, matched_type, bindings)
                elif isinstance(inner_sub, TpyLiteralPattern):
                    if inner_sub.value is None:
                        self._check_none_field_nullable(field_name, field_type, inner_sub)
                    else:
                        self._validate_literal_pattern(
                            inner_sub, field_type,
                            where=f"field '{field_name}' of type")
                    bindings[sub_pattern.name] = field_type
                elif isinstance(inner_sub, (TpyWildcardPattern, TpyCapturePattern)):
                    bindings[sub_pattern.name] = field_type
                    if isinstance(inner_sub, TpyCapturePattern):
                        bindings[inner_sub.name] = field_type
                else:
                    raise self.ctx.error(
                        "Unsupported sub-pattern in field binding", sub_pattern
                    )
            else:
                raise self.ctx.error(
                    "Unsupported sub-pattern in field binding", sub_pattern
                )

    def _check_none_field_nullable(
        self, field_name: str, field_type: TpyType, pattern: TpyPattern,
    ) -> None:
        """A `field=None` sub-pattern only makes sense when the field can hold
        None; on a non-nullable field it can never match (CPython) and codegen
        has no repr to emit a check against (it would silently match all)."""
        if not (isinstance(field_type, OptionalType)
                or (isinstance(field_type, UnionType)
                    and field_type.has_none_member())):
            raise self.ctx.error(
                f"field '{field_name}' of type '{field_type}' cannot be None, "
                f"so `{field_name}=None` can never match; drop the arm or use "
                f"a guard", pattern,
            )

    def _analyze_pattern_nonunion(
        self, pattern: TpyPattern, subject_type: TpyType,
        seen_values: set[object], bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Analyze a pattern for non-union subjects (enum, primitive, literal, wildcard, capture, as)."""
        if isinstance(pattern, TpyWildcardPattern):
            return

        elif isinstance(pattern, TpyCapturePattern):
            bindings[pattern.name] = subject_type

        elif isinstance(pattern, TpyAsPattern):
            self._analyze_pattern_nonunion(
                pattern.pattern, subject_type, seen_values, bindings, stmt,
            )
            bindings[pattern.name] = subject_type

        elif isinstance(pattern, TpyLiteralPattern):
            self._validate_literal_pattern(pattern, subject_type)
            self._check_duplicate_literal(pattern, seen_values)

        elif isinstance(pattern, TpyValuePattern):
            self._validate_value_pattern(pattern, subject_type)
            self._check_duplicate_value(pattern, seen_values, subject_type)

        elif isinstance(pattern, TpyOrPattern):
            self._analyze_or_pattern(pattern, subject_type, seen_values, bindings, stmt, kind=OrPatternKind.NONUNION)

        else:
            raise self.ctx.error(
                f"Unsupported pattern for {subject_type} subject", pattern
            )

    def _analyze_pattern_record(
        self, pattern: TpyPattern, subject_type: NominalType,
        bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Analyze a pattern for concrete record subjects (field-value matching)."""
        if isinstance(pattern, TpyWildcardPattern):
            return

        elif isinstance(pattern, TpyCapturePattern):
            bindings[pattern.name] = subject_type

        elif isinstance(pattern, TpyAsPattern):
            self._analyze_pattern_record(
                pattern.pattern, subject_type, bindings, stmt,
            )
            bindings[pattern.name] = subject_type

        elif isinstance(pattern, TpyClassPattern):
            self._analyze_class_pattern_record(pattern, subject_type, bindings, stmt)

        elif isinstance(pattern, TpyOrPattern):
            self._analyze_or_pattern(
                pattern, subject_type, set(), bindings, stmt, kind=OrPatternKind.RECORD,
            )

        else:
            raise self.ctx.error(
                f"Unsupported pattern for record subject '{subject_type.name}'", pattern
            )

    def _analyze_pattern_optional(
        self, pattern: TpyPattern, subject_type: OptionalType,
        seen_values: set[object], bindings: dict[str, TpyType], stmt: TpyMatch,
        none_covered: bool = True,
    ) -> None:
        """Analyze a pattern for Optional subjects.

        `none_covered`: an earlier unguarded arm already matched the None
        side. A wildcard/capture arm matches None too (CPython), so its
        binding carries the full Optional type unless None is already
        covered -- then it soundly narrows to the inner type.
        """
        if isinstance(pattern, TpyWildcardPattern):
            return

        elif isinstance(pattern, TpyCapturePattern):
            if none_covered:
                bindings[pattern.name] = subject_type.inner
            else:
                pattern.binds_full_optional = True
                bindings[pattern.name] = subject_type

        elif isinstance(pattern, TpyAsPattern):
            # `case None as x:` has no value to bind; reject rather than
            # silently binding the inner type (mirrors the union path).
            if (isinstance(pattern.pattern, TpyLiteralPattern)
                    and pattern.pattern.value is None):
                raise self.ctx.error(
                    "'as' binding not allowed on 'case None:'", pattern,
                )
            self._analyze_pattern_optional(
                pattern.pattern, subject_type, seen_values, bindings, stmt,
                none_covered=none_covered,
            )
            if (isinstance(pattern.pattern,
                           (TpyWildcardPattern, TpyCapturePattern))
                    and not none_covered):
                pattern.binds_full_optional = True
                bindings[pattern.name] = subject_type
            else:
                bindings[pattern.name] = subject_type.inner

        elif isinstance(pattern, TpyLiteralPattern):
            if pattern.value is None:
                self._check_duplicate_literal(pattern, seen_values)
            else:
                # Literal match on the inner type (e.g. case 42: on Optional[int32])
                self._validate_literal_pattern(pattern, subject_type.inner)
                self._check_duplicate_literal(pattern, seen_values)

        elif isinstance(pattern, TpyValuePattern):
            # Value pattern on inner type (e.g. case Color.RED: on Optional[Color])
            self._validate_value_pattern(pattern, subject_type.inner)
            self._check_duplicate_value(pattern, seen_values, subject_type.inner)

        elif isinstance(pattern, TpyClassPattern):
            # Class pattern on the inner type (e.g. case Point(): on Optional[Point])
            inner = subject_type.inner
            if isinstance(inner, NominalType) and inner.is_user_record:
                record = self.ctx.registry.get_record(inner.name)
                if record is not None:
                    self._analyze_class_pattern_record(pattern, inner, bindings, stmt)
                    return
            raise self.ctx.error(
                f"class pattern not valid for Optional inner type '{inner}'",
                pattern,
            )

        elif isinstance(pattern, TpyOrPattern):
            self._analyze_or_pattern(
                pattern, subject_type, seen_values, bindings, stmt, kind=OrPatternKind.OPTIONAL,
            )

        else:
            raise self.ctx.error(
                "Unsupported pattern for Optional subject", pattern
            )

    def _analyze_or_pattern(
        self, pattern: TpyOrPattern, subject_type: TpyType,
        seen: set, bindings: dict[str, TpyType], stmt: TpyMatch,
        kind: OrPatternKind,
    ) -> None:
        """Analyze an or-pattern: all alternatives must bind same variables with compatible types."""
        if len(pattern.patterns) < 2:
            raise self.ctx.error("or-pattern must have at least 2 alternatives", pattern)

        first_bindings: dict[str, TpyType] | None = None
        for alt in pattern.patterns:
            alt_bindings: dict[str, TpyType] = {}
            if kind is OrPatternKind.UNION:
                self._analyze_pattern(alt, subject_type, seen, alt_bindings, stmt)
            elif kind is OrPatternKind.RECORD:
                self._analyze_pattern_record(alt, subject_type, alt_bindings, stmt)
            elif kind is OrPatternKind.OPTIONAL:
                self._analyze_pattern_optional(alt, subject_type, seen, alt_bindings, stmt)
            else:
                self._analyze_pattern_nonunion(alt, subject_type, seen, alt_bindings, stmt)

            if first_bindings is None:
                first_bindings = alt_bindings
            else:
                # Check same variable names
                if set(alt_bindings.keys()) != set(first_bindings.keys()):
                    missing = set(first_bindings.keys()) - set(alt_bindings.keys())
                    extra = set(alt_bindings.keys()) - set(first_bindings.keys())
                    if missing:
                        raise self.ctx.error(
                            f"variable(s) {', '.join(sorted(missing))} not bound "
                            f"in all alternatives of or-pattern", alt
                        )
                    if extra:
                        raise self.ctx.error(
                            f"variable(s) {', '.join(sorted(extra))} not bound "
                            f"in all alternatives of or-pattern", alt
                        )
                # Check compatible types
                for name, ty in alt_bindings.items():
                    first_ty = first_bindings[name]
                    if ty != first_ty:
                        raise self.ctx.error(
                            f"variable '{name}' has type '{first_ty}' in first alternative "
                            f"but '{ty}' in another", alt
                        )

        if kind is OrPatternKind.UNION:
            self._check_union_or_alternatives(pattern)

        if first_bindings:
            bindings.update(first_bindings)

    def _check_union_or_alternatives(self, pattern: TpyOrPattern) -> None:
        """Reject an or-pattern alternative a union subject cannot dispatch on.

        Both union tiers select an or-group alternative by the variant index
        of the member CLASS it names and stack nothing else, so `case A() |
        None:` is refused here rather than miscompiled. `case None:` on its
        own arm is fine -- the None arm dispatches on its own variant index.
        That the or-group could stack the same label is the gap filed as
        BUGS.md#or-pattern-none-alt-union.

        Runs after the binding pass so a binding disagreement between
        alternatives, which points at the more specific mistake, still
        reports first.
        """
        # A wildcard or capture alternative makes the whole group irrefutable,
        # so the emitters dispatch it as the switch default and never ask the
        # other alternatives for a label. Nothing is undispatchable there, and
        # rejecting it would refuse a program CPython accepts.
        if any(isinstance(alt, (TpyWildcardPattern, TpyCapturePattern))
               for alt in pattern.patterns):
            return
        for alt in pattern.patterns:
            if isinstance(alt, (TpyClassPattern, TpyWildcardPattern,
                                TpyCapturePattern)):
                continue
            raise self.ctx.error(
                "unsupported alternative in an or-pattern over a union "
                "subject; every alternative must name "
                "a union member class -- give 'None' or a literal its own "
                "'case' arm", alt
            )

    def _reject_unrepresentable_literal(
        self, pattern: TpyLiteralPattern, check_type: TpyType,
        subject_type: TpyType, where: str, kind: str,
        spelled: object, value: int,
    ) -> None:
        """Reject an integral literal the slot cannot hold.

        A slot that cannot represent the value can never equal it, so the arm
        is statically dead -- the same fact the non-integral float arm above
        reports, one axis over. CPython runs such an arm as dead code; TPy
        reports it, because the label or comparison it would emit is what the
        toolchain refuses (a `case 300:` in a `switch (int8_t)`, a `== 2` on a
        bool). Only the fixed-width families answer: 'BigInt' is unbounded,
        and a float slot holds the value approximately but genuinely.
        `spelled` is the literal as written, so a folded float still names its
        own source spelling."""
        if is_bool_type(check_type):
            if value in (0, 1):
                return
            holds = "0 and 1"
        elif is_fixed_int_type(check_type):
            if fixed_int_range_contains(check_type, value):
                return
            traits = int_traits_of(check_type)
            holds = f"{traits.min_value}..{traits.max_value}"
        else:
            return
        raise self.ctx.error(
            f"{kind} literal pattern {spelled!r} can never match {where} "
            f"'{subject_type}', which holds {holds}; drop the arm, or "
            f"compare in a guard (e.g. `case _ if <subject> == {spelled!r}:`)",
            pattern,
        )

    def _validate_literal_pattern(
        self, pattern: TpyLiteralPattern, subject_type: TpyType,
        where: str = "subject type",
    ) -> None:
        """Validate that a literal pattern is compatible with the subject type.

        `where` names what the literal is being compared against, so a field
        sub-pattern reads as a field rather than as the match subject.
        """
        # Descend to the type the emitted comparison actually reads. Every
        # wrapper stripped here still renders as `<value> == <literal>`: a
        # readonly/borrowed view compares by value, and an empty
        # `std::optional` compares false, which is CPython's answer for a
        # None-valued slot. Enumerating the wrappers instead of descending is
        # what let a wrapped type reach the kind checks unrecognized.
        check_type = subject_type
        while True:
            stripped = _strip_match_qualifiers(check_type)
            if isinstance(stripped, LiteralType):
                stripped = stripped.base_type
            elif isinstance(stripped, OptionalType):
                stripped = stripped.inner
            if stripped == check_type:
                break
            check_type = stripped
        val = pattern.value
        if val is None:
            raise self.ctx.error(
                "None literal pattern requires an Optional subject", pattern
            )
        # A UnionType that still carries None has at least two non-None
        # members (`make_union` collapses the single-member form to
        # OptionalType), so the emitted flat `== <literal>` would be applied
        # to a std::variant and not compile. CPython does match here.
        if isinstance(check_type, (UnionType, AliasRef,
                                   RecursiveAliasInstanceType)):
            raise self.ctx.error(
                f"literal pattern not yet implemented for union {where} "
                f"'{subject_type}'; select the member with a class pattern "
                f"first, or compare in a guard instead",
                pattern,
            )
        if isinstance(val, bool):
            # PEP 634 compares a True/False pattern by IDENTITY, not `==`, so
            # it matches a bool subject and nothing else -- `match 1: case
            # True:` does not fire in CPython. Admitting the cross-kind form
            # as `== true` would match where CPython does not, so the arm that
            # can never match is reported instead of silently diverging.
            if not is_bool_type(check_type):
                raise self.ctx.error(
                    f"bool literal pattern can never match {where} "
                    f"'{subject_type}': 'True'/'False' patterns compare by "
                    f"identity, so they only match a 'bool'; use '1'/'0' to "
                    f"compare numerically",
                    pattern,
                )
        elif isinstance(val, (int, float)):
            kind = "float" if isinstance(val, float) else "int"
            # CPython does match an int literal against an IntEnum, so this is
            # a gap rather than a type error -- say so, and keep it distinct
            # from the plain-Enum case, where the comparison is meaningless.
            if is_int_enum_type(check_type):
                raise self.ctx.error(
                    f"{kind} literal pattern against IntEnum "
                    f"{where} '{subject_type}' is not yet implemented; match "
                    f"the member (e.g. `case {subject_type}.MEMBER:`) or use "
                    f"a guard",
                    pattern,
                )
            # A non-bool numeric literal is compared with `==`, which is
            # numeric ACROSS bool/int/float in CPython and in the emitted C++
            # alike, so the slot only has to be numeric -- not the literal's
            # own kind. `is_numeric_type` is that one predicate, and both the
            # subject and the field caller reach it here.
            if not is_numeric_type(check_type):
                raise self.ctx.error(
                    f"{kind} literal pattern not valid for "
                    f"{where} '{subject_type}'",
                    pattern,
                )
            if isinstance(val, float) and not is_float_category(check_type):
                # The slot holds whole numbers only (fixed int, 'bool',
                # BigInt), so the pattern is either the same test spelled with
                # a decimal point or an arm that can never fire. Fold the
                # first to its int literal HERE -- the one site both the
                # subject and the field caller reach -- so every integral
                # subject carries a single label form downstream (a C++ case
                # label cannot be floating-point, and 'BigInt' has no
                # comparison against a double at all). CPython runs the
                # second as a dead arm; TPy reports it, as it does the
                # bool-identity arm.
                if not val.is_integer():
                    raise self.ctx.error(
                        f"float literal pattern {val!r} can never match "
                        f"{where} '{subject_type}', which holds whole numbers "
                        f"only; drop the arm, or compare in a guard "
                        f"(e.g. `case _ if <subject> == {val!r}:`)",
                        pattern,
                    )
                folded = int(val)
                # The range verdict comes BEFORE the fold, so an arm that can
                # never fire is one diagnostic naming what the user wrote,
                # not a "spell it N" pointing at a rejected N.
                self._reject_unrepresentable_literal(
                    pattern, check_type, subject_type, where, kind, val,
                    folded)
                self.ctx.warning(
                    f"float literal pattern {val!r} against {where} "
                    f"'{subject_type}'; spell it {folded}",
                    pattern,
                )
                pattern.value = folded
            elif isinstance(val, int):
                self._reject_unrepresentable_literal(
                    pattern, check_type, subject_type, where, kind, val, val)
        elif isinstance(val, str):
            if not is_any_str_type(check_type):
                raise self.ctx.error(
                    f"str literal pattern not valid for {where} '{subject_type}'",
                    pattern,
                )
        elif isinstance(val, bytes):
            # Against a bytes-family type the KIND matches and CPython does
            # match the arm -- TPy just cannot render the comparison yet, so
            # report a gap. Any other type is a genuine kind mismatch.
            if is_bytes_category(check_type):
                raise self.ctx.error(
                    f"bytes literal pattern against {where} "
                    f"'{subject_type}' is not yet implemented; compare in a "
                    f"guard instead (e.g. `case _ if <subject> == b\"...\":`)",
                    pattern,
                )
            raise self.ctx.error(
                f"bytes literal pattern not valid for {where} '{subject_type}'",
                pattern,
            )
        else:
            # Exhaustive: a literal kind with no arm above has no comparison
            # codegen can emit, so admitting it silently drops the arm's test.
            raise self.ctx.error(
                f"{type(val).__name__} literal pattern not valid for "
                f"{where} '{subject_type}'",
                pattern,
            )

    def _extract_literal_pattern_values(
        self, pattern: TpyPattern, lit_type: LiteralType,
    ) -> list[LiteralValue] | None:
        """Extract LiteralValues matched by a pattern. None for wildcard/capture."""
        if isinstance(pattern, TpyAsPattern):
            return self._extract_literal_pattern_values(pattern.pattern, lit_type)
        if isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
            return None
        if isinstance(pattern, TpyLiteralPattern):
            val = pattern.value
            tag = (LiteralTag.BOOL if isinstance(val, bool)
                   else LiteralTag.INT if isinstance(val, int)
                   else LiteralTag.STR)
            return [LiteralValue(tag, val)]
        if isinstance(pattern, TpyOrPattern):
            result: list[LiteralValue] = []
            for alt in pattern.patterns:
                sub = self._extract_literal_pattern_values(alt, lit_type)
                if sub is None:
                    return None
                result.extend(sub)
            return result
        return None

    def _validate_value_pattern(
        self, pattern: TpyValuePattern, subject_type: TpyType,
    ) -> None:
        """Validate a value pattern (e.g., Color.RED) against the subject type."""
        val_type = self.expr.analyze_expr(pattern.expr)
        if is_enum_type(subject_type):
            # Compare by qualified identity, not bare name: two enums sharing
            # a canonical name (one imported under an alias) are distinct, so
            # a case member from the wrong enum must not match the subject. The
            # helper's None-qname loose fallback can't fire here -- every enum
            # carries a qname by registration, and an alias rebinds the same
            # NominalType object rather than minting a bare one.
            if not is_enum_type(val_type) or not same_nominal_symbol_loose(val_type, subject_type):
                # Disambiguate with the intrinsic qname when the two enums
                # share a bare name (the aliased-import case), else "'E' does
                # not match 'E'" is meaningless. `disambiguated_pair` can't be
                # used here: it qualifies via the codegen namespace map, which
                # isn't populated at sema time, so it renders both as 'E'.
                same_bare = is_enum_type(val_type) and val_type.name == subject_type.name
                val_disp = (val_type.qualified_name() or str(val_type)) if same_bare else str(val_type)
                subj_disp = (subject_type.qualified_name() or str(subject_type)) if same_bare else str(subject_type)
                raise self.ctx.error(
                    f"value pattern type '{val_disp}' does not match "
                    f"subject type '{subj_disp}'", pattern
                )
        else:
            raise self.ctx.error(
                f"value pattern (dotted name) not valid for subject type "
                f"'{subject_type}'", pattern
            )

    def _check_duplicate_literal(
        self, pattern: TpyLiteralPattern, seen: set[object],
    ) -> None:
        """Check for duplicate literal pattern values."""
        key = pattern.value
        if key in seen:
            label = repr(key)
            raise self.ctx.error(
                f"duplicate case for {label} in match statement", pattern
            )
        seen.add(key)

    def _check_duplicate_value(
        self, pattern: TpyValuePattern, seen: set[object],
        subject_type: TpyType,
    ) -> None:
        """Check for duplicate value pattern (e.g. Color.RED)."""
        if isinstance(pattern.expr, TpyFieldAccess):
            obj_name = pattern.expr.obj.name if isinstance(pattern.expr.obj, TpyName) else "?"
            # Key by the subject enum's qualified identity, not the case's
            # source spelling: under an import alias the bare names diverge,
            # and this set is read back by `_match_missing_cases` on the same
            # `subject_type.qualified_name()` key.
            key = (subject_type.qualified_name(), pattern.expr.field)
            if key in seen:
                raise self.ctx.error(
                    f"duplicate case for '{obj_name}.{pattern.expr.field}' "
                    f"in match statement", pattern
                )
            seen.add(key)

    # ------------------------------------------------------------------
    # Nested type sub-pattern helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_pattern_type_key(
        pattern: TpyClassPattern, resolved_type: TpyType,
    ) -> str:
        """Build a key for duplicate-case / exhaustiveness detection, folding
        in field-value constraints so a conditional arm (`Dog(legs=4)`) stays
        distinct from `Dog()` / `Dog(legs=5)` and is reported as uncovered."""
        return str(resolved_type) + MatchAnalyzer._field_constraint_key(pattern)

    @staticmethod
    def _field_constraint_key(pattern: TpyClassPattern) -> str:
        """Canonical signature of a class pattern's field-value constraints
        (literals + union field guards, recursing through nested record
        sub-patterns). Empty when the pattern only binds / type-tests fields."""
        parts: list[str] = []
        for field_name, sub in pattern.keywords:
            inner = sub.pattern if isinstance(sub, TpyAsPattern) else sub
            if isinstance(inner, TpyLiteralPattern):
                parts.append(f"+{field_name}={inner.value!r}")
            elif isinstance(inner, TpyClassPattern) and inner.is_union_field_guard:
                parts.append(f"+{field_name}:{inner.resolved_type}"
                             + MatchAnalyzer._field_constraint_key(inner))
            elif isinstance(inner, TpyClassPattern):
                sub_key = MatchAnalyzer._field_constraint_key(inner)
                if sub_key:
                    parts.append(f"+{field_name}{sub_key}")
        return "".join(parts)

    @staticmethod
    def _is_constraining_sub_pattern(sub: TpyPattern) -> bool:
        """Check if a field sub-pattern adds a constraint (not always-matching)."""
        if isinstance(sub, (TpyLiteralPattern, TpyValuePattern, TpyClassPattern)):
            return True
        # An `as` binding is transparent to the test: `x=3 as v` still only
        # matches x == 3, and codegen emits that comparison. Answering False
        # here made the arm read as a catch-all, so the fallthrough became
        # `std::unreachable()` for the values the arm does not match.
        if isinstance(sub, TpyAsPattern):
            return MatchAnalyzer._is_constraining_sub_pattern(sub.pattern)
        # An or-pattern is missing on purpose: a field sub-pattern rejects it
        # before this runs, so answering False for it costs nothing today.
        # Whoever lifts that reject wants `all(...)` over the alternatives and
        # not `any(...)` -- one always-matching alternative makes the whole
        # group a catch-all.
        return False

    @staticmethod
    def _build_type_subst(
        record: 'RecordInfo', resolved_type: TpyType | None,
    ) -> dict[str, TpyType]:
        """Build type param substitution map from resolved_type's type_args."""
        if (resolved_type is None or not isinstance(resolved_type, NominalType)
                or not record.type_params or not resolved_type.type_args):
            return {}
        subst: dict[str, TpyType] = {}
        for param_name, arg in zip(record.type_params, resolved_type.type_args):
            if isinstance(arg, TpyType):
                subst[param_name] = arg
        return subst

    def _disambiguate_by_field_types(
        self, cls_name: str, candidates: list[TpyType],
        pattern: TpyClassPattern,
    ) -> TpyType | None:
        """Try to disambiguate multiple same-name union members using field type sub-patterns.

        For example, Box(value=str()) on union Box[str] | Box[int] narrows to Box[str]
        by checking which candidate's 'value' field type matches 'str'.

        Recurses into nested type sub-patterns, so Box(value=Box(value=str()))
        can disambiguate Box[Box[str]] | Box[Box[int]].

        Returns the unique matching candidate, or None if no type guards are present.
        Raises an error if type guards are present but no candidate matches.
        """
        record = self.ctx.registry.get_record(cls_name)
        if record is None:
            return None
        if not self._has_type_sub_patterns(pattern, record):
            return None

        all_fields = self.ctx.registry.get_all_fields(record)
        matching: list[TpyType] = []
        for candidate in candidates:
            if self._pattern_matches_candidate(record, candidate, all_fields, pattern):
                matching.append(candidate)

        if len(matching) == 1:
            return matching[0]
        if len(matching) == 0:
            raise self.ctx.error(
                f"no '{cls_name}' variant matches the field type pattern(s)",
                pattern,
            )
        return None

    def _has_type_sub_patterns(
        self, pattern: TpyClassPattern, record: 'RecordInfo',
    ) -> bool:
        """Check if a class pattern has any type sub-patterns (TpyClassPattern in fields)."""
        match_args = record.match_args if record.match_args is not None else tuple(
            f.name for f in record.fields
        )
        for i, sub_pat in enumerate(pattern.positional):
            if i >= len(match_args):
                break
            inner = sub_pat
            if isinstance(inner, TpyAsPattern):
                inner = inner.pattern
            if isinstance(inner, TpyClassPattern):
                return True
        for _, sub_pat in pattern.keywords:
            inner = sub_pat
            if isinstance(inner, TpyAsPattern):
                inner = inner.pattern
            if isinstance(inner, TpyClassPattern):
                return True
        return False

    def _pattern_matches_candidate(
        self, record: 'RecordInfo', candidate: TpyType,
        all_fields: list, pattern: TpyClassPattern,
    ) -> bool:
        """Recursively check if all type sub-patterns in a class pattern match a candidate."""
        subst = self._build_type_subst(record, candidate)

        # Build combined keyword list (positional resolved via match_args + explicit keywords)
        match_args = record.match_args if record.match_args is not None else tuple(
            f.name for f in record.fields
        )
        keywords: list[tuple[str, TpyPattern]] = [
            (match_args[i], sub_pat)
            for i, sub_pat in enumerate(pattern.positional)
            if i < len(match_args)
        ]
        keywords.extend(pattern.keywords)

        for field_name, sub_pat in keywords:
            inner = sub_pat
            if isinstance(inner, TpyAsPattern):
                inner = inner.pattern
            if not isinstance(inner, TpyClassPattern) or not isinstance(inner.cls, TpyName):
                continue

            type_name = inner.cls.name
            field_type = next((f.type for f in all_fields if f.name == field_name), None)
            if field_type is None:
                return False
            resolved = _subst_type_params(field_type, subst) if subst else field_type

            if not self._type_pattern_compatible(type_name, resolved, inner):
                return False
        return True

    def _type_pattern_compatible(
        self, type_name: str, field_type: TpyType,
        inner_pattern: TpyClassPattern,
    ) -> bool:
        """Check if a type name matches a field type, recursing into inner patterns."""
        # Resolve to a concrete type
        pattern_type = _resolve_concrete_type_name(type_name)
        if pattern_type is not None:
            if field_type == pattern_type:
                return True
            if isinstance(field_type, UnionType):
                return any(m == pattern_type for m in field_type.members)
            return False

        # User record: match by name
        inner_record = self.ctx.registry.get_record(type_name)
        if inner_record is None:
            return False

        # Collect matching types from field_type
        if isinstance(field_type, NominalType) and field_type.name == type_name:
            candidates = [field_type]
        elif isinstance(field_type, UnionType):
            candidates = [m for m in field_type.members
                          if isinstance(m, NominalType) and m.name == type_name]
        else:
            return False

        if not candidates:
            return False

        # If the inner pattern has further type sub-patterns, use them to narrow
        has_inner = (inner_pattern.keywords or inner_pattern.positional) and self._has_type_sub_patterns(inner_pattern, inner_record)
        if has_inner:
            inner_fields = self.ctx.registry.get_all_fields(inner_record)
            candidates = [c for c in candidates
                          if self._pattern_matches_candidate(
                              inner_record, c, inner_fields, inner_pattern)]

        return len(candidates) >= 1

    def _validate_field_type_pattern(
        self, sub_pattern: TpyClassPattern, field_type: TpyType,
        parent: TpyPattern,
    ) -> TpyType:
        """Validate a type sub-pattern against the resolved field type.

        Returns the matched type (for binding). Sets sub_pattern.resolved_type
        when the field is a union (signals codegen to emit holds_alternative).
        """
        if not isinstance(sub_pattern.cls, TpyName):
            raise self.ctx.error(
                "type pattern in field must use a simple name", sub_pattern
            )
        cls_name = sub_pattern.cls.name

        pattern_type = _resolve_concrete_type_name(cls_name)
        if pattern_type is None:
            info = self.ctx.registry.get_record(cls_name)
            if info is not None:
                pattern_type = NominalType(cls_name, _module_qname=info.qualified_name())
        if pattern_type is None:
            raise self.ctx.error(
                f"unknown type '{cls_name}' in field type pattern", sub_pattern
            )

        # Exact match: compile-time type guard (no runtime check)
        if field_type == pattern_type:
            return field_type
        if isinstance(field_type, NominalType) and field_type.name == cls_name:
            return field_type

        # Union field: check if field_type is a union containing pattern_type
        is_record = isinstance(pattern_type, NominalType)
        if isinstance(field_type, UnionType):
            if is_record:
                # Records: match by name (handles parameterized types like Box[str])
                matches = [m for m in field_type.members
                           if isinstance(m, NominalType) and m.name == cls_name]
            else:
                # Primitives: exact type match
                matches = [m for m in field_type.members if m == pattern_type]

            if len(matches) == 1:
                sub_pattern.resolved_type = matches[0]
                sub_pattern.is_union_field_guard = True
                return matches[0]

            if len(matches) > 1:
                # Multiple same-name members -- try inner patterns for disambiguation
                inner_record = self.ctx.registry.get_record(cls_name)
                if inner_record is not None and (sub_pattern.keywords or sub_pattern.positional):
                    disambiguated = self._disambiguate_by_field_types(
                        cls_name, matches, sub_pattern)
                    if disambiguated is not None:
                        sub_pattern.resolved_type = disambiguated
                        sub_pattern.is_union_field_guard = True
                        return disambiguated
                raise self.ctx.error(
                    f"ambiguous '{cls_name}' in field union type '{field_type}'",
                    sub_pattern,
                )

            raise self.ctx.error(
                f"type '{cls_name}' is not a member of "
                f"field union type '{field_type}'", sub_pattern
            )

        raise self.ctx.error(
            f"type pattern '{cls_name}' does not match "
            f"field type '{field_type}'", sub_pattern
        )

    def _resolve_nested_class_fields(
        self, sub_pattern: TpyClassPattern, matched_type: TpyType,
        bindings: dict[str, TpyType],
    ) -> None:
        """Recursively resolve field patterns on a nested class sub-pattern."""
        if not isinstance(sub_pattern.cls, TpyName):
            return
        cls_name = sub_pattern.cls.name
        record = self.ctx.registry.get_record(cls_name)
        if record is None:
            raise self.ctx.error(
                f"type '{cls_name}' does not support field patterns", sub_pattern
            )
        # resolved_type is already set by _validate_field_type_pattern for
        # union fields; for exact matches, set it for type param substitution
        if sub_pattern.resolved_type is None:
            sub_pattern.resolved_type = matched_type
        self._resolve_class_pattern_fields(sub_pattern, record, bindings)
