"""
TurboPython Match/Case Code Generation

Generates C++ code from TurboPython match/case statements.
"""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NominalType, NoneType, OptionalType, OwnType, PtrType, ReadonlyType,
    PendingStrType, UnionType, RecursiveAliasInstanceType,
    LiteralType,
    unwrap_readonly, is_any_str_type, is_protocol_type, polymorphic_source_inner,
    polymorphic_source_is_pointer,
)
from .variant_access import VariantAccess
from ..parse import (
    TpyStmt, TpyExpr, TpyFieldAccess, TpyName, TpyMatch, TpyMatchCase, TpyPattern,
    TpySubscript, TpyWildcardPattern, TpyCapturePattern, TpyClassPattern,
    TpyLiteralPattern, TpyValuePattern, TpyOrPattern, TpyAsPattern, TpyMethodCall,
)


def _returns_bare_reference(rt: 'TpyType | None') -> bool:
    """True when a return type lowers to a C++ lvalue reference (`T&` /
    `const T&`) -- a bare non-value reference type. Own (by-value move),
    Optional / Ptr (pointer repr), protocols (auto / base), and value types
    are prvalues / non-references and excluded."""
    if rt is None:
        return False
    rt = unwrap_readonly(rt)
    if rt.is_value_type() or isinstance(rt, (OwnType, OptionalType, PtrType)):
        return False
    if isinstance(rt, UnionType) and not rt.needs_wrapper():
        # A pointer-variant or value union is returned by value (a prvalue
        # `std::variant<...>`), not `T&`. Only a wrapper union returns by
        # reference (mirrors UnionType.to_cpp_return's needs_wrapper gate).
        return False
    return not is_protocol_type(rt)


def _match_subject_is_lvalue(expr: TpyExpr) -> bool:
    """A match subject is an lvalue when binding it with `auto&` is
    safe (won't dangle) and useful (lets `case C() as v: v.f = ...`
    write through to the original storage).

    Plain names, field accesses whose target is itself an lvalue,
    and subscripts on lvalue targets all qualify. A method call that
    returns a bare reference (an accessor like `h.get() -> Tree[T]`
    lowering to `Tree<T>&`) on an lvalue receiver also qualifies -- the
    reference aliases the receiver's storage, which outlives the match.
    Other calls (by-value / Own returns, temporary receivers), literals,
    and constructed temporaries do not."""
    if isinstance(expr, TpyName):
        return True
    if isinstance(expr, TpyFieldAccess):
        return _match_subject_is_lvalue(expr.obj)
    if isinstance(expr, TpySubscript):
        return _match_subject_is_lvalue(expr.obj)
    if isinstance(expr, TpyMethodCall):
        fi = expr.resolved_function_info
        return (fi is not None and _returns_bare_reference(fi.return_type)
                and _match_subject_is_lvalue(expr.obj))
    return False
from .context import INDENT, CodeGenError, escape_cpp_name, escape_cpp_string, escape_cpp_char, cpp_string_literal_expr
from .string_dispatch import find_best_discriminator, STRING_SWITCH_THRESHOLD
from ..type_def_registry import is_fixed_int_type, is_bool_type, is_enum_type
from ..liveness import stmts_terminate

if TYPE_CHECKING:
    from ..parse import SourceLocation
    from .context import CodeGenContext
    from .types import TypeResolver
    from .expressions import ExpressionGenerator
    from .statements import StatementGenerator


@dataclass
class _PolyDispatch:
    """Cast context shared across the arms of a @dynamic / polymorphic match.

    `cast_arg` is the C++ pointer expression fed to `dynamic_cast` /
    `dyn_adapter_cast` (the once-bound `__match_subject`'s payload address);
    `source_inner` is the @dynamic protocol or polymorphic-class root the casts
    narrow from; `subject_read` reads the subject at its base type for capture /
    as bindings. `subject_name` is the subject's variable name when it is a bare
    name (used to narrow the body's reads to the arm subclass) and None for an
    expression subject (the arm reaches the value via `as` / capture only)."""
    subject_name: str | None
    source_inner: TpyType
    cast_arg: str
    is_const: bool
    subject_read: str


