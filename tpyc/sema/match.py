"""
TurboPython Match/Case Semantic Analysis

Semantic analysis for match/case statements and pattern matching.
"""

from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType,
    NominalType,
    NoneType, OptionalType, UnionType, PendingStrType,
    LiteralType, LiteralValue, TypeParamRef,
    unwrap_readonly, unwrap_ref_type,
    is_float_type, is_any_str_type,
)
from ..modules import _resolve_concrete_type_name
from .flow_facts import FlowFacts
from ..type_def_registry import (
    is_bool_type, is_fixed_int_type, is_big_int_type,
    is_str_category, is_char_type, is_float_category,
    is_enum_type, enum_info_of,
)
from ..parse import (
    TpyName, TpyFieldAccess,
    TpyMatch, TpyMatchCase, TpyPattern, TpyWildcardPattern, TpyCapturePattern,
    TpyClassPattern, TpyLiteralPattern, TpyValuePattern, TpyOrPattern, TpyAsPattern,
)

if TYPE_CHECKING:
    from ..typesys import RecordInfo
    from .context import SemanticContext
    from .expressions import ExpressionAnalyzer
    from .statements import StatementAnalyzer


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
        effective_type = unwrap_ref_type(unwrap_readonly(subject_type))
        # Expand recursive union alias NominalType to its underlying UnionType
        # (bare parser placeholder; no TypeDef entry, so is_user_record is
        # False post-migration -- filter on name + non-protocol instead).
        if (isinstance(effective_type, NominalType)
                and not effective_type.is_protocol
                and effective_type.name in self.ctx.recursive_union_names):
            alias = self.ctx.registry.get_type_alias(effective_type.name)
            if alias is not None:
                effective_type = alias
        stmt.subject_type = effective_type
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
        if not (is_union or is_enum or is_primitive or is_record or is_optional):
            raise self.ctx.error(
                f"match subject must be a union, enum, primitive, record, "
                f"or Optional type, got '{effective_type}'", stmt
            )

        # Subject variable name for narrowing (only if simple name)
        subject_name: str | None = None
        if isinstance(stmt.subject, TpyName):
            subject_name = stmt.subject.name

        had_wildcard = False
        seen_types: set[str] = set()
        seen_values: set[object] = set()

        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        assigned_before = frozenset(self.ctx.func.definitely_assigned)
        bindings_before = dict(self.ctx.func.current_scope.bindings)
        ns_types_before = self.stmts._save_ns_var_types()
        before = self.stmts.init.save()

        arm_states: list[FlowFacts] = []
        arm_bindings: list[dict[str, TpyType]] = []
        consumed_before = self.ctx.func.current_consumed_own_params.copy()
        arm_consumed: list[tuple[set[str], bool]] = []  # (consumed_set, terminated)

        for case in stmt.cases:
            if had_wildcard:
                raise self.ctx.error(
                    "unreachable case after wildcard pattern", case.pattern
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
            if is_union:
                self._analyze_pattern(case.pattern, effective_type, seen_types, pattern_bindings, stmt)
            elif is_record:
                self._analyze_pattern_record(
                    case.pattern, effective_type, pattern_bindings, stmt,
                )
            elif is_optional:
                self._analyze_pattern_optional(
                    case.pattern, effective_type, seen_values, pattern_bindings, stmt,
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

            for name, ty in pattern_bindings.items():
                self.ctx.func.current_scope.define(name, ty)
                self.stmts.init.mark_assigned(name)
                if name not in self.ctx.func.var_scope_depth:
                    self.ctx.func.var_scope_depth[name] = self.ctx.func.current_scope.depth

            # Narrow subject variable for class patterns (union only)
            narrowing_facts = self._match_case_narrowing_facts(
                case.pattern, subject_name, effective_type,
            ) if is_union else {}
            if narrowing_facts:
                case.type_facts = self.stmts._filter_union_codegen_facts(narrowing_facts)
                self.ctx.func.narrowed_types.update(narrowing_facts)

            # Narrow Optional subject to inner type in non-None arms
            if is_optional and subject_name is not None:
                pat = case.pattern
                if isinstance(pat, TpyAsPattern):
                    pat = pat.pattern
                is_none_arm = isinstance(pat, TpyLiteralPattern) and pat.value is None
                if not is_none_arm:
                    self.ctx.func.narrowed_types[subject_name] = effective_type.inner

            # Narrow Literal subject to matched value(s)
            if is_literal and subject_name is not None:
                matched = self._extract_literal_pattern_values(case.pattern, effective_type)
                if matched is not None:
                    narrowed = LiteralType(effective_type.base_type, tuple(matched))
                    self.ctx.func.narrowed_types[subject_name] = narrowed
                    facts = {subject_name: narrowed}
                    case.type_facts = self.stmts._filter_union_codegen_facts(facts)

            # Analyze guard expression (pattern bindings are in scope)
            if case.guard is not None:
                self.expr.analyze_expr(case.guard)

            for s in case.body:
                self.stmts.analyze_stmt(s)

            arm_states.append(self.stmts.init.save())
            arm_consumed.append((self.ctx.func.current_consumed_own_params.copy(), self.ctx.func.init_terminated))
            arm_bindings.append(dict(self.ctx.func.current_scope.bindings))

            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            if isinstance(pat, (TpyWildcardPattern, TpyCapturePattern)) and case.guard is None:
                had_wildcard = True
            # Class pattern on concrete record with no conditions is always-matching
            elif ((is_record or is_optional) and isinstance(pat, TpyClassPattern)
                  and case.guard is None
                  and not any(self._is_constraining_sub_pattern(sub)
                              for _, sub in pat.keywords)):
                had_wildcard = True

        # Exhaustiveness check for finite-valued types
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
                    f"(add 'case _:' to suppress)"
                )
            else:
                msg = (
                    f"non-exhaustive match on '{effective_type}'; "
                    f"missing: {', '.join(missing)} "
                    f"(add 'case _:' to suppress)"
                )
            self.ctx.warning(msg, stmt)

        # Merge flow states across all arms
        self._merge_match_arms(arm_states, before)

        # Merge consumed Own[T] params: intersect non-terminated arms.
        # Terminated arms don't affect live continuation (same as if/else).
        if arm_consumed:
            live_sets = [s for s, terminated in arm_consumed if not terminated]
            dead_sets = [s for s, terminated in arm_consumed if terminated]
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
        if predecl:
            self.ctx.if_branch_decls[id(stmt)] = {
                name: self.ctx.func.current_scope.lookup(name)
                for name in sorted(predecl)
            }

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
    ) -> None:
        """Merge flow states from multiple match arms.

        Uses the same logic as merge_branches: intersect definitely_assigned
        across non-terminated arms, union across terminated arms.
        """
        if not arm_states:
            self.stmts.init.restore(before)
            return
        if len(arm_states) == 1:
            self.stmts.init.restore(arm_states[0])
            return
        # Pairwise merge: merge first two, then merge result with next, etc.
        self.stmts.init.restore(arm_states[0])
        for i in range(1, len(arm_states)):
            current = self.stmts.init.save()
            self.stmts.init.restore(before)
            self.stmts.init.merge_branches(current, arm_states[i])

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
                if (subject_type.name, name) not in seen_values
            ]

        if isinstance(subject_type, OptionalType):
            if None not in seen_values:
                return ["None"]
            return []

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

        return []

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
                f"Unsupported pattern type in match on union: "
                f"{type(pattern).__name__}", pattern
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
            cls_name, subject_type, record is not None, pattern)

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
        is_record: bool, pattern: TpyClassPattern,
    ) -> TpyType:
        """Resolve a type name in a match class pattern against a union subject.

        Resolution order:
        1. Exact match: record NominalType or primitive (Int32, str, bool, ...)
        2. Name-based member search: find the union member whose base name
           matches (handles parameterized types like list[T], Box[str])
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
            # Known type but not a union member -- give specific error,
            # unless it could match as a parameterized type (fall through)
            if not any(getattr(m, 'name', None) == name for m in subject_type.members):
                raise self.ctx.error(
                    f"'{name}' is not a member of union '{subject_type}'", pattern
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

        if cls_name != subject_type.name:
            raise self.ctx.error(
                f"class pattern '{cls_name}' does not match "
                f"subject type '{subject_type.name}'", pattern
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
                pass  # Literal comparison -- validated at codegen time
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
                    bindings[sub_pattern.name] = field_type
                elif isinstance(inner_sub, (TpyWildcardPattern, TpyCapturePattern)):
                    bindings[sub_pattern.name] = field_type
                    if isinstance(inner_sub, TpyCapturePattern):
                        bindings[inner_sub.name] = field_type
                else:
                    raise self.ctx.error(
                        f"Unsupported sub-pattern in field binding: "
                        f"{type(inner_sub).__name__}", sub_pattern
                    )
            else:
                raise self.ctx.error(
                    f"Unsupported sub-pattern in field binding: "
                    f"{type(sub_pattern).__name__}", sub_pattern
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
            self._check_duplicate_value(pattern, seen_values)

        elif isinstance(pattern, TpyOrPattern):
            self._analyze_or_pattern(pattern, subject_type, seen_values, bindings, stmt, kind=OrPatternKind.NONUNION)

        else:
            raise self.ctx.error(
                f"Unsupported pattern for {subject_type} subject: "
                f"{type(pattern).__name__}", pattern
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
                f"Unsupported pattern for record subject '{subject_type.name}': "
                f"{type(pattern).__name__}", pattern
            )

    def _analyze_pattern_optional(
        self, pattern: TpyPattern, subject_type: OptionalType,
        seen_values: set[object], bindings: dict[str, TpyType], stmt: TpyMatch,
    ) -> None:
        """Analyze a pattern for Optional subjects."""
        if isinstance(pattern, TpyWildcardPattern):
            return

        elif isinstance(pattern, TpyCapturePattern):
            # Capture matches the non-None value (case None: is a literal pattern)
            bindings[pattern.name] = subject_type.inner

        elif isinstance(pattern, TpyAsPattern):
            self._analyze_pattern_optional(
                pattern.pattern, subject_type, seen_values, bindings, stmt,
            )
            bindings[pattern.name] = subject_type.inner

        elif isinstance(pattern, TpyLiteralPattern):
            if pattern.value is None:
                self._check_duplicate_literal(pattern, seen_values)
            else:
                # Literal match on the inner type (e.g. case 42: on Optional[Int32])
                self._validate_literal_pattern(pattern, subject_type.inner)
                self._check_duplicate_literal(pattern, seen_values)

        elif isinstance(pattern, TpyValuePattern):
            # Value pattern on inner type (e.g. case Color.RED: on Optional[Color])
            self._validate_value_pattern(pattern, subject_type.inner)
            self._check_duplicate_value(pattern, seen_values)

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
                f"Unsupported pattern for Optional subject: "
                f"{type(pattern).__name__}", pattern
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

        if first_bindings:
            bindings.update(first_bindings)

    def _validate_literal_pattern(
        self, pattern: TpyLiteralPattern, subject_type: TpyType,
    ) -> None:
        """Validate that a literal pattern is compatible with the subject type."""
        # Unwrap LiteralType to base_type for validation
        check_type = subject_type.base_type if isinstance(subject_type, LiteralType) else subject_type
        val = pattern.value
        if val is None:
            raise self.ctx.error(
                "None literal pattern requires an Optional subject", pattern
            )
        if isinstance(val, bool):
            if not is_bool_type(check_type):
                raise self.ctx.error(
                    f"bool literal pattern not valid for subject type '{subject_type}'",
                    pattern,
                )
        elif isinstance(val, int):
            if not (is_fixed_int_type(check_type) or is_big_int_type(check_type)
                    or is_enum_type(check_type)):
                raise self.ctx.error(
                    f"int literal pattern not valid for subject type '{subject_type}'",
                    pattern,
                )
        elif isinstance(val, float):
            if not is_float_type(check_type):
                raise self.ctx.error(
                    f"float literal pattern not valid for subject type '{subject_type}'",
                    pattern,
                )
        elif isinstance(val, str):
            if not is_any_str_type(check_type):
                raise self.ctx.error(
                    f"str literal pattern not valid for subject type '{subject_type}'",
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
            tag = "bool" if isinstance(val, bool) else "int" if isinstance(val, int) else "str"
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
            if not is_enum_type(val_type) or val_type.name != subject_type.name:
                raise self.ctx.error(
                    f"value pattern type '{val_type}' does not match "
                    f"subject type '{subject_type}'", pattern
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
    ) -> None:
        """Check for duplicate value pattern (e.g. Color.RED)."""
        if isinstance(pattern.expr, TpyFieldAccess):
            obj_name = pattern.expr.obj.name if isinstance(pattern.expr.obj, TpyName) else "?"
            key = (obj_name, pattern.expr.field)
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
        """Build a key for duplicate-case detection that includes union field guards."""
        key = str(resolved_type)
        for _, sub in pattern.keywords:
            inner = sub
            if isinstance(inner, TpyAsPattern):
                inner = inner.pattern
            if isinstance(inner, TpyClassPattern) and inner.is_union_field_guard:
                key += f"+{inner.resolved_type}"
        return key

    @staticmethod
    def _is_constraining_sub_pattern(sub: TpyPattern) -> bool:
        """Check if a field sub-pattern adds a constraint (not always-matching)."""
        if isinstance(sub, (TpyLiteralPattern, TpyValuePattern, TpyClassPattern)):
            return True
        if isinstance(sub, TpyAsPattern) and isinstance(sub.pattern, TpyClassPattern):
            return True
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
