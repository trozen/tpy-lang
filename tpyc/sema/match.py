"""
TurboPython Match/Case Semantic Analysis

Semantic analysis for match/case statements and pattern matching.
"""

from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, FixedIntType, FloatType, Float32Type,
    BoolType, StrType, StrViewType, StringType, CharType, NamedType,
    NoneType, OptionalType, UnionType, EnumType, PendingStrType,
    LiteralType, LiteralValue,
    unwrap_readonly,
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


class OrPatternKind(Enum):
    UNION = "union"
    NONUNION = "nonunion"
    RECORD = "record"
    OPTIONAL = "optional"


class MatchAnalyzer:
    """Semantic analysis for match/case statements."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx
        # Set via set_dependencies()
        self.stmts: StatementAnalyzer | None = None
        self.expr: ExpressionAnalyzer | None = None

    def set_dependencies(self, stmts: StatementAnalyzer, expr: ExpressionAnalyzer) -> None:
        self.stmts = stmts
        self.expr = expr

    def analyze_match(self, stmt: TpyMatch) -> None:
        """Analyze a match/case statement."""
        from .flow_facts import FlowFacts

        subject_type = self.expr.analyze_expr(stmt.subject)
        stmt.subject_type = subject_type

        effective_type = unwrap_readonly(subject_type)
        is_union = isinstance(effective_type, UnionType)
        is_enum = isinstance(effective_type, EnumType)
        is_literal = isinstance(effective_type, LiteralType)
        is_primitive = is_literal or isinstance(effective_type, (
            Int32Type, BigIntType, FixedIntType, FloatType, Float32Type,
            BoolType, StrType, StrViewType, StringType, CharType,
            PendingStrType,
        ))
        is_record = (
            isinstance(effective_type, NamedType)
            and effective_type.is_user_record
            and self.ctx.registry.get_record(effective_type.name) is not None
        )
        is_optional = isinstance(effective_type, OptionalType)
        if not (is_union or is_enum or is_primitive or is_record or is_optional):
            raise self.ctx.error(
                f"match subject must be a union, enum, primitive, record, "
                f"or Optional type, got '{subject_type}'", stmt
            )

        # Subject variable name for narrowing (only if simple name)
        subject_name: str | None = None
        if isinstance(stmt.subject, TpyName):
            subject_name = stmt.subject.name

        had_wildcard = False
        seen_types: set[str] = set()
        seen_values: set[object] = set()

        scope_before = set(self.ctx.current_scope.bindings.keys())
        assigned_before = frozenset(self.ctx.definitely_assigned)
        bindings_before = dict(self.ctx.current_scope.bindings)
        ns_types_before = self.stmts._save_ns_var_types()
        before = self.stmts.init.save()

        arm_states: list[FlowFacts] = []
        arm_bindings: list[dict[str, TpyType]] = []
        consumed_before = self.ctx.current_consumed_own_params.copy()
        arm_consumed: list[tuple[set[str], bool]] = []  # (consumed_set, terminated)

        for case in stmt.cases:
            if had_wildcard:
                raise self.ctx.error(
                    "unreachable case after wildcard pattern", case.pattern
                )
            # Restore state to pre-match for each arm
            self.stmts.init.restore(before)
            self.ctx.current_consumed_own_params = consumed_before.copy()
            self.ctx.current_scope.bindings = dict(bindings_before)
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
                self.ctx.current_scope.define(name, ty)
                self.stmts.init.mark_assigned(name)
                if name not in self.ctx.var_scope_depth:
                    self.ctx.var_scope_depth[name] = self.ctx.current_scope.depth

            # Narrow subject variable for class patterns (union only)
            narrowing_facts = self._match_case_narrowing_facts(
                case.pattern, subject_name, effective_type,
            ) if is_union else {}
            if narrowing_facts:
                case.type_facts = self.stmts._filter_union_codegen_facts(narrowing_facts)
                self.ctx.narrowed_types.update(narrowing_facts)

            # Narrow Optional subject to inner type in non-None arms
            if is_optional and subject_name is not None:
                pat = case.pattern
                if isinstance(pat, TpyAsPattern):
                    pat = pat.pattern
                is_none_arm = isinstance(pat, TpyLiteralPattern) and pat.value is None
                if not is_none_arm:
                    self.ctx.narrowed_types[subject_name] = effective_type.inner

            # Narrow Literal subject to matched value(s)
            if is_literal and subject_name is not None:
                matched = self._extract_literal_pattern_values(case.pattern, effective_type)
                if matched is not None:
                    narrowed = LiteralType(effective_type.base_type, tuple(matched))
                    self.ctx.narrowed_types[subject_name] = narrowed
                    facts = {subject_name: narrowed}
                    case.type_facts = self.stmts._filter_union_codegen_facts(facts)

            # Analyze guard expression (pattern bindings are in scope)
            if case.guard is not None:
                self.expr.analyze_expr(case.guard)

            for s in case.body:
                self.stmts.analyze_stmt(s)

            arm_states.append(self.stmts.init.save())
            arm_consumed.append((self.ctx.current_consumed_own_params.copy(), self.ctx.init_terminated))
            arm_bindings.append(dict(self.ctx.current_scope.bindings))

            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            if isinstance(pat, (TpyWildcardPattern, TpyCapturePattern)) and case.guard is None:
                had_wildcard = True
            # Class pattern on concrete record with no conditions is always-matching
            elif ((is_record or is_optional) and isinstance(pat, TpyClassPattern)
                  and case.guard is None
                  and not any(isinstance(sub, (TpyLiteralPattern, TpyValuePattern))
                              for _, sub in pat.keywords)):
                had_wildcard = True

        # Exhaustiveness check for finite-valued types
        if not had_wildcard:
            missing = self._match_missing_cases(
                effective_type, seen_types, seen_values,
            )
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
                self.ctx.current_consumed_own_params = merged_consumed
            elif dead_sets:
                self.ctx.current_consumed_own_params = set().union(*dead_sets)
            else:
                self.ctx.current_consumed_own_params = consumed_before

        # Restore scope bindings, merging types from arms
        self.ctx.current_scope.bindings = dict(bindings_before)
        self.stmts._restore_ns_var_types(ns_types_before)
        for arm_b in arm_bindings:
            for name, ty in arm_b.items():
                if name not in bindings_before:
                    self.ctx.current_scope.define(name, ty)

        self.stmts._sync_promoted_var_types(
            set().union(*(set(b) for b in arm_bindings))
        )

        # Pre-declare variables first declared inside match arms
        if not self.ctx.init_terminated:
            branch_new = set(self.ctx.current_scope.bindings.keys()) - scope_before
            newly_assigned = self.ctx.definitely_assigned - assigned_before
            predecl = (branch_new & newly_assigned) - self.ctx.global_declarations
        else:
            predecl = set()
        if predecl:
            self.ctx.if_branch_decls[id(stmt)] = {
                name: self.ctx.current_scope.lookup(name)
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
            # Skip NoneType -- no class-pattern syntax to match it in unions
            return [
                str(m) for m in subject_type.members
                if not isinstance(m, NoneType)
                and str(m) not in seen_types
            ]

        if isinstance(subject_type, EnumType):
            return [
                f"{subject_type.name}.{name}"
                for name in subject_type.members
                if (subject_type.name, name) not in seen_values
            ]

        if isinstance(subject_type, OptionalType):
            if None not in seen_values:
                return ["None"]
            return []

        if isinstance(subject_type, BoolType):
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

        if isinstance(subject_type, NamedType) and subject_type.is_user_record:
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

        # Resolve to a NamedType and check union membership
        record = self.ctx.registry.get_record(cls_name)
        if record is None:
            raise self.ctx.error(f"unknown type '{cls_name}' in match pattern", pattern)

        named_type = NamedType(cls_name)
        if not any(m == named_type for m in subject_type.members):
            raise self.ctx.error(
                f"'{cls_name}' is not a member of union '{subject_type}'", pattern
            )

        if cls_name in seen_types:
            raise self.ctx.error(
                f"duplicate case for '{cls_name}' in match statement", pattern
            )
        seen_types.add(cls_name)
        pattern.resolved_type = named_type

        self._resolve_class_pattern_fields(pattern, record, bindings)

    def _analyze_class_pattern_record(
        self, pattern: TpyClassPattern, subject_type: NamedType,
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

        self._resolve_class_pattern_fields(pattern, record, bindings)

    def _resolve_class_pattern_fields(
        self, pattern: TpyClassPattern, record: RecordInfo,
        bindings: dict[str, TpyType],
    ) -> None:
        """Resolve positional patterns and validate keyword field bindings."""
        cls_name = pattern.cls.name if isinstance(pattern.cls, TpyName) else "?"

        # Use all fields in constructor order for positional resolution and field lookup.
        # For @dataclass, dataclass_fields includes inherited fields; for others, use own fields.
        all_fields = record.dataclass_fields if record.is_dataclass else record.fields

        # Resolve positional patterns to keyword patterns via field declaration order
        if pattern.positional:
            n = len(all_fields)
            if len(pattern.positional) > n:
                noun = "positional pattern" if n == 1 else "positional patterns"
                raise self.ctx.error(
                    f"'{cls_name}' accepts {n} {noun} "
                    f"but {len(pattern.positional)} were given", pattern
                )
            kwd_names = {name for name, _ in pattern.keywords}
            resolved: list[tuple[str, TpyPattern]] = []
            for i, sub_pat in enumerate(pattern.positional):
                field_name = all_fields[i].name
                if field_name in kwd_names:
                    raise self.ctx.error(
                        f"field '{field_name}' is bound both positionally and by keyword "
                        f"in pattern for '{cls_name}'", pattern
                    )
                resolved.append((field_name, sub_pat))
            resolved.extend(pattern.keywords)
            pattern.keywords = resolved
            pattern.positional = []

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
            # Sub-pattern bindings
            if isinstance(sub_pattern, TpyCapturePattern):
                bindings[sub_pattern.name] = field_info.type
            elif isinstance(sub_pattern, TpyWildcardPattern):
                pass
            elif isinstance(sub_pattern, TpyLiteralPattern):
                pass  # Literal comparison -- validated at codegen time
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
        self, pattern: TpyPattern, subject_type: NamedType,
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
            if isinstance(inner, NamedType) and inner.is_user_record:
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
            if not isinstance(check_type, BoolType):
                raise self.ctx.error(
                    f"bool literal pattern not valid for subject type '{subject_type}'",
                    pattern,
                )
        elif isinstance(val, int):
            if not isinstance(check_type, (Int32Type, BigIntType, FixedIntType, EnumType)):
                raise self.ctx.error(
                    f"int literal pattern not valid for subject type '{subject_type}'",
                    pattern,
                )
        elif isinstance(val, float):
            if not isinstance(check_type, (FloatType, Float32Type)):
                raise self.ctx.error(
                    f"float literal pattern not valid for subject type '{subject_type}'",
                    pattern,
                )
        elif isinstance(val, str):
            if not isinstance(check_type, (StrType, StrViewType, StringType, PendingStrType)):
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
        if isinstance(subject_type, EnumType):
            if not isinstance(val_type, EnumType) or val_type.name != subject_type.name:
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