class MatchGenerator:
    """Generates C++ code from TurboPython match/case statements."""

    def __init__(self, ctx: CodeGenContext, types: TypeResolver,
                 expressions: ExpressionGenerator, stmts: StatementGenerator):
        self.ctx = ctx
        self.types = types
        self.expressions = expressions
        self.stmts = stmts

    def gen_match(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate a match/case statement. Uses switch when possible, if/elif otherwise."""
        assert stmt.subject_type is not None
        subject_type = unwrap_readonly(stmt.subject_type)

        # @overload dead branch elimination for match on union subject
        if (isinstance(subject_type, UnionType)
                and isinstance(stmt.subject, TpyName)
                and self.ctx.overload_param_types):
            concrete = self.ctx.overload_param_types.get(stmt.subject.name)
            if concrete is not None:
                self._gen_match_overload_specialized(out, stmt, concrete, indent)
                return

        # Pre-declare variables first declared inside match arms
        self.stmts._emit_branch_decls(out, stmt, indent)

        # @dynamic / polymorphic dispatch evaluates the subject once into
        # `__match_subject` (see `_gen_match_polymorphic`) and runs the
        # `dynamic_cast` chain off it -- so it owns its own binding emit and
        # returns here.
        if stmt.polymorphic_dispatch:
            self._gen_match_polymorphic(out, stmt, indent)
            self._emit_match_unreachable_tail(out, stmt, indent)
            return

        # Evaluate subject and bind to a local
        subject_code = self.expressions.gen_expr(stmt.subject)
        # A wrapper subject in pointer-form (a hoisted/reassigned local, `Tree<T>*`)
        # must deref so the `.value` access lands on the struct, not the pointer.
        # Gated to the wrapper shape: Optional/Ptr/ptr-variant subjects keep their
        # pointer (the match reads it directly for the None-check / variant dispatch).
        if (isinstance(stmt.subject, TpyName)
                and isinstance(subject_type, (UnionType, RecursiveAliasInstanceType))
                and subject_type.needs_wrapper()
                and self.ctx.is_indirect_name(stmt.subject)):
            subject_code = f"(*{subject_code})"
        self.ctx.temps.flush(out, indent)
        # Use auto& for lvalue subjects (safe reference; lets a
        # mutating `case C() as v: v.field = ...` arm write through
        # to the original storage). Plain names are lvalues; so is a
        # field access whose target is itself an lvalue (e.g.
        # `self.payload`). Everything else (calls, temporaries) is
        # copied as `auto` to avoid dangling references.
        subject_is_lvalue = _match_subject_is_lvalue(stmt.subject)
        binding = "auto&" if subject_is_lvalue else "auto"
        # Record whether the subject is a stable frame-resident lvalue, for
        # the resumable pointer-form binding emit: a pointer-into-subject arm
        # binding survives a suspension only if the subject's storage outlives
        # it. A non-lvalue subject is a dispatch-local copy; a *narrowed* name
        # (union/isinstance) renders to a dispatch-local extraction alias
        # (`__case_N`) even though it is syntactically a name -- so a nested
        # `match` on a narrowed subject is NOT frame-stable. Both are unsafe
        # for a pointer-form binding (the emit rejects that combination).
        # Set unconditionally; read only on the resumable pointer-form path.
        # Re-set per match (incl. nested) -- arm bindings emit before any
        # nested-match arm body runs, so no save/restore is needed.
        subject_is_narrowed = (isinstance(stmt.subject, TpyName)
                               and stmt.subject.name in self.ctx.narrowed_vars)
        self.ctx.resumable_match_subject_is_lvalue = (
            subject_is_lvalue and not subject_is_narrowed)
        self.ctx.resumable_match_loc = stmt.loc
        out.write(f"{indent}{binding} __match_subject = {subject_code};\n")

        if isinstance(subject_type, (UnionType, RecursiveAliasInstanceType)):
            # Generic recursive alias instances dispatch through the same
            # variant-index path; _variant_index / VariantAccess read members
            # via wrapper_info() / needs_wrapper(), both of which the instance
            # implements.
            has_guard = any(c.guard is not None for c in stmt.cases)
            # Also use guarded path when union field guards cause multiple
            # arms to share the same variant index
            if not has_guard:
                has_guard = self._has_shared_variant_index(stmt, subject_type)
            # A field-value sub-pattern (e.g. `Dog(legs=4)`) is conditional --
            # the unconditional switch path emits no `&&` field check and would
            # silently drop it, so route to the guarded path which does.
            if not has_guard:
                has_guard = any(self._pattern_has_field_condition(c.pattern)
                                for c in stmt.cases)
            if has_guard:
                self._gen_match_guarded_union(out, stmt, subject_type, indent)
            else:
                self._gen_match_switch_union(out, stmt, subject_type, indent)
        elif is_enum_type(subject_type):
            self._gen_match_switch_enum(out, stmt, indent)
        elif isinstance(subject_type, LiteralType):
            base = subject_type.base_type
            if is_any_str_type(base):
                if self._should_switch_str(stmt):
                    self._gen_match_switch_str(out, stmt, indent)
                else:
                    self._gen_match_if_elif(out, stmt, indent)
            elif is_fixed_int_type(base):
                self._gen_match_switch_primitive(out, stmt, indent)
            else:
                # bool (and anything else literal-like) falls through to if/elif:
                # `switch(bool_var)` is valid C++ but trips -Wswitch-bool, and an
                # if-chain is the same shape with two arms anyway.
                self._gen_match_if_elif(out, stmt, indent)
        elif is_fixed_int_type(subject_type):
            self._gen_match_switch_primitive(out, stmt, indent)
        elif is_bool_type(subject_type):
            self._gen_match_if_elif(out, stmt, indent)
        elif isinstance(subject_type, NominalType) and subject_type.is_user_record:
            has_guard = any(c.guard is not None for c in stmt.cases)
            if has_guard:
                self._gen_match_guarded_record(out, stmt, indent)
            else:
                self._gen_match_if_elif_record(out, stmt, indent)
        elif isinstance(subject_type, OptionalType):
            partition = self._partition_optional_cases(stmt.cases)
            if partition is not None:
                none_cases, inner_cases = partition
                self._gen_match_optimized_optional(
                    out, stmt, subject_type, none_cases, inner_cases, indent,
                )
            else:
                self._gen_match_if_elif_optional(out, stmt, subject_type, indent)
        elif is_any_str_type(subject_type):
            if self._should_switch_str(stmt):
                self._gen_match_switch_str(out, stmt, indent)
            else:
                self._gen_match_if_elif(out, stmt, indent)
        else:
            self._gen_match_if_elif(out, stmt, indent)

        self._emit_match_unreachable_tail(out, stmt, indent)

    def _emit_match_unreachable_tail(
        self, out: TextIO, stmt: TpyMatch, indent: str,
    ) -> None:
        """If the match is exhaustive (sema-proven) AND every arm body
        terminates, the post-match control point is unreachable. Tell the
        compiler so -- otherwise it warns "control reaches end of non-void
        function" when the match is the function's last statement.

        For non-exhaustive matches, falling through the end-label is the
        user's intent (sema only warns), so emitting std::unreachable() there
        would let the optimizer eliminate code that the user expects to
        execute."""
        if (stmt.is_exhaustive
                and stmt.cases
                and all(stmts_terminate(c.body) for c in stmt.cases)):
            out.write(f"{indent}::std::unreachable();\n")

    # ------------------------------------------------------------------
    # @dynamic / polymorphic dispatch
    # ------------------------------------------------------------------

    def _gen_match_polymorphic(
        self, out: TextIO, stmt: TpyMatch, indent: str,
    ) -> None:
        """Dispatch a match on a @dynamic / polymorphic subject via a
        `dynamic_cast` chain, the match sibling of isinstance subclass
        narrowing. Reuses the same cast-argument / cast-rhs helpers so the two
        constructs stay in lockstep."""
        # A suspension inside an arm body is not threaded through the resumable
        # frame's arm routing (the dispatch is a plain dynamic_cast chain, not
        # the decomposed state machine) -- refuse rather than drop it.
        if self.ctx.resumable_arm_emitter is not None:
            raise CodeGenError(
                "a `yield` / `await` inside a `match` on a @dynamic / "
                "polymorphic value is not yet supported",
                loc=stmt.loc,
            )

        subject = stmt.subject
        subject_name = subject.name if isinstance(subject, TpyName) else None
        registry = self.ctx.analyzer.registry
        # Dispatch type: declared var type for a bare name; for an expression,
        # the fully-unwrapped effective type sema already stored on the node
        # (Own / Ref / Readonly stripped). Reusing it rather than re-deriving
        # keeps the source / deref-depth predicates seeing exactly what sema
        # accepted as polymorphic. Both feed the same predicates below.
        if subject_name is not None:
            var_decl = self.ctx.lookup_var_type(subject_name)
        else:
            var_decl = stmt.subject_type

        # Evaluate the subject once into `__match_subject` -- `auto&` for an
        # lvalue (so `case C() as v: v.f = ...` writes through to the original
        # storage), `auto` for an rvalue (so a temporary such as `make_box()`
        # outlives the deref-view cast that points into it). The dynamic_cast
        # chain then takes its pointer from this single binding.
        subject_code = self.expressions.gen_expr(subject)
        self.ctx.temps.flush(out, indent)
        binding = "auto&" if _match_subject_is_lvalue(subject) else "auto"
        out.write(f"{indent}{binding} __match_subject = {subject_code};\n")

        source_inner = polymorphic_source_inner(var_decl, registry)
        if source_inner is not None:
            depth = 0
        else:
            deref = self.ctx.deref_dispatch_source(var_decl)
            assert deref is not None  # sema flagged this as polymorphic
            source_inner, depth = deref
        cast_arg = self._poly_match_cast_arg(var_decl, depth)
        is_const = self._poly_subject_is_const(subject, subject_name, var_decl)

        ctx = _PolyDispatch(subject_name, source_inner, cast_arg, is_const,
                            "__match_subject")
        # The if/elif chain relies on the `if (Sub* p = cast)` init form, which
        # can't also AND a field-value check; a field condition (like a guard)
        # therefore needs the standalone-if + goto guarded path.
        needs_guarded = any(
            c.guard is not None or self._pattern_has_field_condition(c.pattern)
            for c in stmt.cases
        )
        if needs_guarded:
            self._gen_match_polymorphic_guarded(out, stmt, ctx, indent)
        else:
            self._gen_match_polymorphic_if_elif(out, stmt, ctx, indent)

    def _poly_match_cast_arg(self, var_decl: 'TpyType | None', depth: int) -> str:
        """The dynamic_cast pointer input, taken from the once-bound
        `__match_subject`: a deref view (`&(__match_subject.__deref__()...)`)
        for owning wrappers, the pointer itself for pointer-repr sources
        (`Ptr[inner]` / pointer-repr `Optional`), else its address."""
        if depth > 0:
            return f"&(__match_subject{'.__deref__()' * depth})"
        if polymorphic_source_is_pointer(var_decl):
            return "__match_subject"
        return "&__match_subject"

    def _poly_subject_is_const(
        self, subject: TpyExpr, subject_name: str | None,
        var_decl: 'TpyType | None',
    ) -> bool:
        """Whether the dispatch pointee is const, so the cast targets
        `const Sub*` (else `dynamic_cast<Sub*>(const T*)` fails to compile).

        True when the subject is readonly-typed, or when `__match_subject`
        binds a const borrow: a const-ref param, or -- since C++ propagates
        const through member access -- a field / element of a const receiver.
        The param const-borrow facts (`const_ref_params` etc.) need not surface
        as `ReadonlyType` in the sema type, so they are consulted directly."""
        if isinstance(self.ctx.analyzer.get_expr_type(subject), ReadonlyType):
            return True
        return self._expr_is_const_borrow(subject, var_decl)

    def _expr_is_const_borrow(
        self, expr: TpyExpr, var_decl: 'TpyType | None',
    ) -> bool:
        """Does `expr` bind as a const borrow? A bare name defers to the
        param/local const-borrow facts; a field / subscript is const when its
        receiver is (C++ member access preserves const)."""
        if isinstance(expr, TpyName):
            return self.stmts._is_const_borrow_source(expr.name, var_decl)
        if isinstance(expr, (TpyFieldAccess, TpySubscript)):
            recv = expr.obj
            return self._expr_is_const_borrow(
                recv, self.ctx.analyzer.get_expr_type(recv))
        return False

    def _poly_narrow_save(
        self, ctx: '_PolyDispatch', ref_local: str,
    ) -> dict[str, str | None]:
        """Narrow the subject name to the arm's subclass reference so reads of
        the subject in the body resolve to it. Only a bare-name subject has a
        name to narrow; an expression subject reaches the value via `as` /
        capture, so this is a no-op there."""
        if ctx.subject_name is None:
            return {}
        saved = {ctx.subject_name: self.ctx.narrowed_vars.get(ctx.subject_name)}
        self.ctx.narrowed_vars[ctx.subject_name] = ref_local
        return saved

    def _poly_arm_cast(
        self, pattern: TpyClassPattern, i: int, ctx: '_PolyDispatch',
    ) -> tuple[str, str, str]:
        """Return (cond_decl, ref_decl, ref_local) for a class-pattern arm.

        `cond_decl` is the `Sub* __ptr = <cast>` usable directly as an
        if-condition; `ref_decl` binds a `Sub&` to the cast result for field
        and subject-narrowing reads inside the arm."""
        narrowed = pattern.resolved_type
        cpp_type = self.types.type_to_cpp(narrowed)
        const_pfx = "const " if ctx.is_const else ""
        ptr_local = f"__mpoly_{i}"
        ref_local = f"__case_{i}"
        cast_rhs = self.stmts.protocols.dynamic_narrow_cast_rhs(
            cpp_type, narrowed, ctx.source_inner, ctx.cast_arg,
            is_const=ctx.is_const)
        cond_decl = f"{const_pfx}{cpp_type}* {ptr_local} = {cast_rhs}"
        ref_decl = f"{const_pfx}{cpp_type}& {ref_local} = *{ptr_local};"
        return cond_decl, ref_decl, ref_local

    def _poly_or_condition(
        self, pattern: TpyOrPattern, ctx: '_PolyDispatch',
    ) -> str:
        """Build an OR of `dynamic_cast != nullptr` tests for an or-pattern
        (type-test only; or-pattern alternatives bind no variables)."""
        parts: list[str] = []
        for alt in pattern.patterns:
            assert isinstance(alt, TpyClassPattern)
            cpp_type = self.types.type_to_cpp(alt.resolved_type)
            rhs = self.stmts.protocols.dynamic_narrow_cast_rhs(
                cpp_type, alt.resolved_type, ctx.source_inner, ctx.cast_arg,
                is_const=ctx.is_const)
            parts.append(f"({rhs} != nullptr)")
        return " || ".join(parts)

    def _gen_match_polymorphic_if_elif(
        self, out: TextIO, stmt: TpyMatch, ctx: '_PolyDispatch', indent: str,
    ) -> None:
        inner = INDENT * (self.ctx.indent_level + 1)
        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern):
                # A field-value sub-pattern needs the guarded path; the
                # `if (Sub* p = cast)` init form here can't AND a field check.
                if self._pattern_has_field_condition(pattern):
                    raise CodeGenError(
                        "internal: field-value sub-pattern reached the "
                        "polymorphic if/elif path", loc=case.loc)
                cond_decl, ref_decl, ref_local = self._poly_arm_cast(pattern, i, ctx)
                out.write(f"{indent}{keyword} ({cond_decl}) {{\n")
                out.write(f"{inner}{ref_decl}\n")
                self._gen_match_field_bindings(out, pattern, ref_local, inner)
                self._emit_binding(out, as_name, as_raw, ref_local, inner)
                saved = self._poly_narrow_save(ctx, ref_local)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1
                self.ctx.restore_narrowed_vars(saved)

            elif isinstance(pattern, TpyOrPattern):
                out.write(f"{indent}{keyword} ({self._poly_or_condition(pattern, ctx)}) {{\n")
                self._emit_binding(out, as_name, as_raw, ctx.subject_read, inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    self._emit_binding(out, escape_cpp_name(pattern.name),
                                       pattern.name, ctx.subject_read, inner)
                self._emit_binding(out, as_name, as_raw, ctx.subject_read, inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            else:
                raise CodeGenError(
                    f"Unsupported match pattern for polymorphic subject: "
                    f"{type(pattern).__name__}")

        out.write(f"{indent}}}\n")

    def _gen_match_polymorphic_guarded(
        self, out: TextIO, stmt: TpyMatch, ctx: '_PolyDispatch', indent: str,
    ) -> None:
        inner = INDENT * (self.ctx.indent_level + 1)
        self.ctx.match_counter += 1
        end_label = f"__match_end_{self.ctx.match_counter}"

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyClassPattern):
                cond_decl, ref_decl, ref_local = self._poly_arm_cast(pattern, i, ctx)
                out.write(f"{indent}if ({cond_decl}) {{\n")
                out.write(f"{inner}{ref_decl}\n")
                self._gen_match_field_bindings(out, pattern, ref_local, inner)
                self._emit_binding(out, as_name, as_raw, ref_local, inner)
                saved = self._poly_narrow_save(ctx, ref_local)
                field_conds = self._record_field_conditions(pattern, ref_local)
                self._emit_poly_guarded_action(
                    out, case, guard, field_conds, end_label, indent, inner)
                self.ctx.restore_narrowed_vars(saved)
                out.write(f"{indent}}}\n")

            elif isinstance(pattern, TpyOrPattern):
                cond = self._poly_or_condition(pattern, ctx)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"({cond}) && {guard_code}"
                out.write(f"{indent}if ({cond}) {{\n")
                self._emit_binding(out, as_name, as_raw, ctx.subject_read, inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1
                out.write(f"{inner}goto {end_label};\n")
                out.write(f"{indent}}}\n")

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if isinstance(pattern, TpyCapturePattern):
                    self._emit_binding(out, escape_cpp_name(pattern.name),
                                       pattern.name, ctx.subject_read, indent)
                self._emit_binding(out, as_name, as_raw, ctx.subject_read, indent)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if ({guard_code}) {{\n")
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}goto {end_label};\n")
                    out.write(f"{indent}}}\n")
                else:
                    out.write(f"{indent}{{\n")
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")

            else:
                raise CodeGenError(
                    f"Unsupported match pattern for polymorphic subject: "
                    f"{type(pattern).__name__}")

        out.write(f"{indent}{end_label}:;\n")

    def _emit_poly_guarded_action(
        self, out: TextIO, case: TpyMatchCase, guard: 'TpyExpr | None',
        field_conds: list[str], end_label: str, indent: str, inner: str,
    ) -> None:
        """Emit a class-pattern arm body inside its `if (cast)` block, gating
        the `goto end_label` on the field-value conditions and guard (if any).
        A failed condition falls through to the next standalone-if arm."""
        cond_parts = list(field_conds)
        if guard is not None:
            guard_code = self.expressions.gen_expr(guard)
            self.ctx.temps.flush(out, inner)
            cond_parts.append(guard_code)
        if cond_parts:
            out.write(f"{inner}if ({' && '.join(cond_parts)}) {{\n")
            self.ctx.indent_level += 2
            self._emit_case_body(out, case.body, case.type_facts)
            self.ctx.indent_level -= 2
            out.write(f"{inner}    goto {end_label};\n")
            out.write(f"{inner}}}\n")
        else:
            self.ctx.indent_level += 1
            self._emit_case_body(out, case.body, case.type_facts)
            self.ctx.indent_level -= 1
            out.write(f"{inner}goto {end_label};\n")

    def _gen_match_overload_specialized(
        self, out: TextIO, stmt: TpyMatch, concrete_type: 'TpyType', indent: str,
    ) -> None:
        """Emit only the matching arm for a match on a concrete overload param.

        The subject variable has a concrete (non-variant) type in this overload,
        so we find the arm whose class pattern matches and emit its body directly.
        """
        assert isinstance(stmt.subject, TpyName)
        subject_name = stmt.subject.name

        # This @overload-specialization path emits the matched arm body
        # directly via `gen_stmt`, bypassing `_emit_case_body` -- so the
        # resumable-frame arm-routing hook would never fire and a suspension
        # in the arm would be emitted as straight-line code (dropped). Refuse
        # rather than silently miscompile; the resumable `match` decomposition
        # does not cover the overload-specialized subject.
        if self.ctx.resumable_arm_emitter is not None:
            raise CodeGenError(
                "a `yield`/`await` inside a `match` on an @overload-"
                "specialized parameter is not yet supported",
                loc=stmt.loc,
            )

        def emit_field_bindings(pattern: TpyClassPattern) -> None:
            self._gen_match_field_bindings(out, pattern, subject_name, indent)

        for case in stmt.cases:
            pattern, as_name, as_raw_name = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern) and pattern.resolved_type is not None:
                if pattern.resolved_type == concrete_type:
                    emit_field_bindings(pattern)
                    if as_name:
                        out.write(f"{indent}auto& {escape_cpp_name(as_name)} = {subject_name};\n")
                    for s in case.body:
                        self.stmts.gen_stmt(out, s)
                    return

            elif isinstance(pattern, TpyOrPattern):
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern) and alt.resolved_type == concrete_type:
                        emit_field_bindings(alt)
                        if as_name:
                            out.write(f"{indent}auto& {escape_cpp_name(as_name)} = {subject_name};\n")
                        for s in case.body:
                            self.stmts.gen_stmt(out, s)
                        return

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if isinstance(pattern, TpyCapturePattern):
                    out.write(f"{indent}auto& {escape_cpp_name(pattern.name)} = {subject_name};\n")
                if as_name:
                    out.write(f"{indent}auto& {escape_cpp_name(as_name)} = {subject_name};\n")
                for s in case.body:
                    self.stmts.gen_stmt(out, s)
                return

        raise AssertionError(
            f"No match arm found for concrete type {concrete_type} "
            f"in @overload specialization"
        )

    def _subject_is_ptr_variant(
        self, subject: TpyExpr, subject_type: TpyType,
    ) -> bool:
        """Whether the match subject's storage is pointer-variant.

        ``is_ptr_variant_union(subject_type)`` reflects the type's repr in
        its primary contexts (params, locals, returns) but the same union
        is stored value-variant when it's a record field or container
        element. The match ``get_expr`` ('*std::get' vs 'std::get') has to
        match actual storage, not just type. Falls back to the assignment
        path's classifier so the same rules apply on both sides.
        """
        if not self.ctx.is_ptr_variant_union(subject_type):
            return False
        return self.ctx.is_ptr_variant_source(subject)

    def _gen_match_switch_union(
        self, out: TextIO, stmt: TpyMatch, subject_type: UnionType, indent: str,
    ) -> None:
        """Generate switch (__match_subject.index()) for union subjects."""
        inner = INDENT * (self.ctx.indent_level + 1)
        is_ptr_var = self._subject_is_ptr_variant(stmt.subject, subject_type)
        va = VariantAccess("__match_subject", subject_type, is_ptr_variant=is_ptr_var)
        out.write(f"{indent}switch ({va.index_expr()}) {{\n")

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw_name = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern):
                assert pattern.resolved_type is not None
                # This unconditional-switch path emits no field-value check;
                # an arm carrying one must have been routed to the guarded
                # path. Fail loudly rather than silently drop the condition.
                if self._pattern_has_field_condition(pattern):
                    raise CodeGenError(
                        "internal: field-value sub-pattern reached the "
                        "unconditional union switch path", loc=case.loc)
                idx = self._variant_index(subject_type, pattern.resolved_type)
                out.write(f"{indent}case {idx}: {{\n")
                case_var: str | None = None
                if pattern.keywords or case.type_facts:
                    case_var = f"__case_{i}"
                    out.write(f"{inner}auto& {case_var} = {va.get_by_index(idx)};\n")
                if pattern.keywords:
                    self._gen_match_field_bindings(out, pattern, case_var, inner)
                self._emit_binding(out, as_name, as_raw_name,
                                   case_var or va.get_by_index(idx), inner)
                # Narrowing
                saved_narrow: dict[str, str | None] = {}
                if case.type_facts:
                    for var_name in case.type_facts:
                        saved_narrow[var_name] = self.ctx.narrowed_vars.get(var_name)
                        self.ctx.narrowed_vars[var_name] = case_var
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1
                self.stmts.ctx.restore_narrowed_vars(saved_narrow)
                out.write(f"{inner}break;\n")
                out.write(f"{indent}}}\n")

            elif isinstance(pattern, TpyOrPattern):
                has_bindings = any(
                    isinstance(alt, TpyClassPattern) and alt.keywords
                    for alt in pattern.patterns
                )
                has_wildcard = any(
                    isinstance(alt, (TpyWildcardPattern, TpyCapturePattern))
                    for alt in pattern.patterns
                )
                if has_wildcard:
                    # Wildcard subsumes all alternatives -> default
                    out.write(f"{indent}default: {{\n")
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}break;\n")
                    out.write(f"{indent}}}\n")
                elif not has_bindings:
                    # No bindings: case fallthrough
                    for alt in pattern.patterns:
                        assert isinstance(alt, TpyClassPattern) and alt.resolved_type is not None
                        idx = self._variant_index(subject_type, alt.resolved_type)
                        out.write(f"{indent}case {idx}:\n")
                    out.write(f"{indent}{{\n")
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}break;\n")
                    out.write(f"{indent}}}\n")
                else:
                    # With bindings: body duplication per alternative
                    for j, alt in enumerate(pattern.patterns):
                        assert isinstance(alt, TpyClassPattern) and alt.resolved_type is not None
                        idx = self._variant_index(subject_type, alt.resolved_type)
                        out.write(f"{indent}case {idx}: {{\n")
                        case_var = f"__case_{i}_{j}"
                        out.write(f"{inner}auto& {case_var} = {va.get_by_index(idx)};\n")
                        if alt.keywords:
                            self._gen_match_field_bindings(out, alt, case_var, inner)
                        saved = self._apply_narrowing(case.type_facts, case_var)
                        self.ctx.indent_level += 1
                        self._emit_case_body(out, case.body, case.type_facts)
                        self.ctx.indent_level -= 1
                        self.stmts.ctx.restore_narrowed_vars(saved)
                        out.write(f"{inner}break;\n")
                        out.write(f"{indent}}}\n")

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                self._gen_switch_default_arm(
                    out, pattern, as_name, as_raw_name, case.body,
                    indent, inner, case.type_facts)

            elif isinstance(pattern, TpyLiteralPattern) and pattern.value is None:
                idx = self._variant_index(subject_type, NoneType())
                out.write(f"{indent}case {idx}: {{\n")
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1
                out.write(f"{inner}break;\n")
                out.write(f"{indent}}}\n")

            else:
                raise CodeGenError(f"Unsupported pattern in union switch: {type(pattern).__name__}")

        out.write(f"{indent}}}\n")

    def _gen_match_switch_enum(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate switch (__match_subject) for enum subjects."""
        groups = self._group_switch_arms(stmt, kind="enum")
        self._emit_switch_groups(out, groups, indent, is_exhaustive=stmt.is_exhaustive)

    def _gen_match_switch_primitive(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate switch (__match_subject) for int/bool subjects."""
        groups = self._group_switch_arms(stmt, kind="primitive")
        self._emit_switch_groups(out, groups, indent, is_exhaustive=stmt.is_exhaustive)

    # Entry in a switch arm group:
    # (guard, body, capture_escaped, as_escaped, raw_names, loc, type_facts)
    # raw_names: set of raw Python names for declared_vars lookup
    _SwitchEntry = tuple[
        TpyExpr | None, list['TpyStmt'], str | None, str | None, set[str],
        'SourceLocation | None', dict[str, TpyType],
    ]

    def _group_switch_arms(
        self, stmt_or_cases: 'TpyMatch | list[TpyMatchCase]', kind: str,
    ) -> list[tuple[list[str], list[_SwitchEntry]]]:
        """Group match cases by switch label for enum/primitive subjects.

        Returns a list of (labels, entries) where:
        - labels: list of case label strings, or ["default"] for wildcard
        - entries: list of (guard, body, cap_escaped, as_escaped, raw_names, loc)
        Same-value cases with guards are merged into a single group.
        Entries are ordered guarded-first, unguarded-last (enforced by sema
        duplicate-case check which only allows same-value repeats with guards).
        """
        cases: list[TpyMatchCase] = (
            stmt_or_cases if isinstance(stmt_or_cases, list) else stmt_or_cases.cases
        )
        groups: dict[str, tuple[list[str], list[MatchGenerator._SwitchEntry]]] = {}
        default_entries: list[MatchGenerator._SwitchEntry] = []

        for case in cases:
            pattern, as_escaped, as_raw = self._unwrap_as_pattern(case.pattern)
            raw_names: set[str] = set()
            if as_raw is not None:
                raw_names.add(as_raw)

            if isinstance(pattern, (TpyValuePattern, TpyLiteralPattern)):
                if kind == "enum":
                    assert isinstance(pattern, TpyValuePattern)
                    label = self.expressions.gen_expr(pattern.expr)
                else:
                    assert isinstance(pattern, TpyLiteralPattern)
                    label = self._switch_literal_label(pattern)
                entry: MatchGenerator._SwitchEntry = (
                    case.guard, case.body, None, as_escaped, raw_names, case.loc, case.type_facts,
                )
                if label in groups:
                    groups[label][1].append(entry)
                else:
                    groups[label] = ([label], [entry])

            elif isinstance(pattern, TpyOrPattern):
                has_wild = any(
                    isinstance(alt, (TpyWildcardPattern, TpyCapturePattern))
                    for alt in pattern.patterns
                )
                if has_wild:
                    default_entries.append((case.guard, case.body, None, as_escaped, raw_names, case.loc, case.type_facts))
                else:
                    labels = []
                    for alt in pattern.patterns:
                        if kind == "enum":
                            assert isinstance(alt, TpyValuePattern)
                            labels.append(self.expressions.gen_expr(alt.expr))
                        else:
                            assert isinstance(alt, TpyLiteralPattern)
                            labels.append(self._switch_literal_label(alt))
                    key = "|".join(labels)
                    entry = (case.guard, case.body, None, as_escaped, raw_names, case.loc, case.type_facts)
                    if key in groups:
                        groups[key][1].append(entry)
                    else:
                        groups[key] = (labels, [entry])

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                cap_escaped = escape_cpp_name(pattern.name) if isinstance(pattern, TpyCapturePattern) else None
                if isinstance(pattern, TpyCapturePattern):
                    raw_names.add(pattern.name)
                default_entries.append((case.guard, case.body, cap_escaped, as_escaped, raw_names, case.loc, case.type_facts))

            else:
                raise CodeGenError(f"Unsupported pattern in {kind} switch: {type(pattern).__name__}")

        result = list(groups.values())
        if default_entries:
            result.append((["default"], default_entries))
        return result

    def _emit_switch_groups(
        self, out: TextIO,
        groups: list[tuple[list[str], list[_SwitchEntry]]],
        indent: str,
        subject_expr: str = "__match_subject",
        is_exhaustive: bool = False,
    ) -> None:
        """Emit a switch statement from grouped arms.

        is_exhaustive: caller's promise that the match covers every possible
        subject value. When true, the synthetic ``default: break;`` is omitted
        so future enum members added upstream still trigger ``-Wswitch``.
        """
        inner = INDENT * (self.ctx.indent_level + 1)

        # Check if any non-default group needs guard fallback to default
        has_default = any(labels == ["default"] for labels, _ in groups)
        needs_default_goto = has_default and any(
            labels != ["default"]
            and all(g is not None for g, _, _, _, _, _, _ in entries)
            for labels, entries in groups
        )
        default_label: str | None = None
        if needs_default_goto:
            self.ctx.match_counter += 1
            default_label = f"__match_default_{self.ctx.match_counter}"

        out.write(f"{indent}switch ({subject_expr}) {{\n")

        for labels, entries in groups:
            # Emit source comment for first entry in group
            if entries:
                self.ctx.emit_source_comment(out, entries[0][5], indent)
            # Emit case labels
            if labels == ["default"]:
                if default_label is not None:
                    out.write(f"{indent}default: {default_label}: {{\n")
                else:
                    out.write(f"{indent}default: {{\n")
            elif len(labels) == 1:
                out.write(f"{indent}case {labels[0]}: {{\n")
            else:
                for label in labels:
                    out.write(f"{indent}case {label}:\n")
                out.write(f"{indent}{{\n")

            # Bound literal_facts/protocol_narrowings to the case scope.
            # type_facts from the first entry apply to the whole group (all
            # entries in a group match the same value, so facts are equivalent).
            type_facts_0 = entries[0][6]

            # Single entry, no guard -> simple body
            if len(entries) == 1 and entries[0][0] is None:
                _, body, cap, as_name, raw_names, _loc, _tf = entries[0]
                self._emit_switch_binding(out, cap, as_name, raw_names, inner, subject_expr)
                self.ctx.indent_level += 1
                self._emit_case_body(out, body, type_facts_0)
                self.ctx.indent_level -= 1
            else:
                # Emit capture/as binding before the guard chain so guards
                # can reference the bound variable
                bindings_emitted: set[str] = set()
                for _g, _b, cap, as_name, raw_names, _loc, _tf in entries:
                    for escaped, raw in self._binding_pairs(cap, as_name, raw_names):
                        if escaped not in bindings_emitted:
                            self._emit_binding(out, escaped, raw, subject_expr, inner)
                            bindings_emitted.add(escaped)

                # Guard chain: if (g1) { body1 } else if (g2) { body2 } else { fallback }
                has_unguarded = any(g is None for g, _, _, _, _, _, _ in entries)
                if_opened = False
                for _j, (guard, body, _cap, _as, _raw, _loc, _tf) in enumerate(entries):
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, inner)
                        keyword = "if" if not if_opened else "} else if"
                        if_opened = True
                        out.write(f"{inner}{keyword} ({guard_code}) {{\n")
                        self.ctx.indent_level += 2
                        self._emit_case_body(out, body, type_facts_0)
                        self.ctx.indent_level -= 2
                    else:
                        # Unguarded entry: final else
                        out.write(f"{inner}}} else {{\n")
                        self.ctx.indent_level += 2
                        self._emit_case_body(out, body, type_facts_0)
                        self.ctx.indent_level -= 2
                # Close last if/else and add goto fallback if needed
                if has_unguarded:
                    out.write(f"{inner}}}\n")
                elif default_label is not None and labels != ["default"]:
                    out.write(f"{inner}}}\n")
                    out.write(f"{inner}goto {default_label};\n")
                else:
                    out.write(f"{inner}}}\n")

            out.write(f"{inner}break;\n")
            out.write(f"{indent}}}\n")

        # Synthetic default for non-exhaustive switches. -Wswitch (in -Wall)
        # flags an enum switch that doesn't list every enum value; non-
        # exhaustive matches on int/bool don't trigger it but adding a
        # default is harmless. Skipped when the user already provided a
        # wildcard arm (`has_default`) or when sema proved the match
        # exhaustive (so future enum members added upstream still trigger
        # -Wswitch instead of being silently swallowed).
        if not has_default and not is_exhaustive:
            out.write(f"{indent}default: break;\n")

        out.write(f"{indent}}}\n")

    def _emit_binding(
        self, out: TextIO, escaped: str | None, raw: str | None,
        subject_expr: str, indent: str,
    ) -> None:
        """Emit a variable binding if name is not None.

        Uses assignment if pre-declared (leaked from match arm), auto& otherwise.
        """
        if escaped is None:
            return
        raw_name = raw or escaped
        # Resumable frame (H1): a bound name that is a frame field must be
        # WRITTEN to the field, not re-declared as a shadowing C++ local --
        # in a decomposed (suspending) match the arm body is a separate
        # state that reads the live field, so an `auto&` dispatch-local
        # would be lost at the state split. The value/frame_slot/pointer
        # split mirrors the field-write logic in `_gen_assign`.
        # NB: `generator_field_names` includes forwarded proto-param aliases
        # (which have no real field); this path has no `generator_storage_name`
        # redirect, but a forwarded local is a protocol-typed alias and so
        # can't be a match-capture target today -- unreachable, see BUGS.md.
        if (self.ctx.in_generator_body
                and raw_name in self.ctx.generator_field_names):
            if raw_name in self.ctx.pointer_locals:
                # Pointer-repr `Optional` field: bridge storage form
                # (`std::optional<T>`) to the borrow-form `T*` frame slot via
                # `optional_to_ptr` (raw `&` would take the address of the
                # optional -- wrong type). The pointer aliases into the
                # subject, so a non-lvalue subject (dispatch-local copy) would
                # dangle across the suspension: reject rather than miscompile.
                if not self.ctx.resumable_match_subject_is_lvalue:
                    raise CodeGenError(
                        "binding a `T | None` field from a non-lvalue "
                        "`match` subject inside a generator/`async` is not "
                        "yet supported (the binding would dangle across a "
                        "suspension); bind the subject to a local first",
                        loc=self.ctx.resumable_match_loc)
                out.write(
                    f"{indent}{escaped} = "
                    f"::tpy::optional_to_ptr({subject_expr});\n")
            elif raw_name in self.ctx.generator_frame_slot_locals:
                out.write(f"{indent}{escaped}.emplace({subject_expr});\n")
            else:
                out.write(f"{indent}{escaped} = {subject_expr};\n")
            return
        if raw_name in self.ctx.declared_vars:
            out.write(f"{indent}{escaped} = {subject_expr};\n")
        else:
            out.write(f"{indent}auto& {escaped} = {subject_expr};\n")

    def _emit_switch_binding(
        self, out: TextIO, cap: str | None, as_name: str | None,
        raw_names: set[str], inner: str,
        subject_expr: str = "__match_subject",
    ) -> None:
        """Emit capture/as binding in a switch arm, respecting declared_vars."""
        for escaped, raw in self._binding_pairs(cap, as_name, raw_names):
            self._emit_binding(out, escaped, raw, subject_expr, inner)

    @staticmethod
    def _binding_pairs(
        cap: str | None, as_name: str | None, raw_names: set[str],
    ) -> list[tuple[str, str]]:
        """Return (escaped_name, raw_name) pairs for binding emission."""
        pairs: list[tuple[str, str]] = []
        # raw_names may contain 1 or 2 entries; match escaped names to raw
        raw_list = list(raw_names)
        if cap is not None:
            raw = next((r for r in raw_list if escape_cpp_name(r) == cap), cap)
            pairs.append((cap, raw))
        if as_name is not None and as_name != cap:
            raw = next((r for r in raw_list if escape_cpp_name(r) == as_name), as_name)
            pairs.append((as_name, raw))
        return pairs

    def _unwrap_as_pattern(
        self, pattern: TpyPattern,
    ) -> tuple[TpyPattern, str | None, str | None]:
        """Unwrap as-pattern, returning (inner_pattern, escaped_as_name, raw_as_name)."""
        if isinstance(pattern, TpyAsPattern):
            return pattern.pattern, escape_cpp_name(pattern.name), pattern.name
        return pattern, None, None

    def _gen_switch_default_arm(
        self, out: TextIO, pattern: TpyWildcardPattern | TpyCapturePattern,
        as_name: str | None, as_raw_name: str | None,
        body: list[TpyStmt], indent: str, inner: str,
        type_facts: dict[str, TpyType] | None = None,
    ) -> None:
        """Emit a default: arm in a switch statement."""
        out.write(f"{indent}default: {{\n")
        if isinstance(pattern, TpyCapturePattern):
            self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, "__match_subject", inner)
        self._emit_binding(out, as_name, as_raw_name, "__match_subject", inner)
        self.ctx.indent_level += 1
        self._emit_case_body(out, body, type_facts)
        self.ctx.indent_level -= 1
        out.write(f"{inner}break;\n")
        out.write(f"{indent}}}\n")

    def _gen_match_guarded_union(
        self, out: TextIO, stmt: TpyMatch, subject_type: UnionType, indent: str,
    ) -> None:
        """Generate guarded match on union using switch(index()) with guards inside case blocks.

        Groups arms by variant type so each type is checked exactly once via
        switch, instead of duplicating holds_alternative per arm.
        """
        self.ctx.match_counter += 1
        end_label = f"__match_end_{self.ctx.match_counter}"
        inner = INDENT * (self.ctx.indent_level + 1)
        inner2 = INDENT * (self.ctx.indent_level + 2)
        is_ptr_var = self._subject_is_ptr_variant(stmt.subject, subject_type)
        va = VariantAccess("__match_subject", subject_type, is_ptr_variant=is_ptr_var)
        # Variant indices come from the wrapper struct's std::variant ordering;
        # for narrowed recursive unions this is wider than subject_type.members.
        wrapper = subject_type.wrapper_info()
        full_members = wrapper.full_members if wrapper is not None else subject_type.members

        # Collect arms per variant type index.
        # Each entry: (case, pattern_for_this_type, as_name, as_raw_name)
        type_arms: dict[int, list[tuple[TpyMatchCase, TpyPattern, str | None, str | None]]] = {
            i: [] for i in range(len(full_members))
        }

        for case in stmt.cases:
            pattern, as_name, as_raw_name = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern):
                assert pattern.resolved_type is not None
                idx = self._variant_index(subject_type, pattern.resolved_type)
                type_arms[idx].append((case, pattern, as_name, as_raw_name))

            elif isinstance(pattern, TpyOrPattern):
                # Track which indices got a class alt from this or-pattern
                # so wildcard alts don't duplicate into the same index
                or_covered: set[int] = set()
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern):
                        assert alt.resolved_type is not None
                        idx = self._variant_index(subject_type, alt.resolved_type)
                        type_arms[idx].append((case, alt, as_name, as_raw_name))
                        or_covered.add(idx)
                    elif isinstance(alt, (TpyWildcardPattern, TpyCapturePattern)):
                        for idx in type_arms:
                            if idx not in or_covered:
                                type_arms[idx].append((case, alt, as_name, as_raw_name))
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alternative: {type(alt).__name__}")

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                for idx in type_arms:
                    type_arms[idx].append((case, pattern, as_name, as_raw_name))

            else:
                raise CodeGenError(
                    f"Unsupported pattern in guarded union match: {type(pattern).__name__}")

        # Truncate each type's arm list after the first truly unguarded arm
        # (anything after an unguarded arm is unreachable).
        # Arms with union field guards are effectively guarded even if guard is None.
        for idx in type_arms:
            truncated: list[tuple[TpyMatchCase, TpyPattern, str | None, str | None]] = []
            for entry in type_arms[idx]:
                truncated.append(entry)
                pat = entry[1]
                has_field_guard = (isinstance(pat, TpyClassPattern)
                                   and any(self._sub_has_field_condition(sub)
                                           for _, sub in pat.keywords))
                if entry[0].guard is None and not has_field_guard:
                    break
            type_arms[idx] = truncated

        # Types whose arms are ALL wildcards/captures can share default:.
        # All such indices have identical arm lists because wildcard/capture arms
        # are always broadcast to every index uniformly during collection above.
        default_indices: set[int] = set()
        default_arms: list[tuple[TpyMatchCase, TpyPattern, str | None, str | None]] | None = None
        for idx, arms in type_arms.items():
            if arms and all(isinstance(a[1], (TpyWildcardPattern, TpyCapturePattern))
                           for a in arms):
                default_indices.add(idx)
                if default_arms is None:
                    default_arms = arms

        out.write(f"{indent}switch ({va.index_expr()}) {{\n")

        for idx in range(len(full_members)):
            if idx in default_indices:
                continue
            arms = type_arms[idx]
            if not arms:
                continue

            out.write(f"{indent}case {idx}: {{\n")

            # Extract variant value once for this case block
            needs_extraction = any(
                isinstance(a[1], TpyClassPattern)
                and (a[1].keywords or a[0].type_facts or a[2] is not None)
                for a in arms
            )
            case_var = f"__case_{idx}"
            if needs_extraction:
                out.write(f"{inner}auto& {case_var} = {va.get_by_index(idx)};\n")

            use_scope = len(arms) > 1
            for arm_case, arm_pattern, as_name, as_raw_name in arms:
                self.ctx.emit_source_comment(out, arm_case.loc, inner)
                self._gen_guarded_switch_arm_action(
                    out, arm_case, arm_pattern, case_var if needs_extraction else None,
                    as_name, as_raw_name, inner, inner2, end_label,
                    needs_scope=use_scope,
                )

            out.write(f"{inner}break;\n")
            out.write(f"{indent}}}\n")

        if default_indices and default_arms:
            out.write(f"{indent}default: {{\n")
            use_scope = len(default_arms) > 1
            for arm_case, arm_pattern, as_name, as_raw_name in default_arms:
                self.ctx.emit_source_comment(out, arm_case.loc, inner)
                self._gen_guarded_switch_arm_action(
                    out, arm_case, arm_pattern, None,
                    as_name, as_raw_name, inner, inner2, end_label,
                    needs_scope=use_scope,
                )
            out.write(f"{inner}break;\n")
            out.write(f"{indent}}}\n")

        out.write(f"{indent}}}\n")
        out.write(f"{end_label}:;\n")

    def _gen_guarded_switch_arm_action(
        self, out: TextIO, arm_case: TpyMatchCase, pattern: TpyPattern,
        case_var: str | None,
        as_name: str | None, as_raw_name: str | None,
        inner: str, inner2: str, end_label: str,
        needs_scope: bool = False,
    ) -> None:
        """Emit a single arm action within a switch case block.

        When needs_scope is True, wraps bindings+body in { } to avoid name
        conflicts with other arms in the same case block.
        """
        bind_indent = inner2 if needs_scope else inner

        if needs_scope:
            out.write(f"{inner}{{\n")

        # Emit guarded/unguarded body with goto
        has_narrowing = isinstance(pattern, TpyClassPattern)
        saved: dict[str, str | None] = {}
        guard = arm_case.guard

        # Union field guards generate implicit conditions even without explicit guards
        field_conds = (self._record_field_conditions(pattern, case_var)
                       if isinstance(pattern, TpyClassPattern) and case_var else [])

        # Emit non-union bindings before the condition (guards may reference them).
        # Union field bindings (std::get) must go after the holds_alternative check.
        if isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
            if isinstance(pattern, TpyCapturePattern):
                self._emit_binding(
                    out, escape_cpp_name(pattern.name), pattern.name,
                    "__match_subject", bind_indent)
            self._emit_binding(out, as_name, as_raw_name, "__match_subject", bind_indent)
        elif isinstance(pattern, TpyClassPattern) and not field_conds:
            if pattern.keywords:
                self._gen_match_field_bindings(out, pattern, case_var, bind_indent)
            self._emit_binding(out, as_name, as_raw_name, case_var, bind_indent)
        elif not isinstance(pattern, TpyClassPattern):
            raise CodeGenError(
                f"Unsupported pattern in guarded switch arm: {type(pattern).__name__}")

        if guard is not None or field_conds:
            cond_parts: list[str] = list(field_conds)
            if guard is not None:
                guard_code = self.expressions.gen_expr(guard)
                self.ctx.temps.flush(out, bind_indent)
                cond_parts.append(guard_code)
            out.write(f"{bind_indent}if ({' && '.join(cond_parts)}) {{\n")
            # Emit union field bindings inside the condition block
            body_indent = INDENT * (self.ctx.indent_level + (3 if needs_scope else 2))
            if isinstance(pattern, TpyClassPattern) and field_conds:
                if pattern.keywords:
                    self._gen_match_field_bindings(out, pattern, case_var, body_indent)
                self._emit_binding(out, as_name, as_raw_name, case_var, body_indent)
            if has_narrowing:
                saved = self._apply_narrowing(arm_case.type_facts, case_var)
            extra = 3 if needs_scope else 2
            self.ctx.indent_level += extra
            self._emit_case_body(out, arm_case.body, arm_case.type_facts)
            out.write(f"{INDENT * self.ctx.indent_level}goto {end_label};\n")
            self.ctx.indent_level -= extra
            if has_narrowing:
                self.stmts.ctx.restore_narrowed_vars(saved)
            out.write(f"{bind_indent}}}\n")
        else:
            if has_narrowing:
                saved = self._apply_narrowing(arm_case.type_facts, case_var)
            extra = 2 if needs_scope else 1
            self.ctx.indent_level += extra
            self._emit_case_body(out, arm_case.body, arm_case.type_facts)
            out.write(f"{INDENT * self.ctx.indent_level}goto {end_label};\n")
            self.ctx.indent_level -= extra
            if has_narrowing:
                self.stmts.ctx.restore_narrowed_vars(saved)

        if needs_scope:
            out.write(f"{inner}}}\n")

    def _apply_narrowing(
        self, type_facts: dict[str, TpyType] | None, case_var: str | None,
    ) -> dict[str, str | None]:
        """Apply narrowing facts and return saved state for later restoration."""
        saved: dict[str, str | None] = {}
        if type_facts:
            for var_name in type_facts:
                saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                self.ctx.narrowed_vars[var_name] = case_var
        return saved

    def _emit_case_body(
        self, out: TextIO, body: list['TpyStmt'],
        type_facts: dict[str, TpyType] | None = None,
    ) -> None:
        """Emit a case body with literal_facts/protocol_narrowings bounded to the case scope.

        Persistent narrowings introduced inside a body (e.g. `assert isinstance(x, P)`)
        would otherwise bleed into later cases or post-match code. Snapshotting and
        restoring around body emission keeps them scoped to this case.

        When type_facts is provided, LiteralType facts are pushed for dead-branch
        elimination. narrowed_vars std::get extractions are managed separately by
        the caller via _apply_narrowing (they depend on a case_var).
        """
        proto_saved = self.ctx.save_protocol_narrowings()
        lit_saved = self.ctx.save_literal_facts()
        # Persistent isinstance aliases (assert / early-return) emitted inside
        # the case body live only in this case's C++ scope; restore both the
        # alias-name set and narrowed_vars so subsequent cases / post-match
        # code don't reference out-of-scope aliases.
        narrowed_saved = dict(self.ctx.narrowed_vars)
        alias_saved = self.ctx.declared_persistent_aliases.copy()
        if type_facts:
            for var_name, ty in type_facts.items():
                if isinstance(ty, LiteralType):
                    self.ctx.literal_facts[var_name] = ty
        try:
            # Resumable-frame `match` (H1): when a suspending match is
            # being emitted, this arm's body is not emitted inline -- it
            # lives in the state machine and is routed back through the
            # resumable walker. Identify the arm by its body's identity.
            arm_emitter = self.ctx.resumable_arm_emitter
            arm_bb = (self.ctx.resumable_arm_bb_by_body.get(id(body))
                      if arm_emitter is not None else None)
            if arm_emitter is not None and arm_bb is not None:
                arm_emitter(arm_bb)
            else:
                for s in body:
                    self.stmts.gen_stmt(out, s)
        finally:
            self.ctx.narrowed_vars = narrowed_saved
            self.ctx.declared_persistent_aliases = alias_saved
            self.ctx.restore_literal_facts(lit_saved)
            self.ctx.restore_protocol_narrowings(proto_saved)

    def _gen_match_if_elif(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate match/case as an if/elif/else chain (for str and float subjects)."""
        inner = INDENT * (self.ctx.indent_level + 1)

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern = case.pattern
            guard = case.guard

            if isinstance(pattern, TpyLiteralPattern):
                cond = self._gen_literal_cond(pattern)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyOrPattern):
                conds = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyLiteralPattern):
                        conds.append(self._gen_literal_cond(alt))
                    else:
                        raise CodeGenError(f"Unsupported or-pattern alternative: {type(alt).__name__}")
                cond = " || ".join(conds)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"({cond}) && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                # Emit capture binding before the guard check so the guard
                # can reference the captured variable.
                if isinstance(pattern, TpyCapturePattern) and guard is not None:
                    if i > 0:
                        out.write(f"{indent}}}\n")
                    self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, "__match_subject", indent)
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if ({guard_code}) {{\n")
                elif guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{keyword} ({guard_code}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern) and guard is None:
                    self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, "__match_subject", inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyAsPattern):
                inner_pat = pattern.pattern
                as_name = escape_cpp_name(pattern.name)
                if isinstance(inner_pat, (TpyLiteralPattern, TpyValuePattern)):
                    if isinstance(inner_pat, TpyLiteralPattern):
                        cond = self._gen_literal_cond(inner_pat)
                    else:
                        val_code = self.expressions.gen_expr(inner_pat.expr)
                        self.ctx.temps.flush(out, indent)
                        cond = f"__match_subject == {val_code}"
                    if guard is not None:
                        # Guard may reference as-variable; split into match + bind + guard
                        out.write(f"{indent}{keyword} ({cond}) {{\n")
                        self._emit_binding(out, as_name, pattern.name, "__match_subject", inner)
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, inner)
                        out.write(f"{inner}if ({guard_code}) {{\n")
                        self.ctx.indent_level += 2
                        self._emit_case_body(out, case.body, case.type_facts)
                        self.ctx.indent_level -= 2
                        out.write(f"{inner}}}\n")
                    else:
                        out.write(f"{indent}{keyword} ({cond}) {{\n")
                        self._emit_binding(out, as_name, pattern.name, "__match_subject", inner)
                        self.ctx.indent_level += 1
                        self._emit_case_body(out, case.body, case.type_facts)
                        self.ctx.indent_level -= 1
                elif isinstance(inner_pat, (TpyWildcardPattern, TpyCapturePattern)):
                    if guard is not None:
                        # Emit binding before guard (like capture+guard path)
                        if i > 0:
                            out.write(f"{indent}}}\n")
                        self._emit_binding(out, as_name, pattern.name, "__match_subject", indent)
                        if isinstance(inner_pat, TpyCapturePattern):
                            self._emit_binding(out, escape_cpp_name(inner_pat.name), inner_pat.name, "__match_subject", indent)
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}if ({guard_code}) {{\n")
                    else:
                        if i == 0:
                            out.write(f"{indent}{{\n")
                        else:
                            out.write(f"{indent}}} else {{\n")
                        self._emit_binding(out, as_name, pattern.name, "__match_subject", inner)
                        if isinstance(inner_pat, TpyCapturePattern):
                            self._emit_binding(out, escape_cpp_name(inner_pat.name), inner_pat.name, "__match_subject", inner)
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                else:
                    raise CodeGenError(f"Unsupported as-pattern inner: {type(inner_pat).__name__}")

            else:
                raise CodeGenError(f"Unsupported match pattern: {type(pattern).__name__}")

        out.write(f"{indent}}}\n")

    def _should_switch_str(self, stmt: TpyMatch) -> bool:
        """Check if a string match has enough unguarded literal cases for switch dispatch."""
        count = 0
        for case in stmt.cases:
            if case.guard is not None:
                continue
            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            if isinstance(pat, TpyLiteralPattern) and isinstance(pat.value, str):
                count += 1
            elif isinstance(pat, TpyOrPattern):
                if all(isinstance(a, TpyLiteralPattern) and isinstance(a.value, str)
                       for a in pat.patterns):
                    count += len(pat.patterns)
        return count >= STRING_SWITCH_THRESHOLD

    def _gen_match_switch_str(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate optimized switch-based dispatch for string match/case."""
        inner = INDENT * (self.ctx.indent_level + 1)
        deep = INDENT * (self.ctx.indent_level + 2)

        self.ctx.match_counter += 1
        end_label = f"__match_end_{self.ctx.match_counter}"

        # Partition cases into guarded literals, unguarded literals, and trailing
        guarded: list[TpyMatchCase] = []
        # Each unguarded entry: (case, list_of_string_values)
        unguarded: list[tuple[TpyMatchCase, list[str]]] = []
        trailing: list[TpyMatchCase] = []

        for case in stmt.cases:
            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            is_str_lit = isinstance(pat, TpyLiteralPattern) and isinstance(pat.value, str)
            is_str_or = (isinstance(pat, TpyOrPattern) and
                         all(isinstance(a, TpyLiteralPattern) and isinstance(a.value, str)
                             for a in pat.patterns))
            if is_str_lit or is_str_or:
                if case.guard is not None:
                    guarded.append(case)
                else:
                    strs = [pat.value] if is_str_lit else [a.value for a in pat.patterns]
                    unguarded.append((case, strs))
            else:
                trailing.append(case)

        # Collect all strings and find best discriminator
        all_strings = []
        for _, strs in unguarded:
            all_strings.extend(strs)
        kind, param, _buckets = find_best_discriminator(all_strings)

        # Build bucket -> [(case, string_value)] mapping, preserving arm order
        bucket_entries: dict[int, list[tuple[TpyMatchCase, str]]] = defaultdict(list)
        for case, strs in unguarded:
            for s in strs:
                key = len(s) if kind == "length" else ord(s[param])
                bucket_entries[key].append((case, s))

        # Emit guarded string literal arms first (pre-switch, in original order)
        for case in guarded:
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            if isinstance(pattern, TpyLiteralPattern):
                cond = self._gen_literal_cond(pattern)
            else:
                # Or-pattern
                conds = [self._gen_literal_cond(a) for a in pattern.patterns]
                cond = " || ".join(conds)
            guard_code = self.expressions.gen_expr(case.guard)
            self.ctx.temps.flush(out, indent)
            is_or = isinstance(pattern, TpyOrPattern)
            cond = f"({cond}) && {guard_code}" if is_or else f"{cond} && {guard_code}"
            out.write(f"{indent}if ({cond}) {{\n")
            self._emit_binding(out, as_name, as_raw, "__match_subject", inner)
            self.ctx.indent_level += 1
            self._emit_case_body(out, case.body, case.type_facts)
            self.ctx.indent_level -= 1
            out.write(f"{inner}goto {end_label};\n")
            out.write(f"{indent}}}\n")

        # Emit switch on discriminator
        # For char_at, wrap in an if-guard so short strings skip the switch
        sw_indent = indent
        sw_inner = inner
        sw_deep = deep
        if kind == "char_at":
            out.write(f"{indent}if (__match_subject.size() >= {param + 1}) {{\n")
            sw_indent = inner
            sw_inner = deep
            sw_deep = INDENT * (self.ctx.indent_level + 3)
            out.write(f"{sw_indent}switch (static_cast<unsigned char>(__match_subject[{param}])) {{\n")
        else:
            out.write(f"{indent}switch (__match_subject.size()) {{\n")

        for disc_value in sorted(bucket_entries.keys()):
            entries = bucket_entries[disc_value]
            if kind == "char_at":
                ch = chr(disc_value)
                out.write(f"{sw_indent}case '{escape_cpp_char(ch)}': {{\n")
            else:
                out.write(f"{sw_indent}case {disc_value}: {{\n")
            for case, string_val in entries:
                self.ctx.emit_source_comment(out, case.loc, sw_inner)
                pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
                out.write(f'{sw_inner}if (__match_subject == {cpp_string_literal_expr(string_val)}) {{\n')
                self._emit_binding(out, as_name, as_raw, "__match_subject", sw_deep)
                self.ctx.indent_level += (3 if kind == "char_at" else 2)
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= (3 if kind == "char_at" else 2)
                out.write(f"{sw_deep}goto {end_label};\n")
                out.write(f"{sw_inner}}}\n")
            out.write(f"{sw_inner}break;\n")
            out.write(f"{sw_indent}}}\n")

        out.write(f"{sw_indent}}}\n")  # close switch
        if kind == "char_at":
            out.write(f"{indent}}}\n")  # close if-guard

        # Emit trailing arms (wildcard, capture, etc.) in a block scope
        # to prevent goto from crossing variable declarations
        if trailing:
            out.write(f"{indent}{{\n")
            for case in trailing:
                self.ctx.emit_source_comment(out, case.loc, inner)
                pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
                if isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                    if isinstance(pattern, TpyCapturePattern):
                        self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, "__match_subject", inner)
                    self._emit_binding(out, as_name, as_raw, "__match_subject", inner)
                    if case.guard is not None:
                        guard_code = self.expressions.gen_expr(case.guard)
                        self.ctx.temps.flush(out, inner)
                        out.write(f"{inner}if ({guard_code}) {{\n")
                        self.ctx.indent_level += 2
                        self._emit_case_body(out, case.body, case.type_facts)
                        self.ctx.indent_level -= 2
                        out.write(f"{inner}}}\n")
                    else:
                        self.ctx.indent_level += 1
                        self._emit_case_body(out, case.body, case.type_facts)
                        self.ctx.indent_level -= 1
                else:
                    raise CodeGenError(
                        f"Unsupported trailing pattern in string switch: {type(pattern).__name__}"
                    )
            out.write(f"{indent}}}\n")

        out.write(f"{indent}{end_label}:;\n")

    def _gen_match_if_elif_record(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate match/case as if/elif chain for concrete record subjects (no guards)."""
        inner = INDENT * (self.ctx.indent_level + 1)

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern):
                conds = self._record_field_conditions(pattern)
                if conds:
                    out.write(f"{indent}{keyword} ({' && '.join(conds)}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                self._gen_match_field_bindings(out, pattern, "__match_subject", inner)
                self._emit_binding(out, as_name, as_raw, "__match_subject", inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyOrPattern):
                or_parts: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern):
                        alt_conds = self._record_field_conditions(alt)
                        if alt_conds:
                            or_parts.append("(" + " && ".join(alt_conds) + ")")
                    elif isinstance(alt, TpyWildcardPattern):
                        or_parts.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alternative for record: "
                            f"{type(alt).__name__}"
                        )
                if or_parts:
                    out.write(f"{indent}{keyword} ({' || '.join(or_parts)}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, "__match_subject", inner)
                self._emit_binding(out, as_name, as_raw, "__match_subject", inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            else:
                raise CodeGenError(f"Unsupported match pattern for record: {type(pattern).__name__}")

        out.write(f"{indent}}}\n")

    def _gen_match_guarded_record(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate match/case for record subjects with guards using standalone ifs + goto."""
        inner = INDENT * (self.ctx.indent_level + 1)
        self.ctx.match_counter += 1
        end_label = f"__match_end_{self.ctx.match_counter}"

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyClassPattern):
                conds = self._record_field_conditions(pattern)
                if conds:
                    out.write(f"{indent}if ({' && '.join(conds)}) {{\n")
                else:
                    out.write(f"{indent}{{\n")
                self._gen_match_field_bindings(out, pattern, "__match_subject", inner)
                self._emit_binding(out, as_name, as_raw, "__match_subject", inner)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, inner)
                    out.write(f"{inner}if ({guard_code}) {{\n")
                    self.ctx.indent_level += 2
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 2
                    out.write(f"{inner}    goto {end_label};\n")
                    out.write(f"{inner}}}\n")
                else:
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}goto {end_label};\n")
                out.write(f"{indent}}}\n")

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if isinstance(pattern, TpyCapturePattern):
                    self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, "__match_subject", indent)
                self._emit_binding(out, as_name, as_raw, "__match_subject", indent)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if ({guard_code}) {{\n")
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}goto {end_label};\n")
                    out.write(f"{indent}}}\n")
                else:
                    out.write(f"{indent}{{\n")
                    self.ctx.indent_level += 1
                    self._emit_case_body(out, case.body, case.type_facts)
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")

            elif isinstance(pattern, TpyOrPattern):
                or_parts: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern):
                        alt_conds = self._record_field_conditions(alt)
                        if alt_conds:
                            or_parts.append("(" + " && ".join(alt_conds) + ")")
                    elif isinstance(alt, TpyWildcardPattern):
                        or_parts.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alternative for record: "
                            f"{type(alt).__name__}"
                        )
                if or_parts:
                    cond = " || ".join(or_parts)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}if ({cond}) {{\n")
                else:
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}if ({guard_code}) {{\n")
                    else:
                        out.write(f"{indent}{{\n")
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1
                out.write(f"{inner}goto {end_label};\n")
                out.write(f"{indent}}}\n")

            else:
                raise CodeGenError(f"Unsupported match pattern for record: {type(pattern).__name__}")

        out.write(f"{indent}{end_label}:;\n")


    # ------------------------------------------------------------------
    # Optimized Optional match: hoist null check, dispatch inner
    # ------------------------------------------------------------------

    def _partition_optional_cases(
        self, cases: list['TpyMatchCase'],
    ) -> tuple[list['TpyMatchCase'], list['TpyMatchCase']] | None:
        """Split cases into (none_cases, inner_cases) if None arms form a prefix.

        Returns None if the optimization cannot be applied:
        - None arms don't form a contiguous prefix
        - An or-pattern mixes None and non-None alternatives
        - A None arm has a guard (guard failure needs fallthrough to later arms)
        """
        none_cases: list[TpyMatchCase] = []
        inner_cases: list[TpyMatchCase] = []
        seen_inner = False

        for case in cases:
            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern

            # Or-pattern mixing None and non-None -- bail out
            if isinstance(pat, TpyOrPattern):
                has_none = any(
                    isinstance(a, TpyLiteralPattern) and a.value is None
                    for a in pat.patterns
                )
                has_other = any(
                    not (isinstance(a, TpyLiteralPattern) and a.value is None)
                    for a in pat.patterns
                )
                if has_none and has_other:
                    return None
                if has_none:
                    if seen_inner:
                        return None
                    if case.guard is not None:
                        return None
                    none_cases.append(case)
                else:
                    seen_inner = True
                    inner_cases.append(case)
                continue

            is_none = isinstance(pat, TpyLiteralPattern) and pat.value is None
            if is_none:
                if seen_inner:
                    return None
                # Guarded None arm needs fallthrough to later arms on guard failure
                if case.guard is not None:
                    return None
                none_cases.append(case)
            else:
                seen_inner = True
                inner_cases.append(case)

        if not inner_cases:
            return None
        return none_cases, inner_cases

    def _gen_match_optimized_optional(
        self, out: TextIO, stmt: TpyMatch, subject_type: OptionalType,
        none_cases: list['TpyMatchCase'], inner_cases: list['TpyMatchCase'],
        indent: str,
    ) -> None:
        """Generate optimized Optional match: if (null) { ... } else { dispatch }."""
        inner = INDENT * (self.ctx.indent_level + 1)
        uses_ptr = subject_type.uses_pointer_repr()
        null_cond = "__match_subject == nullptr" if uses_ptr else "!__match_subject.has_value()"

        # --- None branch ---
        has_value_cond = "__match_subject != nullptr" if uses_ptr else "__match_subject.has_value()"
        if not none_cases:
            # No None arms -- just guard on has_value, no else
            out.write(f"{indent}if ({has_value_cond}) {{\n")
        elif len(none_cases) == 1 and none_cases[0].guard is None:
            # Simple: single unguarded None arm
            case = none_cases[0]
            self.ctx.emit_source_comment(out, case.loc, indent)
            out.write(f"{indent}if ({null_cond}) {{\n")
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            self._emit_binding(out, as_name, as_raw, "__match_subject", inner)
            self.ctx.indent_level += 1
            self._emit_case_body(out, case.body, case.type_facts)
            self.ctx.indent_level -= 1
        else:
            # Multiple or guarded None arms: guard chain inside null block
            self.ctx.emit_source_comment(out, none_cases[0].loc, indent)
            out.write(f"{indent}if ({null_cond}) {{\n")
            for j, case in enumerate(none_cases):
                if j > 0:
                    self.ctx.emit_source_comment(out, case.loc, inner)
                pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
                if case.guard is not None:
                    guard_code = self.expressions.gen_expr(case.guard)
                    self.ctx.temps.flush(out, inner)
                    kw = "if" if j == 0 else "} else if"
                    out.write(f"{inner}{kw} ({guard_code}) {{\n")
                else:
                    if j == 0:
                        pass  # body goes directly in null block
                    else:
                        out.write(f"{inner}}} else {{\n")
                deep = INDENT * (self.ctx.indent_level + 2) if case.guard is not None or j > 0 else inner
                self._emit_binding(out, as_name, as_raw, "__match_subject", deep)
                extra = 2 if case.guard is not None or j > 0 else 1
                self.ctx.indent_level += extra
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= extra
            if any(c.guard is not None for c in none_cases):
                out.write(f"{inner}}}\n")

        # --- Inner value dispatch ---
        if none_cases:
            out.write(f"{indent}}} else {{\n")
        deref = "(*__match_subject)"
        out.write(f"{inner}auto& __match_inner = {deref};\n")

        inner_type = subject_type.inner
        if is_enum_type(inner_type):
            groups = self._group_switch_arms(inner_cases, kind="enum")
            self.ctx.indent_level += 1
            self._emit_switch_groups(out, groups, inner, subject_expr="__match_inner")
            self.ctx.indent_level -= 1
        elif is_fixed_int_type(inner_type) or is_bool_type(inner_type):
            groups = self._group_switch_arms(inner_cases, kind="primitive")
            self.ctx.indent_level += 1
            self._emit_switch_groups(out, groups, inner, subject_expr="__match_inner")
            self.ctx.indent_level -= 1
        elif isinstance(inner_type, NominalType) and inner_type.is_user_record:
            self._emit_optional_inner_record(out, inner_cases, inner)
        else:
            # str, float, other: if/elif chain on __match_inner
            self._emit_optional_inner_if_elif(out, inner_cases, inner)

        out.write(f"{indent}}}\n")

    def _emit_optional_inner_record(
        self, out: TextIO, cases: list['TpyMatchCase'], indent: str,
    ) -> None:
        """Emit if/elif chain for record patterns on dereferenced Optional."""
        inner = INDENT * (self.ctx.indent_level + 2)
        subject_expr = "__match_inner"

        for i, case in enumerate(cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyClassPattern):
                field_conds = self._record_field_conditions(pattern, subject_expr)
                cond = " && ".join(field_conds) if field_conds else "true"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}" if field_conds else guard_code
                if not field_conds and guard is None:
                    # Always-matching class pattern -> else
                    if i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                else:
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                # Emit field bindings
                self._gen_match_field_bindings(out, pattern, subject_expr, inner)
                self._emit_binding(out, as_name, as_raw, subject_expr, inner)
                self.ctx.indent_level += 2
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, subject_expr, inner)
                self._emit_binding(out, as_name, as_raw, subject_expr, inner)
                self.ctx.indent_level += 2
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, TpyOrPattern):
                or_conds: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern):
                        fc = self._record_field_conditions(alt, subject_expr)
                        or_conds.append("(" + " && ".join(fc) + ")" if fc else "true")
                    elif isinstance(alt, (TpyWildcardPattern, TpyCapturePattern)):
                        or_conds.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alt in Optional record: {type(alt).__name__}"
                        )
                if or_conds:
                    cond = " || ".join(or_conds)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                else:
                    if i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 2
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 2

            else:
                raise CodeGenError(
                    f"Unsupported pattern in Optional record dispatch: {type(pattern).__name__}"
                )

        out.write(f"{indent}}}\n")

    def _emit_optional_inner_if_elif(
        self, out: TextIO, cases: list['TpyMatchCase'], indent: str,
    ) -> None:
        """Emit if/elif chain for literal/value patterns on dereferenced Optional."""
        inner = INDENT * (self.ctx.indent_level + 2)
        deref = "__match_inner"

        for i, case in enumerate(cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyLiteralPattern):
                cond = self._gen_literal_cond(pattern, deref)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self._emit_binding(out, as_name, as_raw, deref, inner)
                self.ctx.indent_level += 2
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, TpyValuePattern):
                val_code = self.expressions.gen_expr(pattern.expr)
                self.ctx.temps.flush(out, indent)
                cond = f"{deref} == {val_code}"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self._emit_binding(out, as_name, as_raw, deref, inner)
                self.ctx.indent_level += 2
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, deref, inner)
                self._emit_binding(out, as_name, as_raw, deref, inner)
                self.ctx.indent_level += 2
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, TpyOrPattern):
                or_conds: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyLiteralPattern):
                        or_conds.append(self._gen_literal_cond(alt, deref))
                    elif isinstance(alt, TpyValuePattern):
                        val_code = self.expressions.gen_expr(alt.expr)
                        self.ctx.temps.flush(out, indent)
                        or_conds.append(f"{deref} == {val_code}")
                    elif isinstance(alt, (TpyWildcardPattern, TpyCapturePattern)):
                        or_conds.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alt in Optional inner: {type(alt).__name__}"
                        )
                if or_conds:
                    cond = " || ".join(or_conds)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                else:
                    if i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 2
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 2

            else:
                raise CodeGenError(
                    f"Unsupported pattern in Optional inner dispatch: {type(pattern).__name__}"
                )

        out.write(f"{indent}}}\n")

    def _gen_literal_cond(
        self, pattern: 'TpyLiteralPattern', subject_expr: str = "__match_subject",
    ) -> str:
        """Generate a C++ comparison condition for a literal pattern."""
        val = pattern.value
        if isinstance(val, bool):
            return f"{subject_expr} == {'true' if val else 'false'}"
        elif isinstance(val, int):
            return f"{subject_expr} == {val}"
        elif isinstance(val, float):
            return f"{subject_expr} == {val!r}"
        elif isinstance(val, str):
            return f'{subject_expr} == {cpp_string_literal_expr(val)}'
        else:
            raise CodeGenError(f"Unsupported literal in match: {val!r}")

    def _gen_match_if_elif_optional(
        self, out: TextIO, stmt: TpyMatch, subject_type: OptionalType, indent: str,
    ) -> None:
        """Generate match/case as if/elif chain for Optional subjects."""
        inner = INDENT * (self.ctx.indent_level + 1)
        uses_ptr = subject_type.uses_pointer_repr()
        # Determine how to check null and dereference
        null_cond = "__match_subject == nullptr" if uses_ptr else "!__match_subject.has_value()"
        has_val_cond = "__match_subject != nullptr" if uses_ptr else "__match_subject.has_value()"
        deref = "(*__match_subject)"

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyLiteralPattern) and pattern.value is None:
                # case None:
                cond = null_cond
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self._emit_binding(out, as_name, as_raw, "__match_subject", inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyLiteralPattern):
                # Literal match on inner value (e.g. case 42: on Optional[Int32])
                lit_cond = self._gen_literal_cond(pattern, deref)
                cond = f"{has_val_cond} && {lit_cond}"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self._emit_binding(out, as_name, as_raw, deref, inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyValuePattern):
                # Value pattern on inner type (e.g. case Color.RED: on Optional[Color])
                val_code = self.expressions.gen_expr(pattern.expr)
                self.ctx.temps.flush(out, indent)
                cond = f"{has_val_cond} && {deref} == {val_code}"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self._emit_binding(out, as_name, as_raw, deref, inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyClassPattern):
                # Class pattern on inner record type (e.g. case Point(x=0): on Optional[Point])
                field_conds = self._record_field_conditions(pattern, deref)
                cond_parts = [has_val_cond] + field_conds
                cond = " && ".join(cond_parts)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                # Emit field bindings using dereferenced subject
                self._gen_match_field_bindings(out, pattern, deref, inner)
                self._emit_binding(out, as_name, as_raw, deref, inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                needs_deref = (
                    isinstance(pattern, TpyCapturePattern) or as_name is not None
                )
                if isinstance(pattern, TpyCapturePattern) and guard is not None:
                    # Guarded capture: condition on has_value + guard
                    cond = f"{has_val_cond} && {self.expressions.gen_expr(guard)}"
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                elif guard is not None:
                    # Guarded wildcard (no capture)
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{keyword} ({guard_code}) {{\n")
                elif needs_deref:
                    # Unguarded capture: guard on has_value to avoid UB
                    out.write(f"{indent}{keyword} ({has_val_cond}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    self._emit_binding(out, escape_cpp_name(pattern.name), pattern.name, deref, inner)
                self._emit_binding(out, as_name, as_raw, deref, inner)
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyOrPattern):
                # OR of None/literal/value/class conditions
                or_conds: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyLiteralPattern) and alt.value is None:
                        or_conds.append(null_cond)
                    elif isinstance(alt, TpyLiteralPattern):
                        lit_c = self._gen_literal_cond(alt, deref)
                        or_conds.append(f"({has_val_cond} && {lit_c})")
                    elif isinstance(alt, TpyValuePattern):
                        val_code = self.expressions.gen_expr(alt.expr)
                        self.ctx.temps.flush(out, indent)
                        or_conds.append(f"({has_val_cond} && {deref} == {val_code})")
                    elif isinstance(alt, TpyClassPattern):
                        fc = self._record_field_conditions(alt, deref)
                        parts = [has_val_cond] + fc
                        or_conds.append("(" + " && ".join(parts) + ")")
                    elif isinstance(alt, TpyWildcardPattern):
                        or_conds.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alt for Optional: {type(alt).__name__}"
                        )
                if or_conds:
                    cond = " || ".join(or_conds)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                else:
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}{keyword} ({guard_code}) {{\n")
                    elif i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 1
                self._emit_case_body(out, case.body, case.type_facts)
                self.ctx.indent_level -= 1

            else:
                raise CodeGenError(
                    f"Unsupported match pattern for Optional: {type(pattern).__name__}"
                )

        out.write(f"{indent}}}\n")

    def _record_field_conditions(
        self, pattern: 'TpyClassPattern', subject_expr: str = "__match_subject",
    ) -> list[str]:
        """Generate C++ field comparison conditions for a record class pattern."""
        conds: list[str] = []
        for field_name, sub_pattern in pattern.keywords:
            inner = sub_pattern
            if isinstance(inner, TpyAsPattern):
                inner = inner.pattern
            if isinstance(inner, TpyLiteralPattern):
                val = inner.value
                if val is None:
                    field_type = self._record_field_type(pattern, field_name)
                    expr = f"{subject_expr}.{field_name}"
                    if isinstance(field_type, OptionalType):
                        conds.append(f"!{expr}.has_value()")
                    elif isinstance(field_type, UnionType):
                        conds.append(
                            f"std::holds_alternative<std::monostate>({expr})")
                    else:
                        # Sema gates `=None` to nullable fields, so the repr is
                        # always optional or union-with-None; a miss here means
                        # that invariant broke -- fail loud, never silently drop
                        # the check (the bug this branch fixed).
                        raise CodeGenError(
                            f"internal: `{field_name}=None` field pattern on "
                            f"non-nullable type {field_type}", loc=pattern.loc)
                elif isinstance(val, bool):
                    conds.append(f"{subject_expr}.{field_name} == {'true' if val else 'false'}")
                elif isinstance(val, int):
                    conds.append(f"{subject_expr}.{field_name} == {val}")
                elif isinstance(val, float):
                    conds.append(f"{subject_expr}.{field_name} == {val!r}")
                elif isinstance(val, str):
                    conds.append(f'{subject_expr}.{field_name} == {cpp_string_literal_expr(val)}')
            elif isinstance(inner, TpyClassPattern) and inner.is_union_field_guard:
                # Union-typed field: runtime holds_alternative check
                assert inner.resolved_type is not None
                cpp_type = self.types.type_to_cpp(inner.resolved_type)
                conds.append(f"std::holds_alternative<{cpp_type}>({subject_expr}.{field_name})")
                # Recurse for nested field conditions on the variant member
                if inner.keywords:
                    get_expr = f"std::get<{cpp_type}>({subject_expr}.{field_name})"
                    nested = self._record_field_conditions(inner, get_expr)
                    conds.extend(nested)
            elif isinstance(inner, TpyClassPattern) and not inner.is_union_field_guard and inner.keywords:
                # Non-union record field with nested sub-patterns: recurse through it
                nested = self._record_field_conditions(inner, f"{subject_expr}.{field_name}")
                conds.extend(nested)
        return conds

    @staticmethod
    def _sub_has_field_condition(sub: TpyPattern) -> bool:
        """Whether a field sub-pattern emits a runtime condition (mirrors what
        `_record_field_conditions` produces): a literal comparison, a union
        field guard, or a nested record sub-pattern that itself carries one.
        Such a sub-pattern makes its arm conditional -- a later arm on the same
        variant stays reachable."""
        inner = sub.pattern if isinstance(sub, TpyAsPattern) else sub
        if isinstance(inner, TpyLiteralPattern):
            return True
        if isinstance(inner, TpyClassPattern):
            if inner.is_union_field_guard:
                return True
            return any(MatchGenerator._sub_has_field_condition(s)
                       for _, s in inner.keywords)
        return False

    @staticmethod
    def _pattern_has_field_condition(pattern: TpyPattern) -> bool:
        """Whether a case pattern (class or or-pattern alternative) carries a
        field-value condition that the guarded codegen path must emit."""
        if isinstance(pattern, TpyAsPattern):
            pattern = pattern.pattern
        if isinstance(pattern, TpyClassPattern):
            return any(MatchGenerator._sub_has_field_condition(sub)
                       for _, sub in pattern.keywords)
        if isinstance(pattern, TpyOrPattern):
            return any(MatchGenerator._pattern_has_field_condition(alt)
                       for alt in pattern.patterns)
        return False

    def _has_shared_variant_index(self, stmt: TpyMatch, subject_type: UnionType) -> bool:
        """Check if multiple cases resolve to the same variant index (e.g. union field guards)."""
        seen: set[int] = set()
        for case in stmt.cases:
            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            indices: list[int] = []
            if isinstance(pat, TpyClassPattern) and pat.resolved_type is not None:
                indices.append(self._variant_index(subject_type, pat.resolved_type))
            elif isinstance(pat, TpyOrPattern):
                for alt in pat.patterns:
                    if isinstance(alt, TpyClassPattern) and alt.resolved_type is not None:
                        indices.append(self._variant_index(subject_type, alt.resolved_type))
            for idx in indices:
                if idx in seen:
                    return True
                seen.add(idx)
        return False

    def _variant_index(self, union_type: UnionType, member_type: TpyType) -> int:
        """Find the index of a member type in a union's canonical member ordering.

        For recursive unions (including narrowed-by-None subsets), indices are
        resolved against the alias's full member tuple so they match the
        wrapper struct's variant ordering.
        """
        wrapper = union_type.wrapper_info()
        members = wrapper.full_members if wrapper is not None else union_type.members
        for i, m in enumerate(members):
            if m == member_type:
                return i
        raise CodeGenError(f"type '{member_type}' not found in union '{union_type}'")

    def _switch_literal_label(self, pattern: TpyLiteralPattern) -> str:
        """Generate a C++ case label for a literal pattern (int or bool)."""
        val = pattern.value
        if isinstance(val, bool):
            return "true" if val else "false"
        elif isinstance(val, int):
            return str(val)
        else:
            raise CodeGenError(f"Cannot use literal {val!r} in switch case label")

    def _record_field_type(
        self, pattern: TpyClassPattern, field_name: str,
    ) -> 'TpyType | None':
        """Declared type of a class-pattern field, including inherited fields.
        Generic type-param substitution is skipped on purpose: callers inspect
        only the nullable repr (optional vs union), invariant under the arg."""
        record_type = pattern.resolved_type
        if not isinstance(record_type, NominalType):
            return None
        registry = self.ctx.analyzer.registry
        record = registry.get_record_for_type(record_type)
        if record is None:
            return None
        for f in registry.get_all_fields(record):
            if f.name == field_name:
                return f.type
        return None

    def _record_capture_type(
        self, pattern: TpyClassPattern, field_name: str, capture_name: str,
    ) -> None:
        """Register a match-bound capture's type so ``_get_cpp_declared_type``
        (which reads only ``var_types``) drives narrowing-aware deref on it."""
        field_type = self._record_field_type(pattern, field_name)
        if field_type is not None:
            self.ctx.var_types[capture_name] = field_type

    def _gen_match_field_bindings(
        self, out: TextIO, pattern: TpyClassPattern, case_var: str, indent: str,
    ) -> None:
        """Emit local variable bindings for class pattern keyword fields."""
        for field_name, sub_pattern in pattern.keywords:
            if isinstance(sub_pattern, TpyCapturePattern):
                self._emit_binding(out, escape_cpp_name(sub_pattern.name), sub_pattern.name, f"{case_var}.{field_name}", indent)
                self._record_capture_type(pattern, field_name, sub_pattern.name)
            elif isinstance(sub_pattern, TpyWildcardPattern):
                pass
            elif isinstance(sub_pattern, TpyLiteralPattern):
                pass  # Literal sub-patterns handled as conditions (future)
            elif isinstance(sub_pattern, TpyClassPattern):
                if sub_pattern.is_union_field_guard and sub_pattern.keywords:
                    # Union field with nested record patterns: extract variant, bind sub-fields
                    cpp_type = self.types.type_to_cpp(sub_pattern.resolved_type)
                    # Include parent var in temp name to avoid collisions when
                    # sibling fields share a sub-field name
                    parent_sfx = case_var.replace(".", "_").replace("*", "").lstrip("_")
                    temp = f"__field_{parent_sfx}_{field_name}"
                    out.write(f"{indent}auto& {temp} = std::get<{cpp_type}>({case_var}.{field_name});\n")
                    self._gen_match_field_bindings(out, sub_pattern, temp, indent)
                elif not sub_pattern.is_union_field_guard and sub_pattern.keywords:
                    # Compile-time type guard with nested field bindings: recurse with direct access
                    self._gen_match_field_bindings(out, sub_pattern, f"{case_var}.{field_name}", indent)
                # else: type guard only (no nested bindings), no output needed
            elif isinstance(sub_pattern, TpyAsPattern):
                inner_sub = sub_pattern.pattern
                if isinstance(inner_sub, TpyClassPattern):
                    # Type pattern with capture (e.g., value=str() as v)
                    if inner_sub.is_union_field_guard:
                        cpp_type = self.types.type_to_cpp(inner_sub.resolved_type)
                        field_expr = f"std::get<{cpp_type}>({case_var}.{field_name})"
                    else:
                        field_expr = f"{case_var}.{field_name}"
                    self._emit_binding(out, escape_cpp_name(sub_pattern.name), sub_pattern.name, field_expr, indent)
                    # Recurse for nested field bindings
                    if inner_sub.keywords:
                        self._gen_match_field_bindings(out, inner_sub, escape_cpp_name(sub_pattern.name), indent)
                elif isinstance(inner_sub, TpyLiteralPattern):
                    self._emit_binding(out, escape_cpp_name(sub_pattern.name), sub_pattern.name, f"{case_var}.{field_name}", indent)
                elif isinstance(inner_sub, (TpyWildcardPattern, TpyCapturePattern)):
                    if isinstance(inner_sub, TpyCapturePattern):
                        self._emit_binding(out, escape_cpp_name(inner_sub.name), inner_sub.name, f"{case_var}.{field_name}", indent)
                        self._record_capture_type(pattern, field_name, inner_sub.name)
                    self._emit_binding(out, escape_cpp_name(sub_pattern.name), sub_pattern.name, f"{case_var}.{field_name}", indent)
                    self._record_capture_type(pattern, field_name, sub_pattern.name)
