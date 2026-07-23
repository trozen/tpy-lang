"""
TurboPython Statement Code Generation

Generates C++ code from TurboPython statements.
"""

from __future__ import annotations
import io
from contextlib import contextmanager
from typing import Callable, Iterator, TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, PtrType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, PendingViewType, OwnType, OptionalType,
    NoneType, NominalType, AliasRef, AnyType, STR, BYTES, TupleType, VoidType,
    ValueForm,
    INT32, BIGINT, FLOAT, is_protocol_type, is_dyn_protocol, ConcreteCoroType,
    polymorphic_source_is_pointer, polymorphic_subclass_into_optional,
    polymorphic_source_inner,
    is_polymorphic_subclass_fact,
    ReadonlyType, unwrap_readonly, unwrap_optional_own, unwrap_send_sync, TypeParamRef, UnionType, LiteralType, LiteralTag,
    is_own_pointer_repr_optional,
    resolve_int_literals,
    error_return_to_cpp, qualify_exception_name, is_return_exception,
    unwrap_ref_type, RefType, unwrap_qualifiers,
    is_void_like_type, collapse_tuple_own_elements,
)
from ..parse import (
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyDelVar, TpyDelAttr, TpyExprStmt, TpyReturn, TpyYield,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyPassStmt,
    TpyRaise, TpyExceptHandler, TpyTry, TpyWith,
    TpyGlobal, TpyNonlocal, TpyNestedDef,
    TpyImport, TpySubscript, TpySlice, TpyStrLiteral, TpyNoneLiteral, TpyName, TpyExpr, TpyFunction,
    TpyTupleLiteral, TupleElemCapture,
    TpyAssert, TpyBoolLiteral, TpyArrayLiteral,
    TpyFieldAccess, TpyMethodCall,
    TpyBinOp, TpyCall, TpyIntLiteral, TpyUnaryOp, TpyCoerce, TpyIfExpr,
    TpyMatch, TpyNamedExpr,
)
from dataclasses import fields as dc_fields
from ..namespace import Namespace
from ..symbol_binding import SymbolKind
from ..sema.context import PENDING_CONTAINER_TYPES
from ..sema.literal_utils import (
    fixed_int_literal_value_from_expr,
    literal_value_from_expr,
)
from ..sema.registration import build_record_self_type
from ..typesys import view_family_for_type
from ..value_category import wants_move
from .variant_access import VariantAccess
from ..diagnostics import SemanticError
from ..liveness import stmts_terminate, try_terminates_ignoring_finally

from .context import INDENT, CodeGenError, FinallyContext, LocalCppForm, CppForm, FormValue, escape_cpp_name, qualified_cpp_name, loop_var_binding, is_lvalue_iterable, view_key_target, contains_named_expr
from .forms import classify_local_binding, LocalBinding
from ..type_def_registry import (
    is_list, is_dict,
    is_fixed_int_type, is_big_int_type, is_bytes_type, is_str_type,
    is_str_view_type, is_bytes_view_type, is_string_type,
    protocol_info_of,
)
from .expressions import _is_concrete_user_record
from .functions import default_to_cpp
from .type_resolution import resolve_stmt_binding_type, resolve_stmt_type_cascade
from .types import resolve_pending_container
from ..prescan import match_is_none, parse_deref_view_key
from .match import MatchGenerator
from .gen_async import POLL_VOID_READY_RETURN

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .expressions import ExpressionGenerator
    from .builtins import BuiltinGenerator
    from .protocols import ProtocolGenerator


# The ctx set-fields `StatementGenerator.seed_param_locals` mutates -- the single
# authoritative list the ctor member-init save/restore (records._extract_field_inits)
# snapshots. Add here when seed_param_locals starts writing a new set. (var_types,
# a dict, is snapshotted separately by that caller.)
PARAM_LOCAL_SET_FIELDS = (
    "pointer_locals", "const_indirect_locals", "optional_locals",
    "ptr_variant_locals", "movable_locals", "storage_form_tuple_locals",
)


class StatementGenerator:
    """Generates C++ code from TurboPython statements."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        builtins: BuiltinGenerator,
        protocols: ProtocolGenerator,
        expressions: ExpressionGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.builtins = builtins
        self.protocols = protocols
        self.expressions = expressions
        self.match = MatchGenerator(ctx, types, expressions, self)
        self._reassigned_param_copies: list[tuple[str, TpyType]] = []

    def _gen_buffered_body(self, out: TextIO, stmts: list[TpyStmt],
                           track_stmt_line: bool = False) -> None:
        """Buffer body statements, prepend hoist declarations, write to output."""
        body_buf = io.StringIO()
        # Emit mutable owned local copies for reassigned params that cannot be
        # reassigned in place: const-ref / expensive types (BigInt, bytearray),
        # and by-value view params (str/bytes) -- a borrow kept across a
        # reassignment from an owned temporary would dangle SILENTLY, since
        # span/string_view is implicitly constructible from its owned form, so
        # no build error catches it (which is why this is unconditional, not
        # flow-sensitive per reassignment source).
        if self._reassigned_param_copies:
            indent = self.ctx.indent()
            for pname, ptype in self._reassigned_param_copies:
                cpp_name = escape_cpp_name(pname)
                cpp_type = ptype.to_cpp()
                param_ref = f"__param_{cpp_name}"
                # A borrow-form view param copied into its owned-storage local
                # for in-body reassignment needs the explicit view->owned copy:
                # span->vector is not implicit, and str routes the same way for
                # parity (string_view->string would assign implicitly, but the
                # explicit form keeps both families on the one chokepoint helper).
                opt_inner = ptype.inner if isinstance(ptype, OptionalType) else None
                fam = view_family_for_type(opt_inner if opt_inner is not None else ptype)
                if fam is not None and opt_inner is not None:
                    owned = self.expressions._view_owned_copy_expr(fam, f"*{param_ref}")
                    init = f"{param_ref} ? std::make_optional({owned}) : std::nullopt"
                elif fam is not None:
                    init = self.expressions._view_owned_copy_expr(fam, param_ref)
                else:
                    init = param_ref
                body_buf.write(f"{indent}{cpp_type} {cpp_name} = {init};\n")
            self._reassigned_param_copies = []
        for stmt in stmts:
            if self.ctx.overload_terminated:
                break
            if track_stmt_line:
                self.ctx.current_stmt_line = stmt.loc.line if hasattr(stmt, 'loc') and stmt.loc else 0
            self.gen_stmt(body_buf, stmt)
        if track_stmt_line:
            self.ctx.current_stmt_line = 0
        hoist_indent = INDENT * self.ctx.indent_level
        for decl in self.ctx.pending_hoist_decls:
            out.write(f"{hoist_indent}{decl}")
        out.write(body_buf.getvalue())

    def seed_param_locals(self, params: list[tuple[str, TpyType]],
                          local_ns: Namespace,
                          deep_const_borrow_params: set[str]) -> None:
        """Classify params into the pointer-form local sets access dispatch reads
        (`PARAM_LOCAL_SET_FIELDS` + `var_types`) so a pointer-repr param derefs
        with `->` in a ctor member-init initializer as it does in the body."""
        # Optional non-value params are T* / const T* in C++ -- need pointer-local treatment (->)
        for pname, ptype in params:
            # Peel the Send/Sync marker (representationally transparent -- it
            # erases to its inner type in C++) so a Send[Own[T]] param is
            # classified by its Own/pointer/optional shape, not treated as opaque.
            actual = unwrap_readonly(unwrap_send_sync(ptype))
            if self.protocols.is_static_protocol_param(ptype):
                # Static protocol params: check if nullable (uses pointer repr)
                infos = self.protocols.get_all_protocol_params([(pname, ptype)])
                if infos and infos[0].has_none:
                    self.ctx.pointer_locals.add(pname)
                    self.ctx.const_indirect_locals.add(pname)
            elif isinstance(actual, OptionalType) and actual.uses_pointer_repr():
                self.ctx.pointer_locals.add(pname)
                # `const P*` when annotated `readonly[...]` OR when the inferred
                # verdict const-consts it (readonly fn/method whose param address
                # does not escape) -- same addr-escape-aware verdict the signature
                # renders, so borrow-locals off this receiver spell const to match.
                if (isinstance(ptype, ReadonlyType)
                        or pname in deep_const_borrow_params):
                    self.ctx.const_indirect_locals.add(pname)
            # Own[OptionalType[P_ref]]: param renders as `std::optional<P>&&`
            # (storage form), but body access patterns are the same as a
            # storage-form Optional local: arrow for member access (uses
            # optional<P>::operator->), .has_value() for null check, direct
            # std::move into another storage slot. Register as both
            # pointer_local (for arrow access) and optional_local (so the
            # null-check dispatch picks has_value over `!= nullptr`). Rebind
            # the namespace to the bare Optional so type-aware codegen sites
            # match the sibling pointer-repr Optional handling. movable_locals
            # is set below via the generic `unwrap_optional_own + non-value`
            # pass.
            elif is_own_pointer_repr_optional(actual):
                self.ctx.pointer_locals.add(pname)
                self.ctx.optional_locals.add(pname)
                self.ctx.var_types[pname] = actual.wrapped
                local_ns.bind_variable(pname, actual.wrapped)
            # Non-value union params are pointer variants (variant<T*...>)
            elif self.ctx.is_ptr_variant_union(actual):
                self.ctx.ptr_variant_locals.add(pname)
                # Deep-const members (`const T*`) when the param is `readonly[...]`
                # OR the const verdict deep-consts it (readonly fn/method whose
                # param address does not escape). `deep_const_borrow_params` is the
                # same addr-escape-aware verdict the signature and call site read,
                # so the body's `std::get<T*>` matches the param decl.
                if (isinstance(ptype, ReadonlyType)
                        or pname in deep_const_borrow_params):
                    self.ctx.const_indirect_locals.add(pname)
            # Own[T] and Own[T] | None params are movable (caller gave up ownership)
            own_actual = unwrap_optional_own(actual)
            if own_actual is not None and not own_actual.wrapped.is_value_type():
                self.ctx.movable_locals.add(pname)
            if (isinstance(actual, TupleType) and actual.is_owned_movable()
                    and not isinstance(ptype, ReadonlyType)):
                self.ctx.movable_locals.add(pname)
                self.ctx.storage_form_tuple_locals.add(pname)
            # Own[tuple[T | None, ...]] params are stored in storage form
            # (std::tuple<std::optional<T>, ...>); same C++ shape as the
            # storage-form locals registered for storage-form tuple iteration.
            if isinstance(actual, OwnType):
                inner = unwrap_readonly(actual.wrapped)
                if isinstance(inner, TupleType) and inner.has_pointer_repr_element():
                    self.ctx.storage_form_tuple_locals.add(pname)
            # Value-optional params (std::optional<T> by value) are movable when
            # the inner type has an expensive copy (String, BigInt, etc.).
            # readonly params are excluded to respect the no-mutation contract.
            elif (isinstance(actual, OptionalType) and not actual.uses_pointer_repr()
                    and not isinstance(ptype, ReadonlyType)
                    and actual.inner.is_expensive_copy()):
                self.ctx.movable_locals.add(pname)

    @contextmanager
    def seed_param_locals_scoped(
            self, params: list[tuple[str, TpyType]], local_ns: Namespace,
            deep_const_borrow_params: set[str]) -> Iterator[None]:
        """Seed the param classification (`seed_param_locals`) for the body of
        the with-block, then restore the exact ctx sets it writes. For callers
        that run before `setup_body_scope`/`reset_scope` (the ctor member-init
        extraction) where the full scope snapshot isn't usable yet. Owning the
        save/restore here keeps it from drifting out of sync with what
        seed_param_locals mutates."""
        saved = {f: getattr(self.ctx, f).copy() for f in PARAM_LOCAL_SET_FIELDS}
        saved_var_types = dict(self.ctx.var_types)
        try:
            self.seed_param_locals(params, local_ns, deep_const_borrow_params)
            yield
        finally:
            for f, prev in saved.items():
                setattr(self.ctx, f, prev)
            self.ctx.var_types = saved_var_types

    def setup_body_scope(self, params: list[tuple[str, TpyType]],
                         return_type: TpyType, func: TpyFunction,
                         local_ns: Namespace, indent_level: int = 1,
                         is_method: bool = False,
                         record_type_param_bounds: dict[str, TpyType] | None = None,
                         const_ref_params: set[str] | None = None,
                         deep_const_borrow_params: set[str] | None = None,
                         owning_record_name: str | None = None,
                         return_cpp: str | None = None) -> 'ScanResult | None':
        """Reset per-scope ctx state and repopulate it for the given function.

        Shared by gen_body (sync + simple-gen + multi-yield-gen) and async
        body emission (`_resumable_frame_ctx`). Returns the scan result so
        callers can use it for body-emission-specific work
        (reassigned-param copies, etc.).
        """
        self.ctx.reset_scope()
        # Apply literal overload facts (injected by _gen_literal_specialized_function,
        # survives reset_scope like overload_param_types)
        if self.ctx.literal_overload_facts:
            self.ctx.literal_facts.update(self.ctx.literal_overload_facts)
        self.ctx.const_ref_params = const_ref_params if const_ref_params is not None else set()
        self.ctx.deep_const_borrow_params = deep_const_borrow_params if deep_const_borrow_params is not None else set()
        self.ctx.declared_vars = {pname for pname, _ in params}
        self.ctx.var_types = {pname: unwrap_ref_type(ptype) for pname, ptype in params}
        self.ctx.local_scope_names = {pname for pname, _ in params}
        self.ctx.global_declared_vars = self.ctx.analyzer.function_global_decls.get(id(func), set())
        scan = self.ctx.analyzer.function_scan_results.get(id(func))
        if scan:
            self.ctx.reassigned_vars = scan.reassigned - self.ctx.global_declared_vars
            self.ctx.rvalue_reassigned_vars = scan.rvalue_reassigned - self.ctx.global_declared_vars
            self.ctx.lvalue_reassigned_vars = scan.lvalue_reassigned - self.ctx.global_declared_vars
            self.ctx.aliased_vars = set(scan.alias_sources.values())
            self.ctx.alias_names = scan.initial_alias_names
        else:
            self.ctx.reassigned_vars = set()
            self.ctx.rvalue_reassigned_vars = set()
            self.ctx.lvalue_reassigned_vars = set()
            self.ctx.aliased_vars = set()
            self.ctx.alias_names = set()
        self.ctx.hoisted_vars = self.ctx.analyzer.function_hoisted_vars.get(id(func), set())
        self.ctx.move_through_vars = self.ctx.analyzer.function_move_through_vars.get(id(func), set())
        self.ctx.sema_movable_locals = self.ctx.analyzer.function_movable_locals.get(id(func), set())
        self.ctx.sema_ever_owned_locals = self.ctx.analyzer.function_ever_owned_locals.get(id(func), set())
        self.ctx.sema_stmt_borrow_decls = self.ctx.analyzer.function_stmt_borrow_decls.get(id(func), {})
        # Classify params into the pointer-form local sets (pointer_locals,
        # ptr_variant_locals, optional_locals, movable_locals, ...) that access
        # dispatch consults so `->` vs `.` / move / variant-form are correct.
        self.seed_param_locals(params, local_ns, self.ctx.deep_const_borrow_params)
        # Generator-promoted locals are struct fields; pre-seed var_types
        # so codegen sites that consult it (e.g. address-of for tuple
        # slots) see the original TPy type rather than the synthetic
        # outer-optional wrapper used for init tracking.
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                self.ctx.var_types[lname] = ltype
            self.ctx.setup_resumable_frame_locals(func)
        self.ctx.current_ns = local_ns
        self.ctx.indent_level = indent_level
        self.ctx.current_return_type = return_type
        self.ctx.current_return_cpp = return_cpp
        # Set current_yield_type for generator bodies so yield-emission sites
        # don't need it threaded through their call signatures. Skipped for
        # sema-errored generators (no resolved yield type) -- leaves the
        # field at its reset_scope() default rather than crashing later.
        if func.is_generator and func.generator_yield_type is not None:
            self.ctx.current_yield_type = func.generator_yield_type
        raw_error_return = getattr(func, 'error_return', None)
        self.ctx.current_error_return = error_return_to_cpp(raw_error_return, self.ctx.analyzer.ctx.module_name, self.ctx.analyzer.registry) if raw_error_return else None
        self.ctx.current_func_params = {pname: ptype for pname, ptype in params}
        self.ctx.in_property_getter = getattr(func, 'is_property_getter', False)
        self.ctx.current_type_param_bounds = dict(record_type_param_bounds) if record_type_param_bounds else {}
        if func.type_param_bounds:
            self.ctx.current_type_param_bounds.update(func.type_param_bounds)
        if is_method:
            self.ctx.in_method = True
            # `self` resolves to the enclosing record's type during the body
            # so `lookup_var_type('self')` can drive `isinstance(self, Sub)`
            # polymorphic dispatch. None for static methods (no self). Use
            # `build_record_self_type` so the NominalType carries the proper
            # qname + generic type-param refs, matching how sema constructs
            # self's type -- avoids future cross-module short-name collision
            # risk if polymorphic-source predicates ever route through qname
            # equality.
            if owning_record_name is not None:
                rec_info = self.ctx.analyzer.registry.get_record(owning_record_name)
                if rec_info is not None:
                    self.ctx.current_method_record_type = build_record_self_type(
                        rec_info, qname=rec_info.qualified_name())
                else:
                    self.ctx.current_method_record_type = NominalType(owning_record_name)
        self._compute_borrow_tuple_const(func)
        return scan

    def _compute_borrow_tuple_const(self, func: TpyFunction) -> None:
        """Populate `const_borrow_form_tuple_locals`: borrow-form tuple locals
        whose declared element pointers must be `const T*` because some binding
        source is a const-storage location.

        Codegen runs after Phase-2 const inference, so each source's final
        const-ness is known here. The declared const must be at least as const
        as every source feeding the local (mutable->const lift is safe,
        const->mutable would not compile); we therefore OR const over all
        bindings. A bare-name source feeding from another borrow-form tuple
        carries that local's const, so iterate to a fixpoint over name chains.
        """
        bindings: dict[str, list[TpyExpr]] = {}
        # Nullable-borrow-tuple locals tracked separately: their const set is
        # const_optional_borrow_tuple_locals (the inner tuple sits behind a
        # std::optional, but const-ness is inferred from the same sources).
        opt_bindings: dict[str, list[TpyExpr]] = {}

        # A nullable-borrow-tuple local is identified by its TARGET type
        # (`tuple[..., T] | None`), not the source: a rebind source is often a
        # plain `tuple[..., T]` field, but the local stays the optional form.
        optional_targets: set[str] = set()

        def collect(stmts: list[TpyStmt]) -> None:
            for stmt in stmts:
                if isinstance(stmt, TpyVarDecl) and stmt.init is not None:
                    tt = self._resolve_target_type(stmt)
                    if (isinstance(tt, OptionalType)
                            and tt.wraps_pointer_repr_tuple()):
                        optional_targets.add(stmt.name)
                    record(stmt.name, stmt.init)
                elif (isinstance(stmt, TpyAssign)
                      and isinstance(stmt.target, TpyName)):
                    record(stmt.target.name, stmt.value)
                # Walrus (`(t := src)`) binds too -- in conditions, values, etc.
                for e in stmt.exprs():
                    for tgt, src in _walrus_bindings(e):
                        record(tgt, src)
                for body in stmt.sub_bodies():
                    collect(body)

        def record(tgt: str, src: TpyExpr) -> None:
            if tgt not in self.ctx.reassigned_vars and tgt not in self.ctx.hoisted_vars:
                return
            st = self.ctx.analyzer.get_expr_type(src)
            stb = (unwrap_readonly(unwrap_ref_type(st))
                   if st is not None else None)
            is_ptr_repr_tuple = (
                (isinstance(stb, TupleType) and stb.has_pointer_repr_element())
                or (isinstance(stb, OptionalType) and stb.wraps_pointer_repr_tuple()))
            if not is_ptr_repr_tuple:
                return
            if tgt in optional_targets:
                opt_bindings.setdefault(tgt, []).append(src)
            else:
                bindings.setdefault(tgt, []).append(src)

        def _walrus_bindings(expr: TpyExpr | None):
            """Yield (target, value) for every walrus node in `expr` (generic
            dataclass-field recursion, like prescan's walrus scan)."""
            if expr is None:
                return
            if isinstance(expr, TpyNamedExpr):
                yield expr.target, expr.value
            for f in dc_fields(expr):
                val = getattr(expr, f.name)
                if isinstance(val, TpyExpr):
                    yield from _walrus_bindings(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, TpyExpr):
                            yield from _walrus_bindings(item)

        collect(func.body)
        if not bindings and not opt_bindings:
            return
        # Fixpoint over both kinds together: a name chain can cross between a
        # plain borrow-tuple local and a nullable one, and `is_const_storage_source`
        # (consulted via `_tuple_source_is_const`) reads both const sets.
        pairs = [(bindings, self.ctx.const_borrow_form_tuple_locals),
                 (opt_bindings, self.ctx.const_optional_borrow_tuple_locals)]
        changed = True
        while changed:
            changed = False
            for binds, const_set in pairs:
                for name, srcs in binds.items():
                    if name in const_set:
                        continue
                    if any(self._tuple_source_is_const(s) for s in srcs):
                        const_set.add(name)
                        changed = True

    def _tuple_source_is_const(self, src: TpyExpr) -> bool:
        """Whether a borrow-tuple binding source reads from const storage.

        A ternary feeds whichever arm runs, so it is const if either arm is.
        An explicit `readonly[...]` source (readonly param / field / return) is
        const even though it is not in `const_ref_params`: its element pointers
        lift as `const T*`, so the borrow local must declare them const.
        """
        inner = self.ctx.unwrap_copy(src)
        if isinstance(inner, TpyIfExpr):
            return (self._tuple_source_is_const(inner.then_expr)
                    or self._tuple_source_is_const(inner.else_expr))
        if self.ctx.is_const_storage_source(inner):
            return True
        st = self.ctx.analyzer.get_expr_type(inner)
        return isinstance(st, ReadonlyType)

    def gen_body(self, out: TextIO, body: list[TpyStmt],
                 params: list[tuple[str, TpyType]], return_type: TpyType,
                 func: TpyFunction, local_ns: Namespace,
                 indent_level: int = 1, is_method: bool = False,
                 record_type_param_bounds: dict[str, TpyType] | None = None,
                 const_ref_params: set[str] | None = None,
                 deep_const_borrow_params: set[str] | None = None,
                 owning_record_name: str | None = None,
                 return_cpp: str | None = None) -> None:
        """Generate the body of a function or method.

        Handles scope setup, body buffering, hoist-decl prepending, and cleanup.
        Shared by gen_function_def() and _gen_method().
        """
        # THIR dual-mode: an eligible function emits its body from THIR, with no
        # analyzer/scope setup. Byte-identical to the AST path for the slice.
        if self.ctx.thir_codegen:
            thir_fn = self.ctx.thir_functions.get(id(func))
            if thir_fn is not None:
                from ..thir.emit import (emit_thir_body, CtxCommentSink,
                                         CtxCounter, CtxTempSink)
                emit_thir_body(out, thir_fn, indent_level,
                               comments=CtxCommentSink(self.ctx),
                               temps=CtxTempSink(self.ctx),
                               with_counter=CtxCounter(self.ctx, "with_counter"),
                               try_counter=CtxCounter(self.ctx,
                                                      "try_except_counter"),
                               finally_guard_counter=CtxCounter(
                                   self.ctx, "finally_guard_counter"),
                               return_cpp=return_cpp)
                # The function-level trailing-comment walk runs for THIR
                # bodies too (this early return skips the AST tail's call).
                self.ctx.emit_block_trailing_comments(
                    out, body, INDENT * indent_level)
                return

        scan = self.setup_body_scope(
            params, return_type, func, local_ns,
            indent_level=indent_level, is_method=is_method,
            record_type_param_bounds=record_type_param_bounds,
            const_ref_params=const_ref_params,
            deep_const_borrow_params=deep_const_borrow_params,
            owning_record_name=owning_record_name,
            return_cpp=return_cpp,
        )

        # Emit mutable local copies for reassigned const-ref params (BigInt, str)
        # so internal reassignment doesn't change the function signature.
        self._reassigned_param_copies = []
        if scan:
            for pname, ptype in params:
                if pname in scan.reassigned and ptype.param_needs_copy_for_reassign():
                    self._reassigned_param_copies.append((pname, ptype))

        # @overload short-arity stub: emit the impl params that the stub
        # omitted as locals initialized to the impl's defaults.
        missing_locals = self.ctx.overload_missing_param_locals
        if missing_locals:
            from ..typesys import NoneType as _NoneType
            self.ctx.overload_missing_param_locals = []
            reassigned = scan.reassigned if scan else set()
            indent = self.ctx.indent()
            for pname, ptype, default_expr in missing_locals:
                # NoneType narrowing: accessing an Optional[T] value always
                # requires a guard (if x is not None). When narrowed to None
                # the guard folds to False, dead-branch elim strips the
                # value-access path, so the local is guaranteed unused.
                # Literal narrowing does NOT get this treatment -- the body
                # may use the param directly without any conditional.
                narrowed = self.ctx.overload_param_types.get(pname)
                if isinstance(narrowed, _NoneType) and pname not in reassigned:
                    continue
                cpp_default = default_to_cpp(self.ctx, default_expr, ptype)
                # default_to_cpp falls back to "0" for unrecognized exprs.
                # Use C++ value-initialization ({}) instead -- valid for any
                # default-constructible type (empty vector, 0 for ints, etc.).
                if cpp_default == "0" and not isinstance(default_expr, (TpyIntLiteral, TpyCall)):
                    cpp_default = "{}"
                # Use the storage type (to_cpp), not the param-passing type
                # (to_cpp_param_type) which may be a const reference.
                cpp_type = ptype.to_cpp()
                cpp_name = escape_cpp_name(pname)
                out.write(f"{indent}{cpp_type} {cpp_name} = {cpp_default};\n")
                self.ctx.declared_vars.add(pname)
                self.ctx.local_scope_names.add(pname)
                self.ctx.var_types[pname] = unwrap_ref_type(ptype)
                local_ns.bind_variable(pname, ptype)
                # Mirror the pointer-local / ptr-variant registration that
                # gen_body does for real params of the same shapes, so the
                # body's access-path codegen (-> vs ., variant extraction,
                # move semantics) treats the missing-param local correctly.
                # unwrap_send_sync: see through the transparent Send/Sync marker.
                actual = unwrap_readonly(unwrap_send_sync(ptype))
                if isinstance(actual, OptionalType) and actual.uses_pointer_repr():
                    self.ctx.pointer_locals.add(pname)
                    if isinstance(ptype, ReadonlyType):
                        self.ctx.const_indirect_locals.add(pname)
                elif self.ctx.is_ptr_variant_union(actual):
                    self.ctx.ptr_variant_locals.add(pname)
                    if isinstance(ptype, ReadonlyType):
                        self.ctx.const_indirect_locals.add(pname)

        self._gen_buffered_body(out, body)

        # Void @error_return functions need explicit success return to avoid UB
        if self.ctx.current_error_return and isinstance(return_type, VoidType):
            out.write(f"{self.ctx.indent()}return {{}};\n")

        # Skip trailing-comment scan when overload dead-branch elim resolved
        # the body's top-level if to True -- the emitted stmts come from a
        # then_body, not the TpyIf node, so scanning forward from the TpyIf's
        # line would pick up comments from inside the dead branches.
        if not self.ctx.overload_terminated:
            self.ctx.emit_block_trailing_comments(out, body, self.ctx.indent())

        if is_method:
            self.ctx.in_method = False
            self.ctx.current_method_record_type = None
        self.ctx.local_scope_names = set()
        self.ctx.indent_level = 0
        self.ctx.current_ns = None

    def gen_stmt(self, out: TextIO, stmt: TpyStmt) -> None:
        """Generate a statement."""
        indent = self.ctx.indent()

        # Track current line for order-aware import qualification in top-level context
        # (current_stmt_line > 0 means we're in top-level, set by gen_module_init)
        if self.ctx.current_stmt_line > 0 and hasattr(stmt, 'loc') and stmt.loc:
            self.ctx.current_stmt_line = stmt.loc.line

        # A multi-statement desugar (e.g. tuple-literal unpack) flags its
        # non-first statements so the shared source comment is emitted once.
        comment_loc = None if getattr(stmt, 'no_source_comment', False) else stmt.loc
        self.ctx.emit_inline_comments(out, comment_loc, indent)

        # Compound statements - delegate to handlers (they flush before their header)
        if isinstance(stmt, TpyIf):
            # Source comment is emitted inside _gen_if so that overload
            # dead-branch elim can suppress it when the `if` is resolved away.
            self._gen_if(out, stmt, indent)
        elif isinstance(stmt, TpyWhile):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_while(out, stmt, indent)
        elif isinstance(stmt, TpyForEach):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._emit_branch_decls(out, stmt, indent)
            self._gen_for_each(out, stmt, indent)
        elif isinstance(stmt, TpyAssert):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_assert(out, stmt, indent)
        elif isinstance(stmt, TpyTupleUnpack):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_tuple_unpack(out, stmt, indent)
        elif isinstance(stmt, TpyMatch):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self.match.gen_match(out, stmt, indent)
        elif isinstance(stmt, TpyTry):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._emit_branch_decls(out, stmt, indent)
            self._gen_try(out, stmt, indent)
        elif isinstance(stmt, TpyWith):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._emit_branch_decls(out, stmt, indent)
            self._gen_with(out, stmt, indent)
        elif isinstance(stmt, TpyNestedDef):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self.ctx.temps.flush(out, indent)
            self._gen_nested_def(out, stmt, indent)
        elif isinstance(stmt, TpyYield):
            # Yields never reach the linear statement walk: the simple-generator
            # peephole emits them directly, and the resumable frame emits them
            # as CFG Yield terminators (`gen_async._emit_generator_yield`). A
            # yield arriving here is an internal invariant break.
            raise CodeGenError("internal: yield reached the linear statement walk")
        else:
            # Simple statements - single flush point for all
            code = self._gen_simple_stmt(stmt, indent)
            if code is not None:
                self.ctx.emit_source_comment(out, comment_loc, indent)
                self.ctx.temps.flush(out, indent)
                out.write(code)
            # Assignment narrowing for union VarDecl
            if isinstance(stmt, TpyVarDecl):
                # Clear stale narrowing on any write to this variable
                self.ctx.assign_narrowed_types.pop(stmt.name, None)
                self.ctx.literal_facts.pop(stmt.name, None)
                if stmt.then_type_facts:
                    for var_name, narrowed_type in stmt.then_type_facts.items():
                        self.ctx.assign_narrowed_types[var_name] = narrowed_type

    def _gen_simple_stmt(self, stmt: TpyStmt, indent: str) -> str | None:
        """Generate code for simple statements. Returns code to write or None.

        Expression generation happens here (which may create temps).
        The caller handles flushing temps before writing the returned code.
        """
        if isinstance(stmt, TpyVarDecl):
            if stmt.init and self._get_error_return_fi(stmt.init):
                if self.ctx.try_except_label:
                    return self._gen_error_return_var_decl(stmt, indent)
                if self.ctx.current_error_return:
                    return self._gen_error_return_propagate_var_decl(stmt, indent)
                # Top-level: unwrap with panic on error
                return self._gen_error_return_unwrap_var_decl(stmt, indent)
            return self._gen_var_decl_code(stmt, indent)
        elif isinstance(stmt, TpyAssign):
            if self._get_error_return_fi(stmt.value):
                if self.ctx.try_except_label:
                    return self._gen_error_return_assign(stmt, indent)
                if self.ctx.current_error_return:
                    return self._gen_error_return_propagate_assign(stmt, indent)
                return self._gen_error_return_unwrap_assign(stmt, indent)
            return self._gen_assign_code(stmt, indent)
        elif isinstance(stmt, TpyAugAssign):
            return self._gen_aug_assign_code(stmt, indent)
        elif isinstance(stmt, TpyDelItem):
            return self._gen_del_item_code(stmt, indent)
        elif isinstance(stmt, TpyDelVar):
            return self._gen_del_var_code(stmt, indent)
        elif isinstance(stmt, TpyDelAttr):
            return self._gen_del_attr_code(stmt, indent)
        elif isinstance(stmt, TpyExprStmt):
            if isinstance(stmt.expr, TpyStrLiteral):
                return None  # Skip docstrings
            if isinstance(stmt.expr, TpyCall) and stmt.expr.compile_time_assert:
                return None  # assert_send/assert_sync: checked in sema, no emission
            if self._get_error_return_fi(stmt.expr):
                return self._gen_error_return_stmt_block(
                    self._gen_error_return_call(stmt.expr), indent)
            return f"{indent}{self.expressions.gen_expr(stmt.expr)};\n"
        elif isinstance(stmt, TpyReturn):
            if self.ctx.in_async_coro_body:
                return self._make_async_return(stmt, indent)
            if self.ctx.in_generator_resumable_body:
                # Generator on the resumable frame: bare return / end ->
                # StopIteration done, via the while/switch (no __done label).
                return self._make_generator_resumable_return(stmt, indent)
            if self.ctx.in_generator_finally_helper:
                # return inside a helper-based finally body: set the stop flag
                # and void-return; __next__() checks __finally_stop after the
                # helper call and emits StopIteration (Python: return in finally
                # suppresses any pending exception).
                return (f"{indent}this->__finally_stop = true;\n"
                        f"{indent}return;\n")
            if stmt.value:
                ret_type = self.ctx.current_return_type
                # The const twin of an auto_readonly accessor (implicit on
                # __getitem__/__deref__/__span__) declares
                # ReadonlyType(Optional[T]); the readonly projection only
                # affects the emitted signature's const-ness, not the
                # pointer-vs-storage repr -- classify the OPTIONAL arm below
                # on the wrapped type, else a pointer-repr Optional return
                # misses its `&(...)` lift. Scoped to Optional: other
                # readonly returns (e.g. readonly[tuple[...]]) keep the
                # wrapper, their arms consume its const projection.
                if (isinstance(ret_type, ReadonlyType)
                        and isinstance(ret_type.wrapped, OptionalType)):
                    ret_type = ret_type.wrapped
                if stmt.finally_deferred_capture:
                    if self.ctx.finally_stack:
                        deferred = self._gen_finally_deferred_return(
                            stmt, ret_type, indent)
                        if deferred is not None:
                            return deferred
                    else:
                        # Stamped but no active finally frame here: keep the
                        # retract invariant (stamped-and-not-deferred never
                        # leaves the restored move mark live on eager arms).
                        self._retract_deferred_return_mark(stmt)
                ret_value = stmt.value
                # In @overload specialization: validate return type and strip
                # wrong-target coercions. Sema coerced against the impl's union
                # return type, which may have picked the wrong union member.
                if (self.ctx.overload_param_types or self.ctx.literal_overload_facts) and stmt.value_type is not None:
                    compatible = self._check_overload_return_type(stmt, ret_type)
                    if not compatible:
                        if self.ctx.literal_overload_facts:
                            return None  # Dead code after literal branch elimination
                        from .context import CodeGenError
                        vt = stmt.value_type
                        if isinstance(vt, IntLiteralType):
                            vt = BIGINT
                        raise CodeGenError(
                            f"@overload return type mismatch: returning '{vt}' "
                            f"but this overload declares '-> {ret_type}'",
                            loc=stmt.loc,
                        )
                    ret_value = self._strip_wrong_overload_coerce(ret_value, ret_type)
                # Property getter with pointer-repr return: return field directly
                # (C++ return is std::optional<T>& / std::variant<A,B>&, not T* / variant<T*>)
                if (self.ctx.in_property_getter
                        and ((isinstance(ret_type, OptionalType) and ret_type.uses_pointer_repr())
                             or self.ctx.is_ptr_variant_union(ret_type))):
                    ret_expr = self.expressions.gen_expr(ret_value)
                    return self._make_return(indent, ret_expr)
                if isinstance(ret_type, OptionalType):
                    if not ret_type.uses_pointer_repr():
                        if isinstance(ret_value, TpyNoneLiteral):
                            return self._make_return(indent, "std::nullopt")
                        # A pointer-repr Optional local (`T*` + slot) returned as a
                        # storage Optional (`Own[T] | None`): wrap the slot into
                        # std::optional<T>, moving its value at last use rather than
                        # copying (correct for @nocopy; ownership transfers out).
                        if self.ctx.is_indirect_name(ret_value):
                            value = self.expressions.gen_expr(ret_value)
                            helper = ("ptr_to_optional_move"
                                      if self.expressions._is_last_use_movable(ret_value)
                                      else "ptr_to_optional")
                            return self._make_return(indent, f"::tpy::{helper}({value})")
                        ret_expr = self.expressions.gen_expr_deref(ret_value, ret_type)
                        return self._make_return(indent, ret_expr)
                    ret_expr = self.expressions._optional_pointer_form_value(ret_value, ret_type)
                    return self._make_return(indent, ret_expr)
                # Recursive union wrapper struct: `return None` constructs the
                # monostate variant (NoneType is one of the wrapper's members).
                if isinstance(ret_value, TpyNoneLiteral):
                    if unwrap_qualifiers(ret_type).needs_wrapper():
                        return self._make_return(indent, "std::monostate{}")
                # Pointer-variant union return: return variant<T*...>
                if self.ctx.is_ptr_variant_union(ret_type):
                    if isinstance(ret_value, TpyNoneLiteral):
                        return self._make_return(indent, "std::monostate{}")
                    # Check if source is a ptr-variant AND not currently narrowed.
                    # Narrowed ptr-variant vars resolve to Dog& (via std::get), so
                    # they need &() to produce Dog* for the return variant.
                    is_narrowed = (isinstance(ret_value, TpyName)
                                   and ret_value.name in self.ctx.narrowed_vars)
                    if self.ctx.is_ptr_variant_source(ret_value) and not is_narrowed:
                        ret_expr = self.expressions.gen_expr(ret_value, ret_type)
                        return self._make_return(indent, ret_expr)
                    # VALUE_VARIANT source (Own[A|B] param) returning into a
                    # pointer-variant return: lift via to_ptr_variant. The
                    # Own param's C++ shape is value-variant; without the lift
                    # codegen would take the address of the storage and produce
                    # variant<A,B>* rather than variant<A*,B*>.
                    if (isinstance(ret_value, TpyName) and not is_narrowed
                            and self.ctx.needs_to_ptr_variant_lift(ret_value.name)):
                        ret_expr = self.expressions.gen_expr(ret_value, ret_type)
                        return self._make_return(
                            indent, f"::tpy::to_ptr_variant({ret_expr})")
                    # Narrowed variable or concrete lvalue: take address for implicit
                    # variant<T*...> construction
                    ret_expr = self.expressions.gen_expr(ret_value, ret_type)
                    return self._make_return(indent, f"&({ret_expr})")
                # When returning an error_return call from a matching
                # @error_return function, pass the std::expected through
                # directly -- no unwrap+rewrap needed.
                if self.ctx.current_error_return and self._get_error_return_fi(ret_value):
                    ret_expr = self._gen_error_return_call(ret_value)
                    return self._make_return(indent, ret_expr)
                # Own[abstract @dynamic P] return: mirror the function-call
                # arg path so `return Parrot(...)` lands in a `unique_ptr<Pet>`
                # slot via `std::make_unique<Adapter<P, Concrete>>(...)`.
                # `_is_dyn_own_wrap_needed` returns False for the forward case
                # so named locals/params keep C++ implicit-move on return.
                if (isinstance(ret_type, OwnType)
                        and self.expressions._is_dyn_own_wrap_needed(ret_value, ret_type)):
                    dyn_own_ret = self.expressions._gen_dynamic_protocol_arg(ret_value, ret_type)
                    if dyn_own_ret is not None:
                        return self._make_return(indent, dyn_own_ret)
                ret_expr = self.expressions.gen_expr(
                    ret_value, ret_type)
                # OPTIONAL_STORAGE source names (Own[Opt[P_ref]] params)
                # are rendered as std::optional<P>, not P*. When the return
                # type is the same Own[Optional[P_ref]] shape, std::move the
                # whole optional rather than dereffing -- (*x) is UB on a
                # nullopt and would also lose the None case.
                if (isinstance(ret_value, TpyName)
                        and self.ctx.needs_optional_to_ptr_lift(ret_value.name)
                        and is_own_pointer_repr_optional(ret_type)):
                    ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                # Dereference pointer-locals/pointer-globals on return (T* -> T&)
                elif self.ctx.is_indirect_name(ret_value):
                    ret_expr = f"(*{ret_expr})"
                    ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                # Unwrap value-optional expressions when return type is non-Optional.
                # The sema narrows the type inside `if x is not None:` branches,
                # but the C++ variable/field is still std::optional<T>.
                elif (
                    not isinstance(ret_type, OptionalType)
                    and self._is_value_optional_expr(ret_value)
                ):
                    analyzed_type = self.ctx.get_expr_type(ret_value)
                    if isinstance(analyzed_type, OptionalType):
                        ret_expr = f"::tpy::deref_optional_check({ret_expr})"
                        ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                    else:
                        ret_expr = f"(*{ret_expr})"
                        # Narrowed Optional[str]/Optional[bytes] param: the deref
                        # yields a borrow view (string_view / span) copied into
                        # the owned return slot. The copy IS the ownership
                        # transfer, so skip the last-use move -- std::move on a
                        # trivially-copyable view is a no-op.
                        if self.expressions._view_owned_copy_family(
                                ret_value, ret_type) is not None:
                            ret_expr = self.expressions._view_source_to_owned(
                                ret_value, ret_type, ret_expr)
                        else:
                            ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                else:
                    ret_expr = self._wrap_view_to_storage(
                        ret_value, ret_type, ret_expr)
                # Borrow-form tuple local returned as a per-element-Own storage
                # tuple: deref-COPY each pointee into the owned return slot. A
                # move would be sound only for a dying-local pointee; the
                # borrow may equally be param-rooted (`pair = (b, 0)` for a
                # borrowed param `b`), and codegen cannot tell the two apart
                # here -- a move would silently gut the caller's object, so
                # the dying-local case pays a copy where a move would do.
                ret_tuple = (unwrap_qualifiers(ret_type)
                             if ret_type is not None else None)
                if (isinstance(ret_tuple, TupleType)
                        and any(isinstance(et, OwnType)
                                for et in ret_tuple.element_types)):
                    src = self.ctx.unwrap_copy(ret_value)
                    src_type = self.ctx.get_expr_type(src)
                    src_tuple = (unwrap_qualifiers(src_type)
                                 if src_type is not None else None)
                    if (isinstance(src, TpyName)
                            and isinstance(src_tuple, TupleType)
                            and src_tuple.has_pointer_repr_element()
                            and not self.ctx.is_storage_form_source(src)):
                        ret_expr = (
                            f"::tpy::tuple_to_storage"
                            f"<{self.types.tuple_storage_cpp(ret_tuple)}>({ret_expr})")
                # Borrow-form tuple return read from a storage location
                # (field/subscript): lift element addresses into that storage.
                # Sema rejects non-durable roots, so the pointers outlive the
                # call. Self-gating: Own-element tuples are not pointer-repr.
                ret_expr = self._maybe_wrap_tuple_to_pointer(
                    ret_expr, ret_type, self.ctx.unwrap_copy(ret_value))
                # Consuming method: move self fields on return (this->field is lvalue)
                if (self.ctx.in_consuming_method
                        and isinstance(ret_value, TpyFieldAccess)
                        and isinstance(ret_value.obj, TpyName)
                        and ret_value.obj.name == "self"):
                    ret_expr = f"std::move({ret_expr})"
                return self._make_return(indent, ret_expr)
            if self.ctx.current_error_return:
                return self._make_return(indent, "{}")
            return self._make_return(indent)
        elif isinstance(stmt, TpyBreak):
            return self._make_break_continue(indent, is_break=True)
        elif isinstance(stmt, TpyContinue):
            return self._make_break_continue(indent, is_break=False)
        elif isinstance(stmt, TpyPassStmt):
            return ""  # No-op - emit nothing
        elif isinstance(stmt, TpyGlobal):
            return ""  # No C++ output -- just a sema directive
        elif isinstance(stmt, TpyNonlocal):
            return ""  # No C++ output -- capture mode handles it
        elif isinstance(stmt, TpyRaise):
            return self._gen_raise(stmt, indent)
        elif isinstance(stmt, TpyImport):
            registry = self.ctx.analyzer.registry
            module_info = registry.get_module(stmt.module_name)
            has_init = module_info is None or module_info.has_runtime_init
            result = ""
            if (module_info is not None
                    and module_info.is_native_module
                    and stmt.module_name in self.ctx.user_module_imports):
                # Native facades have no __tpy_init() of their own; chain into
                # the non-native source modules of any re-exported variables.
                for reached in self._native_facade_init_targets(stmt.module_name):
                    if reached in self.ctx.emitted_tpy_inits:
                        continue
                    result += f"{indent}{qualified_cpp_name(reached, '__tpy_init')}();\n"
                    self.ctx.emitted_tpy_inits.add(reached)
            if stmt.module_name in self.ctx.user_module_imports and has_init:
                # For dotted imports, emit parent package inits first (Python semantics)
                # e.g., "mypackage.utils" -> init mypackage first, then mypackage.utils
                parts = stmt.module_name.split('.')
                for i in range(1, len(parts)):
                    parent_pkg = '.'.join(parts[:i])
                    if parent_pkg == self.ctx.module_name:
                        continue  # don't self-init
                    if parent_pkg not in self.ctx.all_user_modules:
                        continue
                    if parent_pkg in self.ctx.emitted_tpy_inits:
                        continue
                    parent_info = registry.get_module(parent_pkg)
                    if parent_info is not None and not parent_info.has_runtime_init:
                        # Native/builtin packages have no __tpy_init symbol;
                        # mark visited so sibling submodules don't retry.
                        self.ctx.emitted_tpy_inits.add(parent_pkg)
                        continue
                    result += f"{indent}{qualified_cpp_name(parent_pkg, '__tpy_init')}();\n"
                    self.ctx.emitted_tpy_inits.add(parent_pkg)
                # Then init the submodule itself (skip self-init)
                if stmt.module_name != self.ctx.module_name and stmt.module_name not in self.ctx.emitted_tpy_inits:
                    result += f"{indent}{qualified_cpp_name(stmt.module_name, '__tpy_init')}();\n"
                    self.ctx.emitted_tpy_inits.add(stmt.module_name)
            return result
        return None

    def _native_facade_init_targets(self, native_module: str) -> list[str]:
        """Defining modules of variables re-exported by ``native_module``.

        Records, functions, and protocols re-exported by the facade are pure
        declarations; only re-exported variables involve runtime
        construction the consumer must trigger.

        The attribute table's VARIABLE bindings carry the chain-flattened
        ultimate definer in `binding.defining_module`, so a single pass
        over the facade's table yields the set of init targets.
        """
        registry = self.ctx.analyzer.registry
        info = registry.get_module(native_module)
        if info is None or info.module_attributes is None:
            return []
        order: list[str] = []
        seen: set[str] = set()
        for cell in info.module_attributes.values():
            bd = cell.binding
            if bd.kind != SymbolKind.VARIABLE or bd.defining_module is None:
                continue
            ult_mod = bd.defining_module
            if ult_mod in seen:
                continue
            ult_info = registry.get_module(ult_mod)
            if ult_info is None or not ult_info.has_runtime_init:
                continue
            seen.add(ult_mod)
            order.append(ult_mod)
        return order

    def _is_plain_nonvalue(self, t: TpyType) -> bool:
        # Recursive-union wrappers are reference types like records: a local
        # bound from a reference source (`g = h.get()`) binds `Tree<T>&`, a
        # fresh value (`t = [1, 2]`) stays by value -- the is_rvalue_source
        # rule in _needs_indirection draws that line (mirrors list/dict/record).
        return self.ctx.is_plain_nonvalue(t)

    def _needs_indirection(self, target_type: TpyType | None, name: str,
                            init: TpyExpr | None) -> bool:
        """Check if a variable needs indirection (T* pointer-local or T& reference).

        Returns True when the variable is reassigned later or initialized from
        a non-rvalue (sharing/aliasing). The caller distinguishes T* vs T&.
        Optional non-value types always need indirection (they are nullable pointers).
        """
        if target_type is None:
            return False
        # Optional[T] for non-value T is always a pointer-local
        if isinstance(target_type, OptionalType) and target_type.uses_pointer_repr():
            return True
        if not self._is_plain_nonvalue(target_type):
            return False
        if name in self.ctx.move_through_vars:
            return False
        if name in self.ctx.reassigned_vars:
            return True
        if name in self.ctx.hoisted_vars:
            return True
        if init is not None and not self.ctx.is_rvalue_source(init):
            return True
        return False

    def _is_const_indirect(self, target_type: TpyType | None, init: TpyExpr | None,
                           stmt: 'TpyVarDecl | None' = None) -> bool:
        """Check if a local variable should use const indirection (const T* or const T&).

        Detects when the variable is derived from a ReadonlyType source:
        - Optional inner is ReadonlyType (None-seeded from readonly param)
        - Init expression has ReadonlyType in sema (direct alias of readonly param)
        - Sema var_types holds ReadonlyType for annotated locals that are later
          reassigned from a readonly source
        """
        if isinstance(target_type, OptionalType) and isinstance(target_type.inner, ReadonlyType):
            return True
        if init is not None:
            sema_type = self.ctx.analyzer.get_expr_type(init)
            if isinstance(sema_type, ReadonlyType):
                return True
        # For annotated Optional locals, sema var_types may hold
        # OptionalType(ReadonlyType(T)) even when stmt.type is plain Optional[T].
        if stmt is not None:
            sema_var_type = self.ctx.analyzer.var_types.get(id(stmt))
            if (isinstance(sema_var_type, OptionalType)
                    and isinstance(sema_var_type.inner, ReadonlyType)):
                return True
        # Readonly method call returns const T& -> variable needs const indirection.
        # (TypeParamRef returns are handled separately via val_or_cref_t in _gen_local_var_decl.)
        if isinstance(init, TpyMethodCall):
            fi = init.resolved_function_info
            if fi is not None and fi.is_readonly and self.ctx._call_returns_cpp_ref(fi):
                return True
        # Operator dispatch mirrors the method-call arm: a readonly dunder's
        # borrow return is const-projected at emit, so the alias binds const.
        if isinstance(init, TpyBinOp) and init.resolved_binop is not None:
            fi = init.resolved_binop.method
            if fi.is_readonly and self.ctx._call_returns_cpp_ref(fi):
                return True
        if isinstance(init, TpyUnaryOp) and init.resolved_unaryop is not None:
            fi = init.resolved_unaryop.method
            if fi.is_readonly and self.ctx._call_returns_cpp_ref(fi):
                return True
        # An alias of a const-inferred source must also bind const, else a
        # mutable reference/pointer would be taken from a const source. Sound
        # because a const source implies the alias is never written through --
        # a write would have marked the source mutated via the borrow chain.
        # Covers the pointer-local (Optional) branch, which the T&-branch
        # call-site propagation does not reach.
        if isinstance(init, TpyName) and (
                init.name in self.ctx.const_ref_params
                or init.name in self.ctx.const_indirect_locals
                or init.name in self.ctx.deep_const_borrow_params):
            return True
        return False

    def _is_dynamic_protocol_type(self, target_type: TpyType | None) -> bool:
        """Check if the type is a @dynamic protocol (needs adapter slot codegen)."""
        if target_type is None or not is_protocol_type(target_type):
            return False
        protocol_info = protocol_info_of(target_type)
        return protocol_info is not None and protocol_info.is_dynamic

    def _get_nullproto_constexpr_guards(self, condition: TpyExpr) -> list[str]:
        """Get param names that need if constexpr guards for nullable protocol params.

        When a nullable static protocol param is narrowed (e.g. `if items is not None:`),
        the narrowing body must be wrapped in `if constexpr (!std::same_as<T_X, std::nullptr_t>)`
        to prevent instantiation of protocol operations on nullptr_t.
        """
        guards = []
        match = match_is_none(condition)
        if match is not None:
            var_name, is_not_none = match
            if is_not_none and '.' not in var_name:
                declared = self.ctx.current_func_params.get(var_name)
                if declared and self.protocols.is_static_protocol_param(declared):
                    infos = self.protocols.get_all_protocol_params([(var_name, declared)])
                    if infos and infos[0].has_none:
                        guards.append(var_name)
        return guards

    def _is_protocol_isinstance_condition(self, condition: 'TpyExpr') -> bool:
        """Check if condition is isinstance(x, Protocol) requiring if constexpr."""
        if isinstance(condition, TpyCall) and condition.isinstance_is_protocol:
            return True
        if isinstance(condition, TpyUnaryOp) and condition.op == "!":
            return self._is_protocol_isinstance_condition(condition.operand)
        return False

    def _gen_concrete_coro_write(self, cpp_name: str, init: 'TpyExpr',
                                  indent: str) -> str:
        """Write into a concrete coroutine handle's `std::optional<coro>`
        slot (frame field or already-declared local). Always emplace:
        a call source constructs the fresh frame in place; a name source
        move-CONSTRUCTS from the source's payload -- optional's move-
        ASSIGN is deleted outright when the frame has reference members
        (method coroutines hold `Record& __self`), while its move
        constructor is fine. emplace destroys any prior payload first.
        Sema guarantees a name source is engaged (consumed handles are
        rejected at the read)."""
        init_inner = self.ctx.unwrap_copy(init)
        if isinstance(init_inner, TpyName):
            src = self.expressions.gen_expr(init)
            if src == cpp_name:
                # Self-write (`c = c`): a no-op in Python; emplace-from-
                # self would destroy the payload mid-construction.
                return ""
            return (f"{indent}{cpp_name}.emplace(std::move(*{src}));\n"
                    f"{indent}{src}.reset();\n")
        init_expr = self.expressions.gen_expr(init)
        return f"{indent}{cpp_name}.emplace({init_expr});\n"

    def _gen_dynamic_protocol_init(self, name: str, target_type: NominalType,
                                    init: 'TpyExpr', indent: str) -> str:
        """Generate slot + pointer-local for a @dynamic protocol variable.

        If the concrete type directly inherits the protocol base, emit a plain
        concrete slot (no adapter). Otherwise use adapter wrapping.
        If the init is already an erased protocol variable, just copy the pointer.
        Uses brace init to avoid C++ most-vexing-parse with constructor calls.
        """
        concrete_type = self.ctx.get_expr_type(init)
        base_type = self.protocols.get_dynamic_base_name(target_type)

        if is_protocol_type(concrete_type):
            # Already erased -- copy the pointer
            init_expr = self.expressions.gen_expr_deref(init, concrete_type)
            return f"{indent}{base_type}* {name} = &{init_expr};\n"

        concrete_cpp = self.types.type_to_cpp(concrete_type)
        init_slot = self.ctx.slots.next_slot()
        init_expr = self.expressions.gen_expr(init, concrete_type)

        if self.protocols.directly_implements_dynamic(concrete_type, target_type):
            # Direct inheritance -- plain concrete slot, implicit upcast
            slot_type = concrete_cpp
        else:
            # Structural conformance -- adapter wrapping
            slot_type = self.protocols.get_dynamic_adapter_type(target_type, concrete_cpp)

        return (f"{indent}{slot_type} {init_slot}{{{init_expr}}};\n"
                f"{indent}{base_type}* {name} = &{init_slot};\n")

    def _gen_dynamic_protocol_rebind(self, name: str, target_type: NominalType,
                                      init: 'TpyExpr', indent: str) -> str:
        """Generate slot rebind for a @dynamic protocol variable reassignment.

        Slots are hoisted to function scope via pending_hoist_decls so they
        survive block scopes (if/else branches, loops).
        """
        concrete_type = self.ctx.get_expr_type(init)

        if is_protocol_type(concrete_type):
            # Already erased -- rebind pointer to same object
            init_expr = self.expressions.gen_expr_deref(init, concrete_type)
            return f"{indent}{name} = &{init_expr};\n"

        concrete_cpp = self.types.type_to_cpp(concrete_type)
        rebind_slot = self.ctx.slots.next_slot()
        init_expr = self.expressions.gen_expr(init, concrete_type)

        if self.protocols.directly_implements_dynamic(concrete_type, target_type):
            slot_type = concrete_cpp
        else:
            slot_type = self.protocols.get_dynamic_adapter_type(target_type, concrete_cpp)

        # Hoist slot to function scope (survives block scopes).
        # Global scope (__tpy_init) needs 'static' so slots outlive the function.
        static_kw = "static " if self.ctx.slots.global_scope else ""
        self.ctx.pending_hoist_decls.append(
            f"{static_kw}std::optional<{slot_type}> {rebind_slot};\n"
        )
        return (f"{indent}{rebind_slot}.emplace({init_expr});\n"
                f"{indent}{name} = &*{rebind_slot};\n")

    def _resolve_literal_view_storage(self, name: str, var_type: TpyType) -> TpyType:
        """Substitute LiteralType[str/bytes] with the view-inferred storage form.

        Sema preserves `stmt.type = LiteralType` so OOS / dispatch / narrowing
        see the annotation; codegen needs the str/bytes view-vs-owned form
        chosen by view inference, looked up by variable name.
        """
        if not isinstance(var_type, LiteralType):
            return var_type
        family = view_family_for_type(var_type)
        if family is None:
            return var_type
        var_id = self.ctx.analyzer.ctx.view_var_map(family).get(name)
        if var_id is None:
            return var_type
        return self.types._resolve_view_storage(family, var_id)

    def _resolve_target_type(self, stmt: TpyVarDecl) -> TpyType | None:
        """Resolve the target type for a variable declaration."""
        target_type = resolve_stmt_binding_type(
            stmt,
            self.ctx.analyzer,
            include_global_binding=(self.ctx.current_ns is self.ctx.analyzer.global_ns),
        )
        if target_type is None and stmt.init:
            target_type = self.ctx.analyzer.get_expr_type(stmt.init)
        if target_type is not None:
            # Strip Ref and ReadonlyType -- C++ reference semantics are
            # handled by codegen binding (T& / auto&), not by the type itself.
            # Send/Sync markers (canonically outermost) have no C++ shape.
            target_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target_type)))
            # Keep Own[dyn P]: the owned-erased local IS unique_ptr<P>;
            # stripping it would route the decl to the borrow-form
            # pointer-local path (dangling for an owned rvalue init).
            if isinstance(target_type, OwnType) and not is_dyn_protocol(
                    unwrap_readonly(target_type.wrapped)):
                target_type = target_type.wrapped
            target_type = resolve_int_literals(target_type, self.ctx.analyzer.ctx.default_int_for_literal)
            if isinstance(target_type, FloatLiteralType):
                target_type = FLOAT
            resolved = self._resolve_pending_container(target_type)
            if resolved is not None:
                target_type = resolved
            elif isinstance(target_type, PendingViewType):
                target_type = self.types._resolve_pending_view(target_type)
            # A reassigned per-element-Own tuple local (including the nullable
            # `tuple[..., Own[T]] | None` form) takes the unified borrow shape
            # so an alias rebind aliases the source instead of copying. Sema
            # records this on inferred decls; an annotated decl reaches codegen
            # with the raw `stmt.type`, so re-apply the collapse here.
            if stmt.name in self.ctx.reassigned_vars:
                target_type = collapse_tuple_own_elements(target_type)
        return target_type

    def _resolve_pending_container(self, typ: TpyType) -> TpyType | None:
        """Resolve a pending container via the shared unified lookup (see
        `types.resolve_pending_container`)."""
        return resolve_pending_container(typ, self.ctx.analyzer)

    def _normalize_decl_type_for_cpp(self, var_type: TpyType) -> TpyType:
        """Normalize declaration type before C++ emission."""
        var_type = unwrap_ref_type(var_type)
        resolve_lit = self.ctx.analyzer.ctx.default_int_for_literal
        resolved = self._resolve_pending_container(var_type)
        if resolved is not None:
            var_type = resolved
        elif isinstance(var_type, PendingListType):
            # Fallback for unresolved list: resolve IntLiteralType in element
            elem = var_type.element_type
            if isinstance(elem, IntLiteralType):
                var_type = make_list(resolve_lit(elem))
        elif isinstance(var_type, PendingViewType):
            var_type = self.types._resolve_pending_view(var_type)
        # Resolve IntLiteralType in all composite types (tuples, arrays, lists)
        var_type = resolve_int_literals(var_type, resolve_lit)
        # Resolve FloatLiteralType to float64 (same as sema: float literals default to double)
        if isinstance(var_type, FloatLiteralType):
            var_type = FLOAT
        # Optional non-value types use inner type (pointer-local adds T*)
        if isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
            var_type = var_type.inner
        return var_type

    def _cpp_decl_type(self, var_type: TpyType) -> str:
        """Return C++ declaration type name for a normalized semantic type."""
        normalized = self._normalize_decl_type_for_cpp(var_type)
        if self.ctx.contains_protocol_type(normalized):
            return "auto"
        if isinstance(normalized, TupleType) and normalized.has_ref_elements():
            return "auto"
        return self.types.type_to_cpp(normalized)

    def _resolve_cpp_type(self, stmt: TpyVarDecl) -> str:
        """Resolve the C++ type string for a variable declaration."""
        if stmt.type:
            return self._cpp_decl_type(self._resolve_literal_view_storage(stmt.name, stmt.type))
        elif stmt.init:
            resolved_type = resolve_stmt_binding_type(
                stmt,
                self.ctx.analyzer,
                include_global_binding=(self.ctx.current_ns is self.ctx.analyzer.global_ns),
            )
            if resolved_type is None or isinstance(resolved_type, (*PENDING_CONTAINER_TYPES, PendingViewType)):
                resolved_type = self.ctx.get_expr_type(stmt.init)
            if resolved_type is None:
                raise CodeGenError(
                    f"Could not infer type for variable '{stmt.name}'", loc=stmt.loc
                )
            return self._cpp_decl_type(resolved_type)
        raise CodeGenError(f"Variable '{stmt.name}' has no type annotation and no initializer", loc=stmt.loc)

    # --- Rvalue slot helpers (shared by init and rebind) ---

    @staticmethod
    def _ptr_from_rvalue_slot(slot: str, init_expr: str, is_opt_field: bool,
                              is_optional_slot: bool = True) -> str:
        """Assign rvalue into pre-declared slot and derive pointer expression."""
        if is_opt_field:
            return f"::tpy::optional_to_ptr({slot} = {init_expr})"
        if is_optional_slot:
            return f"&*({slot} = {init_expr})"
        return f"&({slot} = {init_expr})"

    @staticmethod
    def _ptr_from_local_slot(slot: str, is_opt_field: bool) -> str:
        """Derive pointer from an inline-declared slot."""
        if is_opt_field:
            return f"::tpy::optional_to_ptr({slot})"
        return f"&{slot}"

    @staticmethod
    def _slot_decl_type(cpp_type: str, is_opt_field: bool) -> str:
        """C++ type for a rvalue materialization slot."""
        return f"std::optional<{cpp_type}>" if is_opt_field else cpp_type

    @staticmethod
    def _reject_polymorphic_rvalue_into_optional_local(
            name: str, target_type: 'OptionalType', sub: 'NominalType', loc) -> None:
        """Raise a clean error for rvalue construction of a polymorphic
        subclass into a local Optional[Polymorphic] slot that would slice.

        Fires when the slot is shared across rebinds (init-with-rebind or
        rebind site) -- the shared `std::optional<Base>` storage can't
        preserve dynamic type per assignment. The init-only case is handled
        without rejection by widening the slot to the rvalue's type.
        """
        raise CodeGenError(
            f"rvalue construction of '{sub.name}' into local "
            f"'{name}: Optional[{target_type.inner.name}]' with rvalue rebind "
            f"is not yet supported; pass the value directly as an argument "
            f"or assign to a typed local of type '{sub.name}'.",
            loc=loc
        )

    def _gen_pointer_local_init(self, name: str, cpp_type: str, init: 'TpyExpr',
                                target_type: TpyType | None, indent: str) -> str:
        """Generate pointer-local initialization code.

        Classifies the source expression:
        - None literal -> nullptr
        - OptionalType source (function returning T*) -> direct pointer copy
        - rvalue -> new slot + take address
        - pointer-local name -> pointer copy
        - lvalue ref (param, subscript, field) -> take address

        For vars with future rvalue rebinds, a separate rebind slot is
        pre-declared so aliases to the init value aren't overwritten.
        """
        from ..parse import TpyName as _TpyName
        name = escape_cpp_name(name)
        const_pfx = "const " if name in self.ctx.const_indirect_locals else ""

        # None literal -> nullptr (Optional/Ptr) or monostate slot (Union)
        if isinstance(init, TpyNoneLiteral):
            # Union pointer-locals: allocate slot with std::monostate{}
            if isinstance(target_type, UnionType):
                init_expr = "std::monostate{}"
                static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
                slot_opt_cpp = f"std::optional<{cpp_type}>"
                init_slot = self.ctx.slots.next_slot()
                is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
                if is_hoisted:
                    self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {init_slot};\n")
                    if name in self.ctx.rvalue_reassigned_vars:
                        rebind_slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = rebind_slot
                        self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {rebind_slot};\n")
                    else:
                        self.ctx.rebind_slots[name] = init_slot
                    return f"{indent}{const_pfx}{cpp_type}* {name} = &({init_slot}.emplace({init_expr}));\n"
                if name in self.ctx.rvalue_reassigned_vars:
                    rebind_slot = self.ctx.slots.next_slot()
                    self.ctx.rebind_slots[name] = rebind_slot
                    return (f"{indent}{static_kw}{cpp_type} {init_slot} = {init_expr};\n"
                            f"{indent}{static_kw}{slot_opt_cpp} {rebind_slot};\n"
                            f"{indent}{const_pfx}{cpp_type}* {name} = &{init_slot};\n")
                self.ctx.rebind_slots[name] = init_slot
                return (f"{indent}{static_kw}{cpp_type} {init_slot} = {init_expr};\n"
                        f"{indent}{const_pfx}{cpp_type}* {name} = &{init_slot};\n")
            # Pre-declare rebind slot if future rvalue rebinds need it
            rebind_decl = ""
            if name in self.ctx.rvalue_reassigned_vars:
                static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
                slot_opt_cpp = f"std::optional<{cpp_type}>"
                slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[name] = slot
                if name in self.ctx.hoisted_vars:
                    self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {slot};\n")
                else:
                    rebind_decl = f"{indent}{static_kw}{slot_opt_cpp} {slot};\n"
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = nullptr;\n"

        # OPTIONAL_STORAGE source: an `Own[Opt[T_ref]]` storage-form
        # `optional<T>` value consumed as `T*`. Two source shapes share
        # the same `optional_to_ptr` lift but live at different lifetime
        # tiers:
        #   * rvalue call (callee returns `Own[Opt[T_ref]]`) -- the
        #     returned `optional<P>` has no other home, materialize a
        #     slot and lift the slot. Rebinds reuse `rebind_slots[name]`.
        #   * lvalue name (`Own[Opt[T_ref]]` param in `optional_locals`)
        #     -- the param itself is the storage; pure lift, no slot.
        # Dispatched structurally on AST shape so the param-source case
        # cannot route through the slot path (which would emit a
        # redundant `std::optional<P> __slot = x;` materialization that
        # regressed `cases/auto_move/scalar_own_optional` on a prior
        # attempt that gated both tiers on a single polymorphic predicate).
        # `callee_returns_own_ptr_optional` is already AST-aware (returns
        # False for non-call nodes), so no outer isinstance pre-guard.
        is_call_src = self.ctx.callee_returns_own_ptr_optional(init)
        is_name_src = (isinstance(init, TpyName)
                       and self.ctx.needs_optional_to_ptr_lift(init.name))
        if is_call_src or is_name_src:
            init_expr = self.expressions.gen_expr(init, target_type)
            if is_call_src:
                static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[name] = slot
                slot_type = self._slot_decl_type(cpp_type, is_opt_field=True)
                deref = self._ptr_from_local_slot(slot, is_opt_field=True)
                return (f"{indent}{static_kw}{slot_type} {slot} = {init_expr};\n"
                        f"{indent}{const_pfx}{cpp_type}* {name} = {deref};\n")
            return f"{indent}{const_pfx}{cpp_type}* {name} = ::tpy::optional_to_ptr({init_expr});\n"

        init_type = self.ctx.get_expr_type(init)
        is_opt_field = (isinstance(init_type, OptionalType)
                        and init_type.uses_pointer_repr()
                        and isinstance(init, TpyFieldAccess))
        if isinstance(init_type, OptionalType) and init_type.uses_pointer_repr():
            # Storage-form Optional source (field, container subscript,
            # storage_form_optional_locals): lvalue lift via optional_to_ptr.
            # rvalue cases (e.g. rvalue field-access on a moved-from object)
            # fall through to the generic rvalue-slot path below.
            if (self.ctx.is_storage_form_optional_source(init)
                    and not self.ctx.is_rvalue_source(init)):
                # In const methods, field access yields const ref; propagate const
                # to the narrowed pointer so downstream dereferences are also const.
                # Same for loop-var / comp-unpack-var bound from a const-bound
                # storage source -- the iteration yields `const optional<P>&`,
                # so `optional_to_ptr` returns `const P*`.
                if not const_pfx and (
                        (isinstance(init, TpyFieldAccess)
                            and self.ctx.is_const_union_source(init))
                        or (isinstance(init, TpyName)
                            and init.name in self.ctx.const_storage_form_optional_locals)):
                    const_pfx = "const "
                    self.ctx.const_indirect_locals.add(name)
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{const_pfx}{cpp_type}* {name} = ::tpy::optional_to_ptr({init_expr});\n"
            # Value-emit rvalues reach this branch because sema annotates
            # them with the Optional target type, but their gen_expr emits
            # a value -- the direct-pointer assignment below would produce
            # `T* x = T-val`. Fall through to the rvalue-slot path.
            if not self.ctx.is_value_emit_rvalue(init):
                # A mapping accessor (`d.get(k)`) borrows into its receiver, so
                # its `V*` return is `const V*` when the receiver is const --
                # the local decl must match (C++ overload resolution already
                # picks the `const` dict_get overload on a const receiver).
                if (not const_pfx and isinstance(init, TpyMethodCall)
                        and init.method == "get"
                        and is_dict(unwrap_readonly(self.ctx.get_expr_type(init.obj)))
                        and self.ctx.is_const_union_source(init.obj)):
                    const_pfx = "const "
                    self.ctx.const_indirect_locals.add(name)
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{const_pfx}{cpp_type}* {name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        # Hoisted decls go to function scope -- use global_scope flag from slot state
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = "auto" if cpp_type == "auto" else f"std::optional<{cpp_type}>"
        target = f"{const_pfx}{cpp_type}* {name}"
        if self.ctx.is_rvalue_source(init):
            # Polymorphic-Optional local: typing the slot at the parent
            # cpp_type would slice the rvalue's dynamic type. Use the rvalue's
            # actual class so &slot upcasts to const Base* implicitly. The
            # shared rebind slot can't be retyped per rvalue, so the rebind
            # case is rejected at sema-error tier (see TODO.md for the
            # heap-allocated-slot follow-up).
            slot_cpp_type = cpp_type
            sub = polymorphic_subclass_into_optional(
                target_type, self.ctx.get_expr_type(init),
                self.ctx.analyzer.registry)
            if sub is not None:
                if name in self.ctx.rvalue_reassigned_vars:
                    self._reject_polymorphic_rvalue_into_optional_local(
                        name, target_type, sub, init.loc)
                slot_cpp_type = sub.name
            init_slot = self.ctx.slots.next_slot()
            slot_type = self._slot_decl_type(slot_cpp_type, is_opt_field)
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {init_slot};\n")
                if name in self.ctx.rvalue_reassigned_vars:
                    rebind_slot = self.ctx.slots.next_slot()
                    self.ctx.rebind_slots[name] = rebind_slot
                    self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {rebind_slot};\n")
                else:
                    self.ctx.rebind_slots[name] = init_slot
                deref = self._ptr_from_rvalue_slot(init_slot, init_expr, is_opt_field,
                                                   init_slot not in self.ctx.plain_rebind_slots)
                return f"{indent}{target} = {deref};\n"
            if name in self.ctx.rvalue_reassigned_vars:
                # Separate rebind slot so aliases to init value aren't overwritten
                rebind_slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[name] = rebind_slot
                deref = self._ptr_from_local_slot(init_slot, is_opt_field)
                return (f"{indent}{static_kw}{slot_type} {init_slot} = {init_expr};\n"
                        f"{indent}{static_kw}{slot_opt_cpp} {rebind_slot};\n"
                        f"{indent}{target} = {deref};\n")
            self.ctx.rebind_slots[name] = init_slot
            deref = self._ptr_from_local_slot(init_slot, is_opt_field)
            return (f"{indent}{static_kw}{slot_type} {init_slot} = {init_expr};\n"
                    f"{indent}{target} = {deref};\n")

        # Pre-declare rebind slot for lvalue-init vars with future rvalue rebinds
        rebind_decl = ""
        if name in self.ctx.rvalue_reassigned_vars:
            slot = self.ctx.slots.next_slot()
            self.ctx.rebind_slots[name] = slot
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {slot};\n")
            else:
                rebind_decl = f"{indent}{static_kw}{slot_opt_cpp} {slot};\n"

        if isinstance(init, _TpyName) and init.name in self.ctx.pointer_locals:
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = {init_expr};\n"
        elif self.ctx._is_pointer_global(init):
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = {init_expr};\n"
        elif self.ctx.is_global_name(init):
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = &({init_expr});\n"
        else:
            # lvalue ref: param, subscript, field -> take address
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = &({init_expr});\n"

    def _gen_slice_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate code for slice assignment via __setitem__(basic_slice/slice) stub dispatch."""
        assert isinstance(stmt.target, TpySubscript)
        sl = stmt.target.index
        assert isinstance(sl, TpySlice)
        fi = stmt.target.slice_function_info
        assert fi is not None
        obj = self.expressions.gen_expr(stmt.target.obj)
        subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(stmt.target.obj) else obj
        slice_arg = self.expressions._gen_slice_object(sl, stepped=stmt.target.is_stepped_slice)
        target_type = self.ctx.get_expr_type(stmt.target)
        value = self.expressions.gen_expr(stmt.value, target_type)
        value = self.expressions._maybe_move(stmt.value, value)
        # Non-empty array literals generate bare {e1, e2, ...} which C++ can't deduce Range from;
        # empty literals already include the explicit type from _gen_array_literal.
        if isinstance(stmt.value, TpyArrayLiteral) and stmt.value.elements:
            assert is_list(target_type)
            elem_cpp = self.types.type_to_cpp(target_type.type_args[0])
            value = f"std::vector<{elem_cpp}>{value}"
        code = self.builtins.gen_call_from_fi(fi, subscript_obj, [slice_arg, value])
        return f"{indent}{code};\n"

    def _gen_pointer_local_rebind(self, name: str, cpp_type: str, init: 'TpyExpr',
                                   target_type: TpyType | None, indent: str) -> str:
        """Generate pointer-local rebinding code (reassignment).

        For rvalue sources, reuses the rebind slot declared at init site
        to avoid creating loop-scoped storage that would dangle.
        """
        from ..parse import TpyName as _TpyName
        cpp_name = escape_cpp_name(name)

        # None literal -> set to nullptr (Optional/Ptr) or monostate (Union)
        if isinstance(init, TpyNoneLiteral):
            if isinstance(target_type, UnionType):
                rebind_slot = self.ctx.rebind_slots.get(name)
                if rebind_slot:
                    is_optional_slot = rebind_slot not in self.ctx.plain_rebind_slots
                    if is_optional_slot:
                        return (f"{indent}{rebind_slot}.emplace(std::monostate{{}});\n"
                                f"{indent}{cpp_name} = &(*{rebind_slot});\n")
                    return (f"{indent}{rebind_slot} = std::monostate{{}};\n"
                            f"{indent}{cpp_name} = &{rebind_slot};\n")
                return f"{indent}(*{cpp_name}) = std::monostate{{}};\n"
            return f"{indent}{cpp_name} = nullptr;\n"

        # OPTIONAL_STORAGE source: see the parallel handler in
        # _gen_pointer_local_init for the design rationale. rvalue call
        # reuses the slot declared at init site; lvalue name lifts
        # directly. Structural AST-shape dispatch keeps the param-source
        # case off the slot path. If the call-source path finds no slot
        # (defensive -- the init-site path always declares one), falls
        # through to the generic rvalue path below.
        is_call_src = self.ctx.callee_returns_own_ptr_optional(init)
        is_name_src = (isinstance(init, TpyName)
                       and self.ctx.needs_optional_to_ptr_lift(init.name))
        if is_call_src:
            rebind_slot = self.ctx.rebind_slots.get(name)
            if rebind_slot is not None:
                init_expr = self.expressions.gen_expr(init, target_type)
                return (f"{indent}{rebind_slot} = {init_expr};\n"
                        f"{indent}{cpp_name} = ::tpy::optional_to_ptr({rebind_slot});\n")
        elif is_name_src:
            init_expr = self.expressions.gen_expr(init, target_type)
            return f"{indent}{cpp_name} = ::tpy::optional_to_ptr({init_expr});\n"

        init_type = self.ctx.get_expr_type(init)
        # Optional non-value field on lvalue -> optional_to_ptr directly
        # Optional non-value non-field source -> T* pass-through
        # Optional non-value field on rvalue -> falls through to rvalue path
        is_storage_opt_lvalue = (
            self.ctx.is_storage_form_optional_source(init)
            and not self.ctx.is_rvalue_source(init))
        is_opt_field = (isinstance(init_type, OptionalType)
                        and init_type.uses_pointer_repr()
                        and (isinstance(init, TpyFieldAccess)
                             or is_storage_opt_lvalue))
        if isinstance(init_type, OptionalType) and init_type.uses_pointer_repr():
            if is_storage_opt_lvalue:
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{cpp_name} = ::tpy::optional_to_ptr({init_expr});\n"
            # Mirror of the init-site value-emit-rvalue check. Currently
            # unreachable for container literals because sema infers their
            # intrinsic type during rebind (not the target Optional), but
            # kept symmetric in case sema's type propagation changes.
            if not self.ctx.is_value_emit_rvalue(init):
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{cpp_name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = "auto" if cpp_type == "auto" else f"std::optional<{cpp_type}>"
        if self.ctx.is_rvalue_source(init):
            # Reject rvalue subclass rebound into Optional[Polymorphic]: the
            # shared rebind slot can't preserve dynamic type. Mirror of the
            # init-site check (see TODO.md for the heap-allocated-slot fix).
            sub = polymorphic_subclass_into_optional(
                target_type, self.ctx.get_expr_type(init),
                self.ctx.analyzer.registry)
            if sub is not None:
                self._reject_polymorphic_rvalue_into_optional_local(
                    name, target_type, sub, init.loc)
            rebind_slot = self.ctx.rebind_slots.get(name)
            if rebind_slot:
                deref = self._ptr_from_rvalue_slot(rebind_slot, init_expr, is_opt_field,
                                                   rebind_slot not in self.ctx.plain_rebind_slots)
                return f"{indent}{cpp_name} = {deref};\n"
            # First rvalue assignment (e.g. global init) -- declare slot here
            slot = self.ctx.slots.next_slot()
            self.ctx.rebind_slots[name] = slot
            slot_type = self._slot_decl_type(cpp_type, is_opt_field)
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {slot};\n")
                deref = self._ptr_from_rvalue_slot(slot, init_expr, is_opt_field,
                                                   slot not in self.ctx.plain_rebind_slots)
                return f"{indent}{cpp_name} = {deref};\n"
            if not is_opt_field:
                self.ctx.plain_rebind_slots.add(slot)
            deref = self._ptr_from_local_slot(slot, is_opt_field)
            return (f"{indent}{static_kw}{slot_type} {slot} = {init_expr};\n"
                    f"{indent}{cpp_name} = {deref};\n")
        elif isinstance(init, _TpyName) and init.name in self.ctx.pointer_locals:
            return f"{indent}{cpp_name} = {init_expr};\n"
        elif self.ctx._is_pointer_global(init):
            return f"{indent}{cpp_name} = {init_expr};\n"
        elif self.ctx.is_global_name(init):
            return f"{indent}{cpp_name} = &({init_expr});\n"
        else:
            return f"{indent}{cpp_name} = &({init_expr});\n"

    def _gen_ptr_variant_local_init(
        self, stmt: 'TpyVarDecl', target_type: UnionType, cpp_name: str, indent: str,
    ) -> str:
        """Generate initialization for a pointer-variant union local.

        Handles three source kinds:
        - Pointer-variant source (param, local, function return) -> copy directly
        - Rvalue (constructor, Own return) -> storage slot + to_ptr_variant
        - Lvalue value-variant (field, container element) -> to_ptr_variant
        - None literal -> std::monostate{}
        """
        self.ctx.ptr_variant_locals.add(stmt.name)
        pv_type = self.types.type_to_cpp_ptr_variant(target_type)
        val_type = self.types.type_to_cpp(target_type)

        if not stmt.init:
            # Uninitialized nullable union -> monostate
            return f"{indent}{pv_type} {cpp_name} = std::monostate{{}};\n"

        if isinstance(stmt.init, TpyNoneLiteral):
            return f"{indent}{pv_type} {cpp_name} = std::monostate{{}};\n"

        if self.ctx.is_ptr_variant_source(stmt.init):
            # Already a pointer variant (param, local, function call returning ptr variant)
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            return f"{indent}{pv_type} {cpp_name} = {init_expr};\n"

        if self.ctx.is_rvalue_source(stmt.init):
            # Rvalue (constructor, Own return, literal) -> allocate storage slot
            static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            slot = self.ctx.slots.next_slot()
            # Pre-declare rebind slot if the variable gets reassigned later with rvalues
            rebind_decl = ""
            if stmt.name in self.ctx.rvalue_reassigned_vars:
                rebind_slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[stmt.name] = rebind_slot
                rebind_decl = f"{indent}{static_kw}std::optional<{val_type}> {rebind_slot};\n"
            return (f"{rebind_decl}"
                    f"{indent}{static_kw}{val_type} {slot} = {init_expr};\n"
                    f"{indent}{pv_type} {cpp_name} = ::tpy::to_ptr_variant({slot});\n")

        # Lvalue source: either a value-variant lvalue or a concrete-type lvalue
        init_type = self.ctx.get_expr_type(stmt.init)
        init_expr = self.expressions.gen_expr(stmt.init, target_type)
        # Pre-declare rebind slot if needed
        rebind_decl = ""
        if stmt.name in self.ctx.rvalue_reassigned_vars:
            static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
            rebind_slot = self.ctx.slots.next_slot()
            self.ctx.rebind_slots[stmt.name] = rebind_slot
            rebind_decl = f"{indent}{static_kw}std::optional<{val_type}> {rebind_slot};\n"
        # Detect const source (field on const-ref param or const-indirect local)
        is_const_source = self.ctx.is_const_union_source(stmt.init)
        # If source is a value variant (field, container element), convert to pointer variant
        if isinstance(init_type, UnionType):
            if is_const_source:
                cpv_type = self.types.type_to_cpp_const_ptr_variant(target_type)
                self.ctx.const_indirect_locals.add(stmt.name)
                return (f"{rebind_decl}"
                        f"{indent}{cpv_type} {cpp_name} = ::tpy::to_const_ptr_variant({init_expr});\n")
            return (f"{rebind_decl}"
                    f"{indent}{pv_type} {cpp_name} = ::tpy::to_ptr_variant({init_expr});\n")
        # Concrete-type lvalue (e.g. Dog param): take address for implicit variant construction
        if is_const_source:
            cpv_type = self.types.type_to_cpp_const_ptr_variant(target_type)
            self.ctx.const_indirect_locals.add(stmt.name)
            return (f"{rebind_decl}"
                    f"{indent}{cpv_type} {cpp_name}{{&({init_expr})}};\n")
        return (f"{rebind_decl}"
                f"{indent}{pv_type} {cpp_name}{{&({init_expr})}};\n")

    def _gen_ptr_variant_local_reassign(
        self, stmt: 'TpyVarDecl', target_type: TpyType | None, cpp_name: str, indent: str,
    ) -> str:
        """Generate reassignment for a pointer-variant union local."""
        assert target_type is not None
        assert isinstance(target_type, UnionType)
        pv_type = self.types.type_to_cpp_ptr_variant(target_type)
        val_type = self.types.type_to_cpp(target_type)

        if isinstance(stmt.init, TpyNoneLiteral):
            return f"{indent}{cpp_name} = std::monostate{{}};\n"

        if self.ctx.is_ptr_variant_source(stmt.init):
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            return f"{indent}{cpp_name} = {init_expr};\n"

        if self.ctx.is_rvalue_source(stmt.init):
            # Rvalue -> use pre-declared rebind slot
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            rebind_slot = self.ctx.rebind_slots.get(stmt.name)
            if rebind_slot:
                return (f"{indent}{rebind_slot}.emplace({init_expr});\n"
                        f"{indent}{cpp_name} = ::tpy::to_ptr_variant(*{rebind_slot});\n")
            # Fallback: allocate inline slot
            static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
            slot = self.ctx.slots.next_slot()
            return (f"{indent}{static_kw}{val_type} {slot} = {init_expr};\n"
                    f"{indent}{cpp_name} = ::tpy::to_ptr_variant({slot});\n")

        # Lvalue source
        init_type = self.ctx.get_expr_type(stmt.init)
        init_expr = self.expressions.gen_expr(stmt.init, target_type)
        if isinstance(init_type, UnionType):
            return f"{indent}{cpp_name} = ::tpy::to_ptr_variant({init_expr});\n"
        # Concrete-type lvalue: take address
        pv_cpp = self.types.type_to_cpp_ptr_variant(target_type)
        return f"{indent}{cpp_name} = {pv_cpp}{{&({init_expr})}};\n"

    def _maybe_wrap_storage_tuple_source(self, stmt: 'TpyTupleUnpack', value_expr: str) -> str:
        """If unpacking a storage-form tuple into pointer-form Optional locals,
        wrap the source with tuple_to_pointer so std::get<I> yields T*.

        When the source binding is const (loop var iterating a const list /
        dict.values() / self.field in a readonly method), the inner pointers
        derived via optional_to_ptr come out as `const T*`, so the converted
        tuple type must use `to_cpp_return_const()` to match.
        """
        ptr_form = TupleType(tuple(stmt.target_types))
        if not ptr_form.has_pointer_repr_element():
            return value_expr
        # An Own[T] tuple element over a plain reference type is stored by
        # value and MOVED out at unpack, not pointer-converted.
        # `stmt.target_types` strips Own[T] -> T (so it looks pointer-repr), so
        # consult the SOURCE tuple type. Own over a pointer-repr Optional stays
        # eligible: its `std::optional<T>` storage slot is lifted to `T*` like
        # any other storage tuple.
        src_value_type = self.ctx.get_expr_type(stmt.value)
        src_tuple_type = (unwrap_qualifiers(src_value_type)
                          if src_value_type is not None else None)

        def _own_moved_elem(et: TpyType) -> bool:
            if not isinstance(et, OwnType):
                return False
            inner = unwrap_readonly(et.wrapped)
            return not (isinstance(inner, OptionalType)
                        and inner.uses_pointer_repr())

        if isinstance(src_tuple_type, TupleType) and any(
                _own_moved_elem(et) for et in src_tuple_type.element_types):
            return value_expr
        if not self.ctx.is_storage_form_source(stmt.value):
            return value_expr
        is_const_source = (isinstance(stmt.value, TpyName)
                           and stmt.value.name in self.ctx.const_storage_form_tuple_locals)
        resolved = self.types.resolve_tuple_pending(ptr_form)
        return self.ctx.convert(
            FormValue(value_expr, resolved, CppForm.STORAGE, is_const=is_const_source),
            dst_type=resolved, dst_form=CppForm.BORROW)

    def gen_yield_value(self, yield_stmt: TpyYield) -> str:
        """Emit yield value, bridging storage->pointer when the source is a
        storage location and the iterator slot is borrow form.

        Iterator yields hand out references like function returns: the slot
        for `tuple[T | None, ...]` is `std::tuple<T*, ...>` (borrow form), so
        a storage-form source (field, subscript, etc.) needs `tuple_to_pointer`
        to bridge. Pointer-form sources (rvalue tuple literals, pointer-form
        locals) already match the slot and pass through unchanged.
        """
        yield_type = self.ctx.current_yield_type
        expr = self.expressions.gen_expr(yield_stmt.value, yield_type)
        # A proven-non-None value-Optional NAME into a plain-T yield slot
        # reads the inner value; frame/lambda emission does not always route
        # the name through the target-driven unwrap in gen_expr. Names only:
        # params/locals are frame-private, but a FIELD's narrow can go stale
        # across a suspension (the caller may rebind it between next()
        # calls), so field sources keep the loud build error (BUGS.md).
        if yield_type is not None and isinstance(yield_stmt.value, TpyName):
            expr = self.expressions._maybe_unwrap_narrowed_optional(
                yield_stmt.value, expr, self.ctx.is_indirect_name(yield_stmt.value),
                target_type=yield_type)
        # Deref a borrow-form for-loop var (stored as T* so the borrow survives
        # suspension) into the value-form yield slot. Gated on the
        # borrow-form-loop-var set, not the broader pointer_locals: pointer-repr
        # Optional/Union locals must keep the pass-through path or `(*name)`
        # would strip their None/variant case.
        if (isinstance(yield_stmt.value, TpyName)
                and yield_stmt.value.name in self.ctx.generator_borrow_form_loop_vars):
            return f"(*{expr})"
        return self._maybe_wrap_tuple_to_pointer(
            expr, yield_type, self.ctx.unwrap_copy(yield_stmt.value))

    def _maybe_wrap_tuple_to_pointer(self, expr: str, target_type: TpyType | None,
                                      source: TpyExpr | None = None,
                                      const: bool = False) -> str:
        """Wrap a storage-form tuple expression with tuple_to_pointer if the
        target slot is std::tuple<T*, ...> (borrow form) and the source reads
        from a storage location. Mirror of `_maybe_wrap_tuple_to_storage`.

        The lifted borrow type must match the destination local's declared
        const-ness (see `_compute_borrow_tuple_const`): `const` forces const
        element pointers; a const source also implies them.
        """
        if target_type is None:
            return expr
        unwrapped = unwrap_readonly(unwrap_ref_type(target_type))
        if not (isinstance(unwrapped, TupleType)
                and unwrapped.has_pointer_repr_element()):
            return expr
        if source is None or not self.ctx.is_storage_form_source(source):
            return expr
        is_const = const or self.ctx.is_const_storage_source(source)
        resolved = self.types.resolve_tuple_pending(unwrapped)
        return self.ctx.convert(
            FormValue(expr, resolved, CppForm.STORAGE, is_const=is_const),
            dst_type=resolved, dst_form=CppForm.BORROW)

    def _tuple_owning_slot(self, name: str, var_type: TpyType | None,
                           indent: str) -> tuple[str, str]:
        """Return (slot_name, prefix_decl) for the storage slot backing an
        owning-call binding of borrow-form tuple local `name`.

        The slot is `std::optional<std::tuple<..., T>>` (storage form); the
        owning rvalue is `.emplace`d into it and the local aliases the slot via
        `tuple_to_pointer`. Hoisted/branch-hoisted locals put the slot in
        `pending_hoist_decls` (function scope) so it outlives the branch; a
        straight-line local declares it inline. Reused across rebinds via
        `rebind_slots` so a later owning RHS emplaces into the same slot.
        """
        existing = self.ctx.rebind_slots.get(name)
        if existing is not None:
            return existing, ""
        storage_cpp = unwrap_readonly(unwrap_ref_type(var_type)).to_cpp_stored()
        slot = self.ctx.slots.next_slot()
        self.ctx.rebind_slots[name] = slot
        slot_opt = f"std::optional<{storage_cpp}>"
        if (name in self.ctx.hoisted_vars
                or name in self.ctx.branch_hoisted_vars):
            static_kw = "static " if self.ctx.slots.global_scope else ""
            self.ctx.pending_hoist_decls.append(f"{static_kw}{slot_opt} {slot};\n")
            # The function-scope slot is shared across branches; keep its
            # rebind_slots mapping alive past branch-scope restores so a
            # sibling arm reuses it instead of allocating a second slot.
            self.ctx.persistent_rebind_slots[name] = slot
            return slot, ""
        return slot, f"{indent}{slot_opt} {slot};\n"

    def _borrow_tuple_rhs(self, name: str, init: TpyExpr,
                          var_type: TpyType | None, elem_const: bool,
                          indent: str) -> tuple[str, str]:
        """Return (prefix_decl, rhs_expr) for assigning `init` into the
        borrow-form tuple local `name` (`std::tuple<..., T*>`).

        Owning-call RHS -> `.emplace` into a storage slot, then lift the slot
        (lvalue) to the borrow form; storage-form lvalue RHS -> element-wise
        `tuple_to_pointer` lift; borrow-form RHS (literal / pointer-form local)
        -> assign directly. All lifts target the local's declared const-ness.
        """
        inner = self.ctx.unwrap_copy(init)
        init_expr = self.expressions.gen_expr(init, var_type)
        # An owning-call RHS (Own[tuple] / per-element-Own return) is a dying
        # rvalue -- materialize a slot rather than take its address. The
        # owning-call shape is exactly is_storage_form_source for a call node.
        owning_call = (isinstance(inner, (TpyCall, TpyMethodCall))
                       and self.ctx.is_storage_form_source(inner))
        if owning_call:
            borrow_cpp = self.types.tuple_borrow_cpp(
                unwrap_readonly(unwrap_ref_type(var_type)), const=elem_const)
            slot, prefix = self._tuple_owning_slot(name, var_type, indent)
            return prefix, (f"::tpy::tuple_to_pointer<{borrow_cpp}>"
                            f"({slot}.emplace({init_expr}))")
        return "", self._maybe_wrap_tuple_to_pointer(
            init_expr, var_type, inner, const=elem_const)

    def _optional_borrow_tuple_rhs(self, name: str, init: TpyExpr | None,
                                   inner_tuple: TpyType, elem_const: bool,
                                   opt_cpp: str, indent: str) -> tuple[str, str]:
        """Return (prefix_decl, rhs_expr) for binding `init` into a nullable
        borrow-form tuple local `name` (`std::optional<std::tuple<..., T*>>`).

        `None` -> `std::nullopt`; otherwise reuse `_borrow_tuple_rhs` (which
        produces the `std::tuple<..., T*>` borrow value, materializing an
        owning-call RHS into a storage slot) and wrap the result in the
        optional. The inner tuple aliases its reference elements so a rebind
        does not copy them (matching CPython).
        """
        if init is None:
            return "", "std::nullopt"
        inner = self.ctx.unwrap_copy(init)
        if isinstance(inner, TpyCoerce):
            inner = inner.expr
        if isinstance(inner, TpyNoneLiteral):
            return "", "std::nullopt"
        prefix, borrow_rhs = self._borrow_tuple_rhs(
            name, init, inner_tuple, elem_const, indent)
        return prefix, f"{opt_cpp}{{{borrow_rhs}}}"

    def _maybe_wrap_tuple_to_storage(self, expr: str, target_type: TpyType | None,
                                       source: TpyExpr | None = None) -> str:
        """Wrap a pointer-form tuple expression with tuple_to_storage if the
        target's storage is std::tuple<std::optional<T>, ...>.

        Returns expr unchanged when target isn't a tuple containing pointer-
        repr Optional elements, or when `source` is itself a storage-form
        location (field, subscript, global, storage-form local) -- those
        already match the target's slot shape and need no conversion.
        """
        if target_type is None:
            return expr
        unwrapped = unwrap_readonly(unwrap_ref_type(target_type))
        if not (isinstance(unwrapped, TupleType)
                and unwrapped.has_pointer_repr_element()):
            return expr
        if source is not None and self.ctx.is_storage_form_source(source):
            return expr
        resolved = self.types.resolve_tuple_pending(unwrapped)
        return self.ctx.convert(
            FormValue(expr, resolved, CppForm.BORROW),
            dst_type=resolved, dst_form=CppForm.STORAGE)

    def _lift_to_element_storage(self, value_expr: TpyExpr, value_code: str,
                                 elem_type: TpyType | None) -> str:
        """Lift a borrow-form value into storage form for a container-element
        store (`xs[i] = p`): a pointer-repr Optional element wants
        std::optional<T>, a ptr-variant Union element wants a value variant.

        Copies (no move) -- a container element owns its data inline, and the
        move-vs-copy decision belongs to the ownership pass, not this form
        bridge; an unconditional move here would gut a borrowed source
        (cf. the B30 move-out class). A storage-form source already matches the
        slot and passes through.
        """
        if elem_type is None:
            return value_code
        bare = unwrap_readonly(unwrap_ref_type(elem_type))
        if not (isinstance(bare, (OptionalType, UnionType)) and bare.uses_pointer_repr()):
            return value_code
        # A recursive-union wrapper (needs_wrapper) is a struct, not a
        # std::variant -- the value-variant lift does not apply.
        if isinstance(bare, UnionType) and bare.needs_wrapper():
            return value_code
        src = self.ctx.unwrap_copy(value_expr)
        if isinstance(src, TpyCoerce):
            src = src.expr
        # A narrowed union name is already rendered as the concrete alternative
        # (`*std::get<A*>(p)`), not a variant, so the value-variant lift would be
        # ill-formed -- the value-variant element is constructed from it directly.
        # Mirrors the guard in `_to_value_variant_if_needed`.
        if isinstance(src, TpyName) and src.name in self.ctx.narrowed_vars:
            return value_code
        form = self.ctx.source_form(src)
        if form is not CppForm.BORROW:
            return value_code
        return self.ctx.convert(
            FormValue(value_code, bare, form, is_const=self.ctx.is_const_storage_source(src)),
            dst_type=bare, dst_form=CppForm.STORAGE)

    def _gen_var_decl_code(self, stmt: TpyVarDecl, indent: str) -> str | None:
        """Generate code for a variable declaration. Returns code to write or None."""
        from ..parse.nodes import VarLinkage
        if stmt.linkage != VarLinkage.DEFAULT:
            return None
        # Final globals are defined at namespace scope, skip in __tpy_init
        if stmt.is_final:
            return None

        # A forwarded proto-param alias (`xs = it`) is a compile-time alias:
        # it has no frame field, so the binding emits nothing -- every later
        # use resolves to the backing param via `generator_storage_name`.
        if (self.ctx.in_generator_body
                and stmt.name in self.ctx.generator_forwarded_locals):
            self.ctx.declared_vars.add(stmt.name)
            self.ctx.local_scope_names.add(stmt.name)
            return None

        # Generator body: variable is a struct field, emit assignment only
        if self.ctx.in_generator_body and stmt.name in self.ctx.generator_field_names:
            self.ctx.declared_vars.add(stmt.name)
            self.ctx.local_scope_names.add(stmt.name)
            if stmt.name in self.ctx.pointer_locals:
                # Frame slot stores `T*` directly; reads/writes (arrow
                # access, &(value) on assign, nullptr for None) flow
                # through the same paths as a sync pointer-local.
                if stmt.init:
                    target_type = self.ctx.var_types.get(stmt.name)
                    inner = (target_type.inner
                             if isinstance(target_type, OptionalType)
                             else target_type)
                    cpp_type = self.types.type_to_cpp(inner) if inner else "auto"
                    return self._gen_pointer_local_rebind(
                        stmt.name, cpp_type, stmt.init, target_type, indent)
                return None
            # Frame-promoted storage-slot local: register movability so a
            # last-use read moves out of the slot instead of copying it.
            if stmt.name in self.ctx.sema_movable_locals:
                self.ctx.movable_locals.add(stmt.name)
            if stmt.init:
                cpp_name = escape_cpp_name(stmt.name)
                var_type = self.ctx.var_types.get(stmt.name)
                if (isinstance(var_type, OwnType)
                        and is_dyn_protocol(unwrap_readonly(var_type.wrapped))):
                    inner = unwrap_readonly(var_type.wrapped)
                    # Concrete handle (optional<__coro_*> frame field):
                    # construct the frame in place -- zero allocation.
                    if isinstance(inner, ConcreteCoroType):
                        return self._gen_concrete_coro_write(
                            cpp_name, stmt.init, indent)
                    # Owned-erased @dynamic local (unique_ptr<P> frame
                    # field): route the RHS through the own-arg wrap so a
                    # concrete rvalue gets its make_adapter erasure and an
                    # already-erased source forwards.
                    init_expr = self.expressions._gen_dynamic_protocol_own_arg(
                        stmt.init, inner)
                    return f"{indent}{cpp_name} = {init_expr};\n"
                # Thread the binding type for coerce inits (a view-resolved
                # frame field fed a stale view->owned coerce must render the
                # source bare, gen_expr's stale-coerce arm) and for None
                # literals (a value-Optional field needs the target-typed
                # std::nullopt, not the pointer-repr nullptr default) --
                # like the sync decl.
                init_expr = self.expressions.gen_expr(
                    stmt.init,
                    self.ctx.var_types.get(stmt.name)
                    if isinstance(stmt.init, (TpyCoerce, TpyNoneLiteral))
                    else None)
                # A proven-non-None value-Optional NAME into a plain-T frame
                # field reads the inner value; an Optional field keeps the
                # whole copy (the helper's target_type guard). Names only --
                # a FIELD source's narrow can go stale across a suspension
                # (see the yield sink's note).
                if var_type is not None and isinstance(stmt.init, TpyName):
                    init_expr = self.expressions._maybe_unwrap_narrowed_optional(
                        stmt.init, init_expr, self.ctx.is_indirect_name(stmt.init),
                        target_type=var_type)
                if stmt.name in self.ctx.generator_frame_slot_locals:
                    # frame_slot<T> has no operator= for arbitrary T;
                    # writes route through emplace, which also destroys
                    # any prior payload before constructing the new one.
                    init_expr = self.types.typed_brace_init(
                        init_expr, self.ctx.var_types.get(stmt.name))
                    return f"{indent}{cpp_name}.emplace({init_expr});\n"
                if stmt.name in self.ctx.borrow_form_tuple_locals:
                    # Borrow-form tuple frame field: lift a storage-form RHS
                    # element-wise to pointers, same as the sync reassign path.
                    init_expr = self._maybe_wrap_tuple_to_pointer(
                        init_expr, self.ctx.var_types.get(stmt.name),
                        self.ctx.unwrap_copy(stmt.init))
                return f"{indent}{cpp_name} = {init_expr};\n"
            return None

        cpp_name = escape_cpp_name(stmt.name)

        # Global-declared vars: emit assignment to the existing global, not a local decl.
        # target_type must be the global's *declared* type, not the init expression's
        # type -- otherwise `None` against a `Ptr[T]` or value-`Optional[T]` global
        # would lower as the NoneType unit-value (std::monostate{}) instead of nullptr
        # / std::nullopt.
        if stmt.name in self.ctx.global_declared_vars:
            if not stmt.init:
                return None
            var_type = resolve_stmt_type_cascade(stmt, self.ctx.analyzer, self.types)
            if var_type is not None:
                var_type = unwrap_qualifiers(var_type)
            init_expr = self.expressions.gen_expr(stmt.init, var_type)
            # A proven-non-None value-Optional source into a plain-T global
            # reads the inner value, like the local-reassign arm (the
            # helper's target_type guard keeps Optional targets whole).
            if var_type is not None:
                init_expr = self.expressions._maybe_unwrap_narrowed_optional(
                    stmt.init, init_expr, self.ctx.is_indirect_name(stmt.init),
                    target_type=var_type)
            init_expr = self._maybe_wrap_tuple_to_storage(init_expr, var_type, stmt.init)
            target_name = self.ctx.native_global_names.get(stmt.name, stmt.name)
            return f"{indent}{target_name} = {init_expr};\n"

        # Check if variable is already declared (reassignment)
        if stmt.name in self.ctx.declared_vars:
            if stmt.init:
                # Owned-erased @dynamic rebind: unique_ptr assignment (the
                # previous coroutine is destroyed unrun -- sema warned);
                # the RHS routes through the own-arg wrap like the decl.
                sema_t = resolve_stmt_binding_type(
                    stmt, self.ctx.analyzer,
                    include_global_binding=(
                        self.ctx.current_ns is self.ctx.analyzer.global_ns))
                sema_t = unwrap_readonly(unwrap_send_sync(sema_t)) if sema_t else None
                if (isinstance(sema_t, OwnType)
                        and is_dyn_protocol(unwrap_readonly(sema_t.wrapped))):
                    inner = unwrap_readonly(sema_t.wrapped)
                    # Concrete handle rebind: re-emplace the frame in the
                    # optional slot (same-coroutine only; sema rejects
                    # mixed rebinds).
                    if isinstance(inner, ConcreteCoroType):
                        return self._gen_concrete_coro_write(
                            cpp_name, stmt.init, indent)
                    init_expr = self.expressions._gen_dynamic_protocol_own_arg(
                        stmt.init, inner)
                    return f"{indent}{cpp_name} = {init_expr};\n"
                var_type = self.ctx.var_types.get(stmt.name)
                form = self.ctx.local_cpp_form(stmt.name)
                if form is LocalCppForm.OPTIONAL_STORAGE:
                    # Hoisted optional<T>: move-assign into the slot.
                    init_expr = self.expressions.gen_expr(stmt.init, var_type)
                    return f"{indent}{cpp_name} = {init_expr};\n"
                if form is LocalCppForm.POINTER:
                    # @dynamic protocol reassignment: new adapter slot + rebind
                    if self._is_dynamic_protocol_type(var_type):
                        return self._gen_dynamic_protocol_rebind(stmt.name, var_type, stmt.init, indent)
                    # OptionalType uses inner type (pointer-local adds T*)
                    resolve_type = var_type
                    if isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
                        resolve_type = var_type.inner
                    cpp_type = self.types.type_to_cpp(resolve_type) if resolve_type else "auto"
                    # Structural protocol types map to C++ concepts which
                    # cannot be used as variable types; use auto instead.
                    # @dynamic protocols already have concrete base class
                    # names, so only check for structural protocols here.
                    if (resolve_type and isinstance(resolve_type, NominalType)
                            and resolve_type.is_protocol and not resolve_type.is_dynamic_protocol):
                        cpp_type = "auto"
                    return self._gen_pointer_local_rebind(stmt.name, cpp_type, stmt.init, var_type, indent)
                if form is LocalCppForm.PTR_VARIANT:
                    return self._gen_ptr_variant_local_reassign(stmt, var_type, cpp_name, indent)
                if form is LocalCppForm.BORROW_TUPLE:
                    # The local IS the borrow (std::tuple<..., T*>): an
                    # owning-call RHS materializes into the storage slot, a
                    # storage-form lvalue RHS lifts element-wise to pointers,
                    # a borrow-form RHS assigns directly. Lifts match the
                    # local's declared const-ness.
                    elem_const = stmt.name in self.ctx.const_borrow_form_tuple_locals
                    prefix, rhs = self._borrow_tuple_rhs(
                        stmt.name, stmt.init, var_type, elem_const, indent)
                    return f"{prefix}{indent}{cpp_name} = {rhs};\n"
                if form is LocalCppForm.OPTIONAL_BORROW_TUPLE:
                    # Nullable BORROW_TUPLE: assign the optional-wrapped borrow
                    # lift (alias) or std::nullopt for None. The inner tuple is
                    # the borrow form so reference elements alias on rebind.
                    inner_tuple = (var_type.inner
                                   if isinstance(var_type, OptionalType) else var_type)
                    elem_const = stmt.name in self.ctx.const_optional_borrow_tuple_locals
                    opt_cpp = (f"std::optional<"
                               f"{self.types.tuple_borrow_cpp(inner_tuple, const=elem_const)}>")
                    prefix, rhs = self._optional_borrow_tuple_rhs(
                        stmt.name, stmt.init, inner_tuple, elem_const, opt_cpp, indent)
                    return f"{prefix}{indent}{cpp_name} = {rhs};\n"
                # String x = x + y -> x += y for buffer reuse
                if result := self._try_str_inplace_append(stmt.name, cpp_name, stmt.init, var_type, indent):
                    return result
                init_expr = self.expressions.gen_expr(stmt.init, var_type)
                # A proven-non-None value-Optional source into a plain-T slot
                # reads the inner value; an Optional slot keeps the whole copy
                # (the helper's target_type guard).
                if var_type is not None:
                    init_expr = self.expressions._maybe_unwrap_narrowed_optional(
                        stmt.init, init_expr, self.ctx.is_indirect_name(stmt.init),
                        target_type=var_type)
                init_expr = self._maybe_wrap_tuple_to_storage(init_expr, var_type, stmt.init)
                return f"{indent}{cpp_name} = {init_expr};\n"
            return None

        # Determine target type for first declaration
        target_type = self._resolve_target_type(stmt)

        # First declaration - track the type and mark as local (shadows globals)
        self.ctx.declared_vars.add(stmt.name)
        self.ctx.local_scope_names.add(stmt.name)
        self.ctx.var_types[stmt.name] = target_type
        if self.ctx.current_ns and target_type:
            self.ctx.current_ns.bind_variable(stmt.name, target_type)
        if (isinstance(target_type, OptionalType)
                and target_type.wraps_pointer_repr_tuple()):
            # Nullable borrow-form tuple: `std::optional<std::tuple<..., T*>>`.
            # The optional wraps the BORROW-form inner tuple so reference
            # elements ALIAS storage on rebind (matching CPython) instead of
            # copying. Same lift machinery as BORROW_TUPLE, lifted into / out
            # of the std::optional; `None` init -> std::nullopt.
            self.ctx.optional_borrow_tuple_locals.add(stmt.name)
            elem_const = stmt.name in self.ctx.const_optional_borrow_tuple_locals
            inner_borrow = self.types.tuple_borrow_cpp(target_type.inner, const=elem_const)
            opt_cpp = f"std::optional<{inner_borrow}>"
            prefix, rhs = self._optional_borrow_tuple_rhs(
                stmt.name, stmt.init, target_type.inner, elem_const, opt_cpp, indent)
            return f"{prefix}{indent}{opt_cpp} {cpp_name} = {rhs};\n"
        if (stmt.init is not None
                and isinstance(target_type, TupleType)
                and target_type.has_own_element()
                and not target_type.has_pointer_repr_element()
                and stmt.name not in self.ctx.reassigned_vars):
            # An Own-element tuple is by-value storage (`std::tuple<..., T>`)
            # with no `T*` borrow form, so the local is always storage-form.
            # Register it so the call-site arg path (which collapses Own[T]->T
            # to a pointer-repr param) doesn't mistake it for a borrow-form
            # source and emit a copying tuple_to_storage lift -- deleted when an
            # element is @nocopy. Tuples that DO have a borrow form go through
            # the has_pointer_repr_element block below instead.
            self.ctx.storage_form_tuple_locals.add(stmt.name)
            if stmt.name in self.ctx.sema_movable_locals:
                self.ctx.movable_locals.add(stmt.name)
        if (stmt.init is not None
                and isinstance(target_type, TupleType)
                and target_type.has_pointer_repr_element()):
            # A REASSIGNED pointer-repr tuple local has one fixed C++ shape
            # across all its bindings: borrow form (`std::tuple<..., T*>`). An
            # owning-call RHS materializes into a function-local storage slot
            # and the local aliases it; storage lvalue / borrow sources lift /
            # assign as usual. This subsumes the owning-storage and the
            # alias-rebind cases under one representation (the tuple analog of
            # the scalar rvalue-reassigned pointer-local model).
            if stmt.name in self.ctx.reassigned_vars:
                self.ctx.borrow_form_tuple_locals.add(stmt.name)
                elem_const = stmt.name in self.ctx.const_borrow_form_tuple_locals
                borrow_cpp = self.types.tuple_borrow_cpp(target_type, const=elem_const)
                prefix, rhs = self._borrow_tuple_rhs(
                    stmt.name, stmt.init, target_type, elem_const, indent)
                return f"{prefix}{indent}{borrow_cpp} {cpp_name} = {rhs};\n"
            if self.ctx.is_storage_form_source(stmt.init):
                self.ctx.storage_form_tuple_locals.add(stmt.name)
                # A fresh-owned storage tuple (sema proved it owned, not an
                # alias) is move-only when it owns a @nocopy element and must
                # auto-move at its last use -- e.g. into an owned-tuple
                # `std::tuple<...>&&` param. sema_movable_locals already
                # excludes lvalue-aliasing sources (field/subscript inits).
                if stmt.name in self.ctx.sema_movable_locals:
                    self.ctx.movable_locals.add(stmt.name)
            else:
                # A literal whose non-value members are VALUE-captured (sema
                # marks fresh members owned, e.g. `(a.clone(), b.clone())`)
                # builds the storage form, so the local owns its members and
                # downstream reads need the storage->pointer wrap too.
                init_inner = self.ctx.unwrap_copy(stmt.init)
                if isinstance(init_inner, TpyCoerce):
                    init_inner = init_inner.expr
                if (isinstance(init_inner, TpyTupleLiteral) and init_inner.elem_capture
                        and any(cap == TupleElemCapture.VALUE
                                and i < len(target_type.element_types)
                                and TupleType._element_is_pointer_repr(
                                    target_type.element_types[i])
                                for i, cap in enumerate(init_inner.elem_capture))):
                    self.ctx.storage_form_tuple_locals.add(stmt.name)
                    if stmt.name in self.ctx.sema_movable_locals:
                        self.ctx.movable_locals.add(stmt.name)

        # A storage-form tuple local bound from an LVALUE storage source
        # aliases the source (CPython shares the elements): bind a reference
        # when the name is never rebound (reads keep the storage-form lifts),
        # or fall over to the borrow-form pointer machinery when it is
        # (references can't rebind; the reassign path lifts each RHS).
        # Rvalue sources (calls returning owning tuples) keep the owning
        # copy. Sema gates returning such aliases.
        if (stmt.name in self.ctx.storage_form_tuple_locals
                and stmt.init is not None
                and stmt.name not in self.ctx.hoisted_vars
                and stmt.name not in self.ctx.move_through_vars):
            alias_inner = (stmt.init.expr if isinstance(stmt.init, TpyCoerce)
                           else stmt.init)
            if (isinstance(alias_inner, (TpyFieldAccess, TpySubscript))
                    or (isinstance(alias_inner, TpyName)
                        and self.ctx.is_storage_form_source(alias_inner))):
                if stmt.name not in self.ctx.reassigned_vars:
                    if self.ctx.is_const_storage_source(alias_inner):
                        self.ctx.const_storage_form_tuple_locals.add(stmt.name)
                    init_expr = self.expressions.gen_expr(stmt.init, target_type)
                    return f"{indent}auto&& {cpp_name} = {init_expr};\n"
                self.ctx.storage_form_tuple_locals.discard(stmt.name)
                self.ctx.borrow_form_tuple_locals.add(stmt.name)
                elem_const = stmt.name in self.ctx.const_borrow_form_tuple_locals
                init_expr = self.expressions.gen_expr(stmt.init, target_type)
                init_expr = self._maybe_wrap_tuple_to_pointer(
                    init_expr, target_type, self.ctx.unwrap_copy(stmt.init),
                    const=elem_const)
                borrow_cpp = self.types.tuple_borrow_cpp(target_type, const=elem_const)
                return f"{indent}{borrow_cpp} {cpp_name} = {init_expr};\n"

        cpp_type = self._resolve_cpp_type(stmt)

        # Owned coroutine-handle / owned-erased @dynamic local. Concrete
        # handle: `std::optional<__coro_*>` holding the frame inline --
        # zero allocation. Erased: `std::unique_ptr<P>` (adapter-wrapped
        # concrete rvalue, or forwarded erased source). Both are
        # move-only, consumed by move into Own[P] slots.
        if (isinstance(target_type, OwnType)
                and is_dyn_protocol(unwrap_readonly(target_type.wrapped))):
            assert stmt.init, f"owned @dynamic local '{stmt.name}' requires initializer"
            protocol = unwrap_readonly(target_type.wrapped)
            if stmt.name in self.ctx.sema_movable_locals:
                self.ctx.movable_locals.add(stmt.name)
            if isinstance(protocol, ConcreteCoroType):
                owned_cpp = self.types.type_to_cpp(target_type)
                init_inner = self.ctx.unwrap_copy(stmt.init)
                if isinstance(init_inner, TpyName):
                    rhs = f"std::move({self.expressions.gen_expr(stmt.init)})"
                else:
                    rhs = self.expressions.gen_expr(stmt.init)
                return f"{indent}{owned_cpp} {cpp_name} = {rhs};\n"
            init_expr = self.expressions._gen_dynamic_protocol_own_arg(
                stmt.init, protocol)
            owned_cpp = f"std::unique_ptr<{self.protocols.get_dynamic_base_name(protocol)}>"
            return f"{indent}{owned_cpp} {cpp_name} = {init_expr};\n"

        # @dynamic protocol types always use adapter slots + Base* pointer-local
        if self._is_dynamic_protocol_type(target_type):
            assert stmt.init, f"@dynamic protocol local '{stmt.name}' requires initializer"
            self.ctx.pointer_locals.add(stmt.name)
            return self._gen_dynamic_protocol_init(stmt.name, target_type, stmt.init, indent)

        # TypeParamRef variable initialized from a user method or free function call: use
        # ::tpy::val_or_ref_t<T> (or ::tpy::val_or_cref_t<T> for readonly methods). This expands
        # to T for value types and T& (or const T&) for non-value types, matching the C++
        # return type semantics. Only for non-reassigned, non-hoisted vars -- rebinding a
        # val_or_ref_t alias is not possible in C++ (references can't be rebound), so
        # reassigned vars fall through to the rvalue/value-copy path instead.
        if (isinstance(target_type, TypeParamRef) and not target_type.is_value_type()
                and stmt.init is not None
                and isinstance(stmt.init, (TpyMethodCall, TpyCall))
                and stmt.name not in self.ctx.reassigned_vars
                and stmt.name not in self.ctx.hoisted_vars
                and stmt.name not in self.ctx.move_through_vars):
            fi = stmt.init.resolved_function_info
            if fi is not None and fi.cpp_template is None and isinstance(unwrap_ref_type(fi.return_type), TypeParamRef):
                init_expr = self.expressions.gen_expr(stmt.init, target_type)
                trait = "::tpy::val_or_cref_t" if fi.is_readonly else "::tpy::val_or_ref_t"
                return f"{indent}{trait}<{cpp_type}> {cpp_name} = {init_expr};\n"

        # Pointer-variant locals for non-value unions
        if self.ctx.is_ptr_variant_union(target_type):
            return self._gen_ptr_variant_local_init(stmt, target_type, cpp_name, indent)

        # Indirection for non-value types in function/method scope
        if self._needs_indirection(target_type, stmt.name, stmt.init):
            is_optional = isinstance(target_type, OptionalType)
            if not is_optional:
                assert stmt.init, f"indirect local '{stmt.name}' missing initializer"
            is_const = self._is_const_indirect(target_type, stmt.init, stmt)
            if is_const:
                self.ctx.const_indirect_locals.add(stmt.name)
            const_pfx = "const " if is_const else ""
            # The T*-vs-T& choice is the shared binding classifier (also read by
            # THIR lowering so the two paths cannot drift): REF_ALIAS is exactly
            # the T& case -- a single-assignment, lvalue-sourced, non-optional
            # plain non-value local; optional / reassigned / hoisted take the
            # pointer-local path.
            binding = classify_local_binding(
                target_type, stmt.init, self.ctx.analyzer, name=stmt.name,
                reassigned=self.ctx.reassigned_vars,
                rvalue_reassigned=self.ctx.rvalue_reassigned_vars,
                hoisted=self.ctx.hoisted_vars,
                move_through=self.ctx.move_through_vars)
            if binding is not LocalBinding.REF_ALIAS:
                # T* pointer-local -- needs rebinding support (or hoisted storage)
                self.ctx.pointer_locals.add(stmt.name)
                if stmt.name in self.ctx.sema_movable_locals:
                    self.ctx.movable_locals.add(stmt.name)
                if stmt.init:
                    return self._gen_pointer_local_init(stmt.name, cpp_type, stmt.init, target_type, indent)
                else:
                    # Optional without initializer -> nullptr
                    return f"{indent}{const_pfx}{cpp_type}* {cpp_name} = nullptr;\n"
            else:
                # T& reference -- alias without rebinding
                self.ctx.ref_bound_locals.add(stmt.name)
                init_expr = self.expressions.gen_expr_deref(stmt.init, target_type)
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                # A borrow-local aliasing an lvalue rooted in a const source
                # (param / field / container element off a const-inferred
                # receiver) must bind `const T&` -- else a mutable reference is
                # taken from a `const T`. `is_const_union_source` computes the
                # general "rooted in a const source" predicate, recursing through
                # chained field/subscript access to the base name -- the same one
                # the value-variant and Optional lifts consult.
                if not is_const and self.ctx.is_const_union_source(init_inner):
                    self.ctx.const_indirect_locals.add(stmt.name)
                    return f"{indent}const {cpp_type}& {cpp_name} = {init_expr};\n"
                return f"{indent}{const_pfx}{cpp_type}& {cpp_name} = {init_expr};\n"

        # Tier 1 non-value-type locals are eligible for auto-move at last use
        if (target_type and not target_type.is_value_type()
                and stmt.name in self.ctx.sema_movable_locals):
            self.ctx.movable_locals.add(stmt.name)

        if stmt.init:
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            if stmt.name in self.ctx.move_through_vars:
                init_expr = f"std::move({init_expr})"
            # Unwrap value-optional init when target is non-Optional
            if (
                not isinstance(target_type, OptionalType)
                and self._is_value_optional_expr(stmt.init)
            ):
                analyzed_type = self.ctx.get_expr_type(stmt.init)
                if isinstance(analyzed_type, OptionalType):
                    init_expr = f"::tpy::deref_optional_check({init_expr})"
                else:
                    init_expr = f"(*{init_expr})"
            else:
                # str/bytes view source -> owned storage init (same rule as the
                # return boundary; also covers Optional-param ternaries).
                init_expr = self._wrap_view_to_storage(
                    stmt.init, target_type, init_expr)
            return f"{indent}{cpp_type} {cpp_name} = {init_expr};\n"
        else:
            return f"{indent}{cpp_type} {cpp_name};\n"

    def _gen_assign_code(self, stmt: TpyAssign, indent: str) -> str:
        """Generate code for an assignment. Returns code to write."""
        # Clear stale assignment narrowing on reassignment
        if isinstance(stmt.target, TpyName):
            self.ctx.assign_narrowed_types.pop(stmt.target.name, None)
            self.ctx.literal_facts.pop(stmt.target.name, None)
        # Slice assignment: a[x:y] = rhs -> list_set_slice
        if isinstance(stmt.target, TpySubscript) and isinstance(stmt.target.index, TpySlice):
            return self._gen_slice_assign(stmt, indent)
        # TypedDict subscript assignment: d["key"] = val -> d.key = val
        if isinstance(stmt.target, TpySubscript) and stmt.target.typed_dict_field is not None:
            obj = self.expressions.gen_expr(stmt.target.obj)
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(stmt.target.obj) else obj
            target_type = self.ctx.get_expr_type(stmt.target)
            value = self.expressions.gen_expr(stmt.value, target_type)
            value = self.expressions._maybe_move(stmt.value, value)
            cpp_field = escape_cpp_name(stmt.target.typed_dict_field)
            return f"{indent}{subscript_obj}.{cpp_field} = {value};\n"
        # Special handling for subscript assignment
        if isinstance(stmt.target, TpySubscript):
            obj = self.expressions.gen_expr(stmt.target.obj)
            is_indirect = self.ctx.is_indirect_name(stmt.target.obj)
            # Unwrap a sema-narrowed value-Optional receiver before passing
            # it to __setitem__; mirrors the subscript-read path.
            obj = self.expressions._maybe_unwrap_narrowed_optional(
                stmt.target.obj, obj, is_indirect)
            target_type = self.ctx.get_expr_type(stmt.target)
            value = self.expressions.gen_expr(stmt.value, target_type)
            # The value slot is the inner type, so a proven-non-None optional
            # value must deref to (*v) (the receiver is unwrapped likewise above).
            value = self.expressions._maybe_unwrap_narrowed_optional(
                stmt.value, value, self.ctx.is_indirect_name(stmt.value))
            # Skip the last-use move for a view source headed to the owned-copy
            # sink below -- std::move on a trivially-copyable view is a no-op.
            if self.expressions._view_owned_copy_family(stmt.value, target_type) is None:
                value = self.expressions._maybe_move(stmt.value, value)
            value = self._maybe_wrap_tuple_to_storage(
                value, target_type, self.ctx.unwrap_copy(stmt.value))
            value = self._lift_to_element_storage(stmt.value, value, target_type)
            # A narrowed Optional view param deref'd into a str/bytes value slot
            # needs an owned copy: string_view->string is an implicit assignment
            # but span->vector is not, so emit it explicitly for both families.
            value = self.expressions._view_source_to_owned(
                stmt.value, target_type, value)
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            index_type = self.ctx.analyzer.get_expr_type(stmt.target.index)
            # Dereference globals for subscript access
            subscript_obj = f"(*{obj})" if is_indirect else obj
            # Thread the dict/set's view-typed key through so bytes/str
            # literals pin to static storage (avoiding a stored dangling view).
            index_expr = self.expressions.gen_index_expr(
                stmt.target.index, index_type, view_key_target(obj_type),
                obj_type)

            # Bounds-safe: index provably in [0, len(obj)), skip normalize_index.
            # See the matching note in expressions.py _gen_subscript: cast to
            # size_t for non-literal indices to avoid -Wsign-conversion,
            # except for concrete user records whose operator[] takes the
            # user's declared param type (typically int32_t).
            if stmt.target.bounds_safe:
                if (isinstance(stmt.target.index, TpyIntLiteral)
                        or _is_concrete_user_record(obj_type,
                                                     self.ctx.analyzer.registry)):
                    return f"{indent}{subscript_obj}[{index_expr}] = {value};\n"
                return f"{indent}{subscript_obj}[static_cast<std::size_t>({index_expr})] = {value};\n"

            # A bare collection-literal value can't deduce the forwarding-ref
            # value parameter of the ::tpy::__setitem__ template (the lvalue
            # `x[i] =` path above binds a brace-init fine, so it stays bare).
            value = self.types.typed_brace_init(value, target_type)
            # Use registry lookup for __setitem__
            fi = self.builtins.get_type_method_fi(obj_type, "__setitem__")
            if fi:
                code = self.builtins.gen_call_from_fi(fi, subscript_obj, [index_expr, value])
                return f"{indent}{code};\n"
            else:
                return f"{indent}::tpy::__setitem__({subscript_obj}, {index_expr}, {value});\n"

        # Pointer-local rebinding (e.g., x.field = ... where x is pointer-local handled by field access)
        if isinstance(stmt.target, TpyName) and stmt.target.name in self.ctx.pointer_locals:
            target_type = self.ctx.var_types.get(stmt.target.name)
            cpp_type = self.types.type_to_cpp(target_type) if target_type else "auto"
            return self._gen_pointer_local_rebind(stmt.target.name, cpp_type, stmt.value, target_type, indent)

        # Generator frame_slot rebinding: re-emplace (destroys old
        # payload if alive, constructs fresh). Mirrors the var-decl
        # path; the helper has no operator= for arbitrary T.
        if (isinstance(stmt.target, TpyName)
                and stmt.target.name in self.ctx.generator_frame_slot_locals):
            cpp_name = escape_cpp_name(stmt.target.name)
            target_type = self.ctx.var_types.get(stmt.target.name)
            value = self.expressions.gen_expr_deref(stmt.value, target_type)
            value = self.expressions._maybe_move(stmt.value, value)
            value = self.types.typed_brace_init(value, target_type)
            return f"{indent}{cpp_name}.emplace({value});\n"

        # Property setter: delegate to normal method call codegen
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.property_setter_call is not None:
            call = self.expressions._gen_method_call(stmt.target.property_setter_call)
            return f"{indent}{call};\n"

        # D16 dyn-attr __setattr__ fallback: delegate to normal method call codegen
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.dyn_setattr_call is not None:
            call = self.expressions._gen_method_call(stmt.target.dyn_setattr_call)
            return f"{indent}{call};\n"

        # Field-target type lookup: use the declared field type, not sema's
        # cached expression type. After narrowing (e.g. `if self.x is None:
        # return`), the cached type for the field-access LHS is the narrowed
        # inner, which would route None-assign past the OptionalType branch
        # and emit bare `nullptr` against `std::optional<...>`. Reused below
        # by the field-shape dispatch, the class-constant branch, and the
        # default fallthrough -- all three are field-target paths.
        field_target_type: TpyType | None = None
        if isinstance(stmt.target, TpyFieldAccess):
            declared = self.expressions._get_cpp_declared_type(stmt.target)
            field_target_type = (declared if declared is not None
                                 else self.ctx.get_expr_type(stmt.target))

        # Field assignment: boundary conversions for optional/union pointer repr.
        if isinstance(stmt.target, TpyFieldAccess):
            target_type = field_target_type
            if (isinstance(target_type, TupleType)
                    and target_type.has_pointer_repr_element()):
                source = self.ctx.unwrap_copy(stmt.value)
                target = self.expressions.gen_expr(stmt.target)
                value = self.expressions.gen_expr(stmt.value, target_type)
                value = self._maybe_wrap_tuple_to_storage(value, target_type, source)
                return f"{indent}{target} = {value};\n"
            # Optional field: std::optional<T> storage needs boundary conversion
            if isinstance(target_type, OptionalType) and target_type.uses_pointer_repr():
                target = self.expressions.gen_expr(stmt.target)
                # OPTIONAL_STORAGE source (Own[Opt[T_ref]] param): already
                # optional<T>, direct std::move into the field.
                if (isinstance(stmt.value, TpyName)
                        and self.ctx.needs_optional_to_ptr_lift(stmt.value.name)):
                    value = self.expressions.gen_expr(stmt.value)
                    value = self.expressions._maybe_move(stmt.value, value)
                    return f"{indent}{target} = {value};\n"
                # Value source is T* (pointer-local, function returning Optional) -> wrap.
                # A movable owned local at its last use MOVES its pointee into the
                # field (ptr_to_optional_move) rather than copying -- correct for
                # @nocopy and avoids a silent copy where ownership transfers.
                if self.ctx.is_indirect_name(stmt.value):
                    value = self.expressions.gen_expr(stmt.value)
                    if self.expressions._is_last_use_movable(stmt.value):
                        return f"{indent}{target} = ::tpy::ptr_to_optional_move({value});\n"
                    return f"{indent}{target} = ::tpy::ptr_to_optional({value});\n"
                raw_val_type = self.ctx.get_expr_type(stmt.value)
                val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                source = self.ctx.unwrap_copy(stmt.value)
                if (isinstance(val_type, OptionalType)
                        and not isinstance(source, TpyFieldAccess)
                        and not self.ctx.is_storage_form_optional_source(source)):
                    # Own[T] | None returns std::optional<T> -- direct assign
                    # T | None returns T* -- needs ptr_to_optional wrapping
                    is_owned_optional = (isinstance(val_type, OptionalType)
                                         and isinstance(val_type.inner, OwnType))
                    # Own[Optional[T]] param is std::optional<T>&& -- also direct assign
                    if not is_owned_optional and isinstance(stmt.value, TpyName):
                        param_type = self.ctx.current_func_params.get(stmt.value.name)
                        if isinstance(param_type, OwnType):
                            is_owned_optional = True
                    value = self.expressions.gen_expr(stmt.value, target_type)
                    if is_owned_optional:
                        value = self.expressions._maybe_move(stmt.value, value)
                        return f"{indent}{target} = {value};\n"
                    return f"{indent}{target} = ::tpy::ptr_to_optional({value});\n"
                # Direct value or optional-to-optional (field-to-field) works without conversion
                value = self.expressions.gen_expr_deref(stmt.value, target_type)
                value = self.expressions._maybe_move(stmt.value, value)
                return f"{indent}{target} = {value};\n"
            # Union field: pointer-variant source -> value-variant field conversion
            if self.ctx.is_ptr_variant_union(target_type):
                target = self.expressions.gen_expr(stmt.target)
                value = self.expressions.gen_expr(stmt.value, target_type)
                if self.ctx.is_ptr_variant_source(stmt.value):
                    val_cpp = self.types.type_to_cpp(target_type)
                    value = f"::tpy::to_value_variant<{val_cpp}>({value})"
                else:
                    value = self.expressions._maybe_move(stmt.value, value)
                return f"{indent}{target} = {value};\n"
            # bytes field (std::vector<uint8_t>) from a bytes-view source
            # (std::span<const uint8_t>) -- a BytesView source or a narrowed
            # bytes|None param deref: vector has no span-assign overload, so copy
            # via the shared view->owned chokepoint. Str needs no arm here:
            # std::string has an operator=(string_view), so a str-view source
            # lands via plain copy-assign on the default path.
            if is_bytes_type(target_type) and (
                    self.expressions._is_bytes_view_source(stmt.value)
                    or self.expressions._expr_uses_optional_bytes_param(stmt.value)):
                target = self.expressions.gen_expr(stmt.target)
                value = self.expressions.gen_expr_deref(stmt.value, target_type)
                value = self._wrap_view_to_storage(stmt.value, target_type, value)
                return f"{indent}{target} = {value};\n"
            # Ptr[T] field: storage-form Optional source needs optional_to_ptr
            # lift to mirror the storage-form-to-borrow-form bridge already
            # done for OptionalType destinations above.
            if isinstance(target_type, PtrType):
                source = self.ctx.unwrap_copy(stmt.value)
                raw_val_type = self.ctx.get_expr_type(stmt.value)
                val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                if (isinstance(val_type, OptionalType)
                        and val_type.uses_pointer_repr()
                        and self.ctx.is_storage_form_optional_source(source)):
                    target = self.expressions.gen_expr(stmt.target)
                    value = self.expressions.gen_expr(stmt.value, target_type)
                    return f"{indent}{target} = ::tpy::optional_to_ptr({value});\n"

        # Class-constant write: emit any receiver-side effects as a leading
        # statement so the qualified `<owner>::<member>` appears as a real
        # lvalue. Going through `gen_expr` would wrap it in a GCC statement
        # expression (an rvalue), which fails to compile on the LHS of `=`.
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.class_constant_owner is not None):
            receiver_stmt, lvalue = self.expressions.gen_class_constant_lvalue(stmt.target)
            target_type = field_target_type
            value = self.expressions.gen_expr_deref(stmt.value, target_type)
            value = self.expressions._maybe_move(stmt.value, value)
            prefix = f"{indent}{receiver_stmt};\n" if receiver_stmt else ""
            return f"{prefix}{indent}{lvalue} = {value};\n"

        # Default: simple assignment (includes field assignments like self.x = val).
        target = self.expressions.gen_expr(stmt.target)
        target_type = (field_target_type if isinstance(stmt.target, TpyFieldAccess)
                       else self.ctx.get_expr_type(stmt.target))
        # Detect x = x + y on string types -> emit x += y for buffer reuse
        if isinstance(stmt.target, TpyName):
            if result := self._try_str_inplace_append(stmt.target.name, target, stmt.value, target_type, indent):
                return result
        value = self.expressions.gen_expr_deref(stmt.value, target_type)
        value = self.expressions._maybe_move(stmt.value, value)
        return f"{indent}{target} = {value};\n"

    def _gen_del_item_code(self, stmt: TpyDelItem, indent: str) -> str:
        """Generate code for del obj[key] statement."""
        parts: list[str] = []
        for subscript in stmt.targets:
            obj = self.expressions.gen_expr(subscript.obj)
            obj_type = self.ctx.get_expr_type(subscript.obj)
            index_type = self.ctx.analyzer.get_expr_type(subscript.index)
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(subscript.obj) else obj
            index_expr = self.expressions.gen_index_expr(
                subscript.index, index_type, obj_type=obj_type)

            fi = self.builtins.get_type_method_fi(obj_type, "__delitem__")
            if fi:
                code = self.builtins.gen_call_from_fi(fi, subscript_obj, [index_expr])
                parts.append(f"{indent}{code};\n")
            else:
                parts.append(f"{indent}::tpy::__delitem__({subscript_obj}, {index_expr});\n")
        return "".join(parts)

    def _gen_del_attr_code(self, stmt: TpyDelAttr, indent: str) -> str:
        """D16 Phase 3: codegen for `del obj.foo` -- delegate to __delattr__."""
        parts: list[str] = []
        for target in stmt.targets:
            assert target.dyn_delattr_call is not None, (
                "TpyDelAttr without resolved dyn_delattr_call: sema bug")
            call = self.expressions._gen_method_call(target.dyn_delattr_call)
            parts.append(f"{indent}{call};\n")
        return "".join(parts)

    def _gen_del_var_code(self, stmt: TpyDelVar, indent: str) -> str:
        """Generate code for variable deletion (del x).

        Moves the value into a temporary that is immediately destroyed,
        releasing resources early. Move-sink is only emitted when the
        variable is the sole owner of its value:
        - Skip trivially destructible types (no-op)
        - Skip T& aliases (source still owns it)
        - Skip sources of T& aliases (alias still references it)
        - Skip pointer-locals that started as aliases (may point at source's storage)
        - Skip parameters (non-value params are const T& -- can't move from const)
        - Skip globals (other code may access it)
        Pointer-locals that own their value use std::move(*name) to deref first.
        """
        parts: list[str] = []
        for name in stmt.names:
            var_type = self.ctx.var_types.get(name)
            if var_type and var_type.is_trivially_destructible():
                continue
            if name in self.ctx.ref_bound_locals:
                continue
            if name in self.ctx.aliased_vars:
                continue
            if name in self.ctx.current_func_params:
                continue
            if name in self.ctx.global_declared_vars:
                continue
            if name in self.ctx.pointer_locals:
                if name in self.ctx.alias_names:
                    continue
                cpp_name = escape_cpp_name(name)
                parts.append(f"{indent}{{ auto __del_sink = std::move(*{cpp_name}); }}\n")
                continue
            cpp_name = escape_cpp_name(name)
            parts.append(f"{indent}{{ auto __del_sink = std::move({cpp_name}); }}\n")
        return "".join(parts)

    def _gen_aug_assign_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for an augmented assignment. Returns code to write."""
        if isinstance(stmt.target, TpyName):
            self.ctx.literal_facts.pop(stmt.target.name, None)
        # Special handling for subscript targets - use set_value() pattern
        # TypedDict subscript generates as field access, so the general path handles it
        if isinstance(stmt.target, TpySubscript) and stmt.target.typed_dict_field is None:
            return self._gen_aug_assign_subscript_code(stmt, indent)

        # In-place operator (__iadd__, __ior__, etc.) -- mutates target directly
        if inplace := stmt.resolved_inplace:
            target = self.expressions.gen_expr(stmt.target)
            if self.ctx.is_indirect_name(stmt.target):
                target = f"(*{target})"
            value = self.expressions.gen_expr_deref(stmt.value)
            # C++ can't deduce template params from bare initializer lists when
            # the function uses a two-parameter template (e.g. list_extend(T&, Container)).
            # Prefix with explicit vector type so the range overload resolves cleanly.
            receiver_type = self.ctx.get_expr_type(stmt.target)
            if isinstance(stmt.value, TpyArrayLiteral) and is_list(receiver_type):
                value = f"{self.types.type_to_cpp(receiver_type)}{value}"
            result = self.builtins.gen_call_from_fi(inplace.method, target, [value])
            return f"{indent}{result};\n"

        # Class-constant aug-assign: split receiver eval off so the qualified
        # name is a real lvalue and is emitted at most once. Going through
        # `gen_expr` for the target would wrap it in a GCC statement expression
        # (rvalue), and substituting that into `target = bin_op(target, v)`
        # would also evaluate the receiver twice.
        receiver_stmt = ""
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.class_constant_owner is not None):
            receiver_stmt, target = self.expressions.gen_class_constant_lvalue(stmt.target)
        else:
            target = self.expressions.gen_expr(stmt.target)
            # Unwrap a sema-narrowed value-Optional LHS so the synthesized
            # `target = target op value` reads the inner T, not std::optional<T>.
            target = self.expressions._maybe_unwrap_narrowed_optional(
                stmt.target, target, self.ctx.is_indirect_name(stmt.target))
        target_type = self.ctx.get_expr_type(stmt.target)
        value = self.expressions.gen_expr(stmt.value, target_type)
        # Mirror the target-side unwrap above: a proven-non-None value-Optional
        # source must feed the synthesized binop its inner value (the helper's
        # target_type guard keeps whole-optional targets bare).
        if target_type is not None:
            value = self.expressions._maybe_unwrap_narrowed_optional(
                stmt.value, value, self.ctx.is_indirect_name(stmt.value),
                target_type=target_type)
        value_type = self.types.get_resolved_type(stmt.value, target_type)

        # Special case: FixedInt += BigInt should convert BigInt to the target type
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        if is_fixed_int_type(target_type) and is_big_int_type(value_type):
            # Dereference globals before .to_fixed_check<T>() conversion
            if self.ctx.is_indirect_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_fixed_check<{target_type.to_cpp()}>()"
            value_type = target_type

        prefix = f"{indent}{receiver_stmt};\n" if receiver_stmt else ""
        # Use resolved binop from sema for augmented assignment (a += b is a = a + b)
        if binop_result := stmt.resolved_binop:
            # String += optimization: in-place append instead of allocating a new string
            if (is_str_type(target_type) or is_string_type(target_type)
                    or isinstance(target_type, PendingStrType)) and stmt.op == "+":
                return f"{prefix}{indent}{target} += {value};\n"
            result = self.expressions._gen_binop_from_result(binop_result, target, value)
            return f"{prefix}{indent}{target} = {result};\n"
        else:
            # Fallback for operators not in module system
            cpp_op = "/" if stmt.op == "//" else stmt.op
            return f"{prefix}{indent}{target} {cpp_op}= {value};\n"

    def _gen_aug_assign_subscript_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for augmented assignment to subscript targets.

        Uses set_value(container, index, get_value(container, index) op value) pattern
        for range-checked read and write. Only supported for value type elements.
        """
        if not isinstance(stmt.target, TpySubscript):
            raise CodeGenError("Expected subscript target for augmented assignment", stmt.loc)
        subscript = stmt.target
        obj = self.expressions.gen_expr(subscript.obj)
        is_indirect = self.ctx.is_indirect_name(subscript.obj)
        # Unwrap a sema-narrowed value-Optional receiver so __getitem__ /
        # __setitem__ see the inner container; mirrors _gen_assign_code.
        obj = self.expressions._maybe_unwrap_narrowed_optional(
            subscript.obj, obj, is_indirect)
        obj_type = self.types.get_resolved_type(subscript.obj)
        index_type = self.ctx.get_expr_type(subscript.index)
        # Dereference globals for subscript access
        subscript_obj = f"(*{obj})" if is_indirect else obj
        index_expr = self.expressions.gen_index_expr(
            subscript.index, index_type or INT32, obj_type=obj_type)

        # Get element type
        elem_type = obj_type.get_element_type()

        # Only allow augmented assignment on value type elements
        if elem_type and not elem_type.is_value_type():
            raise RuntimeError(
                f"Augmented assignment on container elements not supported for object types "
                f"(element type: {elem_type})"
            )

        # Generate read expression using registry lookup for __getitem__
        get_fi = self.builtins.get_type_method_fi(obj_type, "__getitem__")
        if get_fi:
            read_expr = self.builtins.gen_call_from_fi(get_fi, subscript_obj, [index_expr])
        else:
            read_expr = f"{subscript_obj}[{index_expr}]"

        value = self.expressions.gen_expr(stmt.value, elem_type)
        value_type = self.types.get_resolved_type(stmt.value, elem_type)

        # Special case: FixedInt += BigInt should convert BigInt to the element type
        if is_fixed_int_type(elem_type) and is_big_int_type(value_type):
            # Dereference globals before .to_fixed_check<T>() conversion
            if self.ctx.is_indirect_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_fixed_check<{elem_type.to_cpp()}>()"
            value_type = elem_type

        # Compute the result expression using resolved binop from sema
        if binop_result := stmt.resolved_binop:
            result_expr = self.expressions._gen_binop_from_result(binop_result, read_expr, value)
        else:
            cpp_op = "/" if stmt.op == "//" else stmt.op
            result_expr = f"{read_expr} {cpp_op} {value}"

        # Generate write using registry lookup for __setitem__
        set_fi = self.builtins.get_type_method_fi(obj_type, "__setitem__")
        if set_fi:
            code = self.builtins.gen_call_from_fi(set_fi, subscript_obj, [index_expr, result_expr])
            return f"{indent}{code};\n"
        else:
            return f"{indent}::tpy::__setitem__({subscript_obj}, {index_expr}, {result_expr});\n"

    def _try_str_inplace_append(
        self, target_name: str, target_cpp: str, value_expr: TpyExpr,
        target_type: TpyType, indent: str,
    ) -> str | None:
        """Emit x += rhs if value_expr is x + rhs on a string type, else None."""
        if not (is_str_type(target_type) or is_string_type(target_type)
                or isinstance(target_type, PendingStrType)):
            return None
        inner = value_expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        if (isinstance(inner, TpyBinOp) and inner.op == "+"
                and isinstance(inner.left, TpyName)
                and inner.left.name == target_name):
            rhs = self.expressions.gen_expr_deref(inner.right, target_type)
            return f"{indent}{target_cpp} += {rhs};\n"
        return None

    def _wrap_view_to_storage(self, ret_value: TpyExpr,
                                     ret_type: 'TpyType | None',
                                     ret_expr: str) -> str:
        """Wrap a borrow-form view (str->string_view, bytes->span) into its
        owned storage form (std::string / tpy::bytes). Shared with the
        container literal/comprehension/insert sinks via the common
        chokepoint on ExpressionGenerator."""
        return self.expressions._view_source_to_owned(ret_value, ret_type, ret_expr)

    def _is_value_optional_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression's C++ type is a value-type std::optional<T>.

        Handles TpyName (locals, params, module globals) and TpyFieldAccess
        (obj.field) via the global-aware declared-type resolver.
        """
        declared = self.expressions._declared_type_incl_globals(expr)
        return (isinstance(declared, OptionalType) and not declared.uses_pointer_repr())


    def _is_const_borrow_source(self, var_name: str, var_decl: 'TpyType | None') -> bool:
        return (isinstance(var_decl, ReadonlyType)
                or var_name in self.ctx.deep_const_borrow_params
                or var_name in self.ctx.const_ref_params)

    def _build_isinstance_init_clause(
        self, type_facts: dict[str, TpyType] | None,
    ) -> tuple[str, list[str]]:
        # Single-fact only -- multi-fact branches would need a tuple of
        # pre-bound locals, which the C++17 if-init form can't express cleanly;
        # those fall through to the per-extraction dynamic_cast.
        if not type_facts:
            return "", []
        registry = self.ctx.analyzer.registry
        # A pointer-source fact retypes the variable (`var -> Sub`); a deref-view
        # fact (key carries the deref suffix) narrows the wrapper's payload
        # without retyping the wrapper. Both pre-bind one `Sub*` for the
        # condition + reads to share; collect either kind, single-fact only.
        candidates: list[tuple[str, NominalType, TpyType, int]] = []
        for var_name, ty in type_facts.items():
            recv = parse_deref_view_key(var_name)
            if recv is not None:
                var_decl = self.ctx.lookup_var_type(recv)
                src = self.ctx.deref_dispatch_source(var_decl)
                if src is None or not isinstance(ty, NominalType):
                    continue
                candidates.append((recv, ty, var_decl, src[1]))
                continue
            var_decl = self.ctx.lookup_var_type(var_name)
            if not is_polymorphic_subclass_fact(var_decl, ty, registry):
                continue
            assert isinstance(ty, NominalType)  # gated by the predicate
            candidates.append((var_name, ty, var_decl, 0))
        if len(candidates) != 1:
            return "", []
        var_name, narrowed_type, var_decl, deref_depth = candidates[0]
        cpp_type = self.types.type_to_cpp(narrowed_type)
        const_pfx = "const " if self._is_const_borrow_source(var_name, var_decl) else ""
        ptr_local = f"__{var_name}_ptr"
        if deref_depth > 0:
            cast_arg = self.ctx.deref_view_cast_arg(var_name, deref_depth)
            source_inner = self.ctx.deref_dispatch_inner(var_decl, deref_depth)
            self.ctx.deref_view_init_locals[var_name] = ptr_local
        else:
            cast_arg = self.ctx.polymorphic_cast_arg(var_name, var_decl)
            source_inner = polymorphic_source_inner(var_decl, registry)
            self.ctx.isinstance_init_locals[var_name] = ptr_local
        cast_rhs = self.protocols.dynamic_narrow_cast_rhs(
            cpp_type, narrowed_type, source_inner, cast_arg,
            is_const=bool(const_pfx))
        init_expr = f"{const_pfx}{cpp_type}* {ptr_local} = {cast_rhs}"
        return f"{init_expr}; ", [var_name]

    def _fresh_alias_local(self, var_name: str, *, persistent: bool) -> str:
        # Default is `__{var_name}`. `persistent` is True when the emit is at
        # the same C++ scope as the caller (assert, early-return) -- where a
        # prior alias declared in the same lexical scope would collide. False
        # when the caller opened a fresh `{...}` block (if-body, while-body):
        # shadowing the outer alias is fine and produces cleaner names.
        #
        # Collision check spans the scope-global `declared_persistent_aliases`
        # set rather than `narrowed_vars` (which only holds the most-recent
        # alias per source variable -- earlier aliases like `__p` become
        # invisible after a bump to `__p_2` even though their C++ declaration
        # is still live). The caller records the chosen name in the set; the
        # set is part of LocalScopeSnap so it tracks C++ lexical scope.
        base = f"__{var_name}"
        # In a resumable frame the alias must not shadow a captured frame
        # field (e.g. `self` is the field `__self`): the cast initializer
        # reads the source by name, so a same-named alias would self-reference
        # its own uninitialized storage. Frame-field sets are empty outside
        # resumable bodies, so this is a no-op for sync codegen.
        if (base == self.ctx.generator_self_ref
                or base in self.ctx.generator_field_names):
            base = f"{base}_narrowed"
        if not persistent:
            return base
        in_use = self.ctx.declared_persistent_aliases
        if base not in in_use:
            return base
        n = 2
        while f"{base}_{n}" in in_use:
            n += 1
        return f"{base}_{n}"

    def _emit_isinstance_extractions(
        self, out: TextIO, type_facts: dict[str, TpyType],
        *, indent_extra: int = 1, persistent: bool = False,
    ) -> dict[str, str | None]:
        """Emit std::get extractions for isinstance-narrowed variables.

        Returns saved narrowed_vars entries for later restoration.
        Only emits extraction when the fact is a concrete (non-union) type.
        `indent_extra` controls how many indent levels past the current level
        to emit at: 1 (default) inside an if-block / while-block, 0 after an
        assert or at the implicit-else of an early-returning if.
        `persistent` is True when the alias must outlive the caller's emit
        block (assert / early-return): the alias-name picker bumps the suffix
        if a prior alias of the same shape is in scope. False when the caller
        opened a fresh `{...}` block (if-body / while-body / else-body) --
        shadowing the outer alias is fine.
        """
        saved: dict[str, str | None] = {}
        if not type_facts:
            return saved
        inner_indent = INDENT * (self.ctx.indent_level + indent_extra)
        for var_name, narrowed_type in type_facts.items():
            # A union fact has no single alternative to extract; a void-like
            # fact (NoneType, or the VoidType `make_union` yields when only the
            # None member remains -- e.g. the else of `isinstance(v, (int, str))`
            # on `int | str | None`) narrows to None, which has no value to bind.
            if isinstance(narrowed_type, UnionType) or is_void_like_type(narrowed_type):
                continue
            # Deref-view facts don't retype the wrapper var -- no extraction
            # local. The narrowed reads route through deref_narrowed_to (and the
            # if-init's deref_view_init_locals); the wrapper stays its own type.
            if parse_deref_view_key(var_name) is not None:
                continue
            # LiteralType narrowing: track for dead branch elimination,
            # no std::get extraction needed.
            if isinstance(narrowed_type, LiteralType):
                self.ctx.literal_facts[var_name] = narrowed_type
                continue
            # Protocol isinstance narrows the concept constraint, not the value;
            # no std::get extraction needed (the variable is already a T& ref).
            # Track the narrowed type so get_resolved_type surfaces it to
            # downstream dispatch (for-loop peephole, `in` operator, etc.).
            if is_protocol_type(narrowed_type):
                self.ctx.protocol_narrowings[var_name] = narrowed_type
                continue
            # In @overload context, the param is already the concrete type --
            # no std::get extraction needed.
            if var_name in self.ctx.overload_param_types:
                continue
            cpp_type = self.types.type_to_cpp(narrowed_type)
            var_decl = self.ctx.lookup_var_type(var_name)
            # Polymorphic source + strict-subclass narrowed_type: cast-and-cache
            # extraction. Identity narrowing (`is not None`) keeps the same
            # class and is gated out by the predicate.
            if is_polymorphic_subclass_fact(
                    var_decl, narrowed_type, self.ctx.analyzer.registry):
                # If `_gen_if` pre-bound the cast via C++17 if-init, route reads
                # through `(*__var_ptr)` directly -- no need for a separate
                # reference local that just aliases the deref. Compiler sees the
                # same object either way. For assert/while paths that don't go
                # through if-init, emit the fresh cast into a reference local.
                init_local = self.ctx.isinstance_init_locals.get(var_name)
                if init_local is not None:
                    saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                    self.ctx.narrowed_vars[var_name] = f"(*{init_local})"
                    continue
                local_name = self._fresh_alias_local(var_name, persistent=persistent)
                cast_const = "const " if self._is_const_borrow_source(var_name, var_decl) else ""
                cast_arg = self.ctx.polymorphic_cast_arg(var_name, var_decl)
                source_inner = polymorphic_source_inner(
                    var_decl, self.ctx.analyzer.registry)
                cast_rhs = self.protocols.dynamic_narrow_cast_rhs(
                    cpp_type, narrowed_type, source_inner, cast_arg,
                    is_const=bool(cast_const))
                out.write(
                    f"{inner_indent}{cast_const}{cpp_type}& {local_name} = "
                    f"*{cast_rhs};\n"
                )
                saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                self.ctx.narrowed_vars[var_name] = local_name
                if persistent:
                    self.ctx.declared_persistent_aliases.add(local_name)
                continue
            # Any narrowing (D15): the source variable is a tpy::Any cell;
            # the narrowed binding is a `const T&` borrow into its
            # contents. The outer Any survives unchanged.
            if isinstance(var_decl, AnyType):
                local_name = self._fresh_alias_local(var_name, persistent=persistent)
                if self.ctx.is_indirect_name(TpyName(var_name)):
                    var_ref = f"(*{var_name})"
                else:
                    var_ref = var_name
                out.write(
                    f"{inner_indent}const {cpp_type}& {local_name} = "
                    f"std::any_cast<const {cpp_type}&>({var_ref}.value);\n"
                )
                saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                self.ctx.narrowed_vars[var_name] = local_name
                if persistent:
                    self.ctx.declared_persistent_aliases.add(local_name)
                continue
            # std::get needs the underlying variant. Previously-extracted T&
            # aliases in narrowed_vars (from outer if-branch narrowing, match
            # binds, or inline isinstance facts) point at non-variants, so we
            # must target the original variable here.
            # A union local hoisted into the resumable frame is a
            # `frame_slot<variant<...>>`; std::get needs the variant, not the
            # slot wrapper (the same `(*name)` unwrap the name-read path uses).
            fs_deref = self.ctx.frame_slot_deref(var_name)
            if self.ctx.is_indirect_name(TpyName(var_name)):
                var_ref = f"(*{var_name})"
            elif fs_deref is not None:
                var_ref = fs_deref
            else:
                var_ref = var_name
            local_name = self._fresh_alias_local(var_name, persistent=persistent)
            # Value-type union params are const&, so std::get yields const T&.
            # Non-value union params and locals are mutable.
            var_decl_type = self.ctx.var_types.get(var_name)
            is_const = (var_name in self.ctx.current_func_params
                        and var_decl_type is not None
                        and (var_decl_type.is_value_type() or var_decl_type.needs_wrapper()))
            qualifier = "const auto&" if is_const else "auto&"
            # Pointer-variant unions: *std::get<T*>(var) or *std::get<const T*>(var)
            if var_name in self.ctx.ptr_variant_locals:
                is_const = var_name in self.ctx.const_indirect_locals
                va = VariantAccess(var_ref, None, is_ptr_variant=True, is_const=is_const)
            else:
                va = VariantAccess(var_ref, var_decl_type, is_ptr_variant=False)
            out.write(f"{inner_indent}{qualifier} {local_name} = {va.get_by_type(cpp_type, lvalue=True)};\n")
            saved[var_name] = self.ctx.narrowed_vars.get(var_name)
            self.ctx.narrowed_vars[var_name] = local_name
            if persistent:
                self.ctx.declared_persistent_aliases.add(local_name)
        return saved


    def _unpack_source_has_const_slots(self, stmt: TpyTupleUnpack) -> bool:
        """True when the unpack source has const-typed borrow slots.

        Triggered when the source is a name referring to either:
        - a const-inferred param (deep_const_borrow_params), or
        - a synthesized for-loop tuple iterating a const-bound source
          (const_storage_form_tuple_locals).
        Drives the unpack codegen to emit `const T*` / `const T&` for
        unpacked locals rather than `T*` / `T&` (which would fail to bind
        from the const slot).
        """
        if not isinstance(stmt.value, TpyName):
            return False
        return (stmt.value.name in self.ctx.deep_const_borrow_params
                or stmt.value.name in self.ctx.const_storage_form_tuple_locals)

    def _gen_tuple_unpack(self, out: TextIO, stmt: TpyTupleUnpack, indent: str) -> None:
        """Generate tuple unpacking: auto __tup_N = expr; T a = std::get<0>(...); ..."""
        # Inside try/except: intercept error_return calls with goto dispatch
        if self.ctx.try_except_label and self._get_error_return_fi(stmt.value):
            self.ctx.try_except_counter += 1
            try_tmp = f"__try_tmp_{self.ctx.try_except_counter}"
            label = self.ctx.try_except_label
            call_cpp = self._gen_error_return_call(stmt.value)
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{{\n")
            out.write(f"{indent}{INDENT}auto {try_tmp} = {call_cpp};\n")
            out.write(self._gen_error_goto(f"{indent}{INDENT}", try_tmp, label))
            out.write(f"{indent}}}\n")
            # Use unwrapped value for the rest of tuple unpacking
            unwrapped_tmp = f"(::tpy::unwrap_ref(*{try_tmp}))"
        else:
            unwrapped_tmp = None

        wrapped_to_pointer = False
        if unwrapped_tmp:
            value_expr = unwrapped_tmp
        else:
            value_expr = self.expressions.gen_expr(stmt.value)
            # A pointer-form loop var (the synthetic `__for_tup` of a
            # tuple-unpack loop aliasing a container element) renders as a
            # bare `T*` -- the bare-name deref only fires for
            # generator_optional_fields, not pointer_locals. Deref here so
            # the tuple source `tmp` binds to the live element (and the
            # per-target `&std::get<i>(tmp)` aliases it), not the pointer.
            if (self.ctx.in_generator_body and isinstance(stmt.value, TpyName)
                    and stmt.value.name in self.ctx.pointer_locals):
                value_expr = f"(*{value_expr})"
            self.ctx.temps.flush(out, indent)
            wrapped = self._maybe_wrap_storage_tuple_source(stmt, value_expr)
            wrapped_to_pointer = wrapped is not value_expr
            value_expr = wrapped

        self.ctx.unpack_counter += 1
        tmp = f"__tup_{self.ctx.unpack_counter}"
        # When value is a named variable and no elements need move semantics,
        # bind by ref to avoid copying the tuple.  Sema guarantees
        # is_ref[i] implies not is_owned[i], so if any element needs a
        # move we take the copy path instead.  Within the ref path, use
        # const only when no element needs a mutable reference (is_ref);
        # std::get on a const tuple returns const T& which can't bind
        # to T&.  Existing is_const_ref elements are unaffected -- const T&
        # binds fine from a non-const tuple.
        # tuple_to_pointer wrap returns a prvalue: bind by value, not by ref.
        # `one_shot_lift_locals` is only populated for resumable bodies, so
        # gate the read on in_generator_body -- a sync body emitted after a
        # coroutine would otherwise see stale entries (harmless, but the set
        # is meaningful only in a resumable body).
        source_is_oneshot = (self.ctx.in_generator_body
                             and isinstance(stmt.value, TpyName)
                             and stmt.value.name in self.ctx.one_shot_lift_locals)
        if (not unwrapped_tmp and not wrapped_to_pointer
                and isinstance(stmt.value, TpyName) and not any(stmt.is_owned)):
            const_kw = "" if any(stmt.is_ref) else "const "
            out.write(f"{indent}{const_kw}auto& {tmp} = {value_expr};\n")
        elif (not unwrapped_tmp and not wrapped_to_pointer
                and any(stmt.is_owned) and source_is_oneshot):
            # Consumable owned-tuple source (a one-shot `__await_lift_*` temp):
            # bind by rvalue-ref and move the owned elements out of the source,
            # rather than copy the whole tuple into `auto __tup` -- the copy is
            # deleted when an element is @nocopy / move-only (e.g. a socket).
            out.write(f"{indent}auto&& {tmp} = {value_expr};\n")
        elif (not unwrapped_tmp and not wrapped_to_pointer
                and any(stmt.is_owned)
                and self.expressions._is_last_use_movable(stmt.value)):
            # A movable owned-tuple source at its last use -- a named local or
            # an owned-tuple (`std::tuple<...>&&`) param.
            out.write(f"{indent}auto&& {tmp} = std::move({value_expr});\n")
        else:
            out.write(f"{indent}auto {tmp} = {value_expr};\n")

        source_has_const_slots = self._unpack_source_has_const_slots(stmt)
        # The source tuple's element types (not stmt.target_types, which strip
        # Own[T] -> T): only when the SOURCE element is pointer-repr does
        # `std::get<i>(tmp)` yield a `T*` borrow. An Own[T] element is stored by
        # value, so its slot is `T` (moved out), not a pointer.
        src_value_type = self.ctx.get_expr_type(stmt.value)
        src_tuple_type = (unwrap_qualifiers(src_value_type)
                          if src_value_type is not None else None)
        src_elem_types = (src_tuple_type.element_types
                          if isinstance(src_tuple_type, TupleType) else None)
        for i, name in enumerate(stmt.targets):
            if name is None:
                continue
            # An Own[T] element moved out of the source tuple is a movable
            # owned local; promote it into movable_locals so a later use moves
            # (std::move) instead of copying -- mirrors the TpyVarDecl/TpyAssign
            # promotion paths. sema_movable_locals already gates to owned,
            # non-hoisted, non-lvalue-reassigned targets.
            if name in self.ctx.sema_movable_locals:
                self.ctx.movable_locals.add(name)
            target_type = stmt.target_types[i]
            # Ref in target type means reference binding -- unwrap for C++ type
            # since the binding mode (ref/const_ref/value) is handled below.
            if isinstance(target_type, RefType):
                target_type = target_type.wrapped
            cpp_type = self.types.type_to_cpp(target_type)
            cpp_name = escape_cpp_name(name)
            get_expr = f"std::get<{i}>({tmp})"
            # Generator/coro body: an unpack target that genuinely lives in
            # the frame must be ASSIGNED, not re-declared -- a fresh C++ local
            # would shadow the field, so the value would not survive a
            # yield/await (the resume state reads the stale field). Mirrors
            # the TpyVarDecl frame-field path in `_gen_var_decl_code`; shared
            # by generators and async coroutines (both set in_generator_body).
            #
            # A target genuinely living in the frame must be ASSIGNED, never
            # re-declared. The one exception is an awaitless tuple-unpack
            # for-loop: its synthetic `__for_tup` source is emitted as a
            # C++-local `auto&& __for_tup` (registered in frame_field_shadows),
            # and the targets should stay zero-copy `T& a = ...` references --
            # frame-storing them there would needlessly copy (and break
            # reference aliasing). `generator_locals` is over-broad (it hoists
            # every local regardless of suspension), so the field-name set
            # alone can't distinguish that loop case; the discriminator is the
            # SOURCE. For-loop unpacks always source from `__for_tup` (a live
            # frame field when the loop suspends, a shadow when it doesn't), so
            # a shadow source means the awaitless loop -- declare locals there.
            # Every other source (call result, tuple literal, frame-stored
            # local) is a plain assignment whose frame-field targets must
            # persist across suspensions, mirroring `_gen_var_decl_code`.
            source_is_loop_shadow = (
                isinstance(stmt.value, TpyName)
                and stmt.value.name in self.ctx.frame_field_shadows)
            if (self.ctx.in_generator_body
                    and name in self.ctx.generator_field_names
                    and name not in self.ctx.frame_field_shadows
                    and not source_is_loop_shadow):
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                if name in self.ctx.pointer_locals:
                    # `T*` frame slot. Two sub-cases:
                    if isinstance(target_type, OptionalType):
                        # Pointer-repr Optional element: storage-form
                        # `optional<T>` -> the pointer the slot expects.
                        out.write(
                            f"{indent}{cpp_name} = "
                            f"::tpy::optional_to_ptr({get_expr});\n")
                    elif (name in self.ctx.generator_pointer_alias_locals
                          or (isinstance(stmt.value, TpyName)
                              and stmt.value.name
                              in self.ctx.borrow_form_tuple_locals)):
                        # Statement-level borrow alias (`a, b = first_two(xs)`
                        # or `a, b = t` for a stable tuple local `t`), or an
                        # unpack from a borrow-form tuple loop element (proxy
                        # iterators like dict_items).
                        # `tuple_elem_ref` normalizes either source-element
                        # shape to the live `T&` -- a borrow-tuple element
                        # (`std::tuple<T*, ...>`, from a call) is dereferenced,
                        # a value-tuple element (a frame-resident tuple local)
                        # is forwarded -- and `&unwrap_ref(...)` takes its
                        # address (a bare `&` on a `T*` element would yield
                        # `T**`).
                        out.write(
                            f"{indent}{cpp_name} = &(::tpy::unwrap_ref("
                            f"::tpy::tuple_elem_ref({get_expr})));\n")
                    else:
                        # Pointer-form for-loop unpack target: `__for_tup` is a
                        # value tuple here, so `std::get<i>(tmp)` is a value
                        # lvalue and `&std::get<i>(tmp)` is a `T*` to the live
                        # member -- mutations propagate to the source.
                        out.write(f"{indent}{cpp_name} = &({get_expr});\n")
                    continue
                if stmt.is_ref[i]:
                    get_expr = f"::tpy::unwrap_ref({get_expr})"
                if stmt.is_owned[i]:
                    get_expr = f"std::move({get_expr})"
                if name in self.ctx.generator_frame_slot_locals:
                    # frame_slot<T>: no operator=, writes go through emplace.
                    get_expr = self.types.typed_brace_init(
                        get_expr, target_type)
                    out.write(f"{indent}{cpp_name}.emplace({get_expr});\n")
                else:
                    out.write(f"{indent}{cpp_name} = {get_expr};\n")
                continue
            # Pointer-repr Optional element: slot is T*, register the local
            # in pointer_locals so subsequent reads know to deref.
            is_ptr_optional = (isinstance(target_type, OptionalType)
                               and target_type.uses_pointer_repr())
            if is_ptr_optional and stmt.is_new[i]:
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                self.ctx.pointer_locals.add(name)
                is_const = source_has_const_slots or (
                    stmt.is_const_ref
                    and i < len(stmt.is_const_ref)
                    and stmt.is_const_ref[i])
                if is_const:
                    self.ctx.const_indirect_locals.add(name)
                ptr_cpp = (target_type.to_cpp_return_const() if is_const
                           else target_type.to_cpp_return())
                out.write(f"{indent}{ptr_cpp} {cpp_name} = {get_expr};\n")
                continue
            # Plain non-value (BORROW_REF) element: the slot may be a bare `T*`
            # (concrete borrow tuple), a `val_or_ref<T>` (generic instantiation
            # with `T = Ref[U]`), or a `T&`. `tuple_elem_ref` derefs the pointer
            # case and `unwrap_ref` the val_or_ref case, so one `auto&&`
            # reference binding aliases the live element in every form -- no
            # pointer-local bookkeeping, plain `.` access; non-nullable, so no
            # optional_to_ptr / null-check.
            src_elem = (src_elem_types[i]
                        if src_elem_types is not None and i < len(src_elem_types)
                        else target_type)
            is_borrow_ref = (src_elem.value_form() is ValueForm.BORROW_REF
                             and TupleType._element_is_pointer_repr(src_elem))
            if is_borrow_ref and stmt.is_new[i]:
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                if (name in self.ctx.predecl_hoisted_vars
                        and name in self.ctx.pointer_locals):
                    # Target hoisted out of a branch/loop as a `T*` alias slot
                    # and used after it: ASSIGN it (a fresh `auto&&` would
                    # shadow the hoisted slot, leaving it disengaged for the
                    # post-construct read).
                    out.write(f"{indent}{cpp_name} = &(::tpy::unwrap_ref("
                              f"::tpy::tuple_elem_ref({get_expr})));\n")
                    continue
                # A fresh C++-local reference shadows any same-named resumable
                # frame field; suppress the (*name) frame peel.
                self.ctx.register_frame_field_shadow(name)
                out.write(f"{indent}auto&& {cpp_name} = ::tpy::unwrap_ref("
                          f"::tpy::tuple_elem_ref({get_expr}));\n")
                continue
            # Pointer-variant Union element: declare as variant<T*,...> via
            # to_ptr_variant lift. Without this the local is value-variant
            # (variant<A, B>) and subsequent uses that expect pointer-variant
            # (call args, returns) fail to convert. Mirror of the pointer-repr
            # Optional branch above.
            is_ptr_variant = self.ctx.is_ptr_variant_union(target_type)
            if is_ptr_variant and stmt.is_new[i]:
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                self.ctx.ptr_variant_locals.add(name)
                pv_cpp = self.types.type_to_cpp_ptr_variant(target_type)
                out.write(
                    f"{indent}{pv_cpp} {cpp_name} = "
                    f"::tpy::to_ptr_variant({get_expr});\n"
                )
                continue
            if stmt.is_ref[i]:
                # Unwrap val_or_ref<T> from iterator-composed tuples
                # (e.g. enumerate(map(f, xs)) yields tuple<int, val_or_ref<T>>).
                # No-op for plain T& elements from regular tuples.
                get_expr = f"::tpy::unwrap_ref({get_expr})"
            if stmt.is_owned[i]:
                # Each owned element is move-constructed into its own local out
                # of the materialized source tuple. Cheap, but for the
                # all-fresh-owned-non-reassigned case a C++17 structured binding
                # (`auto [a, b] = src;`) would drop the named temp and both
                # element moves. Not worth a parallel emit path until codegen
                # quality here matters -- structured bindings can't be
                # reassigned, frame-hoisted, or used for the borrow/optional/
                # union element forms this loop also handles.
                get_expr = f"std::move({get_expr})"

            if stmt.is_new[i]:
                # Variable pre-declared by branch/loop predecl (_emit_branch_decls)
                # -- emit assignment, not re-declaration.
                already_declared = name in self.ctx.predecl_hoisted_vars
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                if not already_declared:
                    # Fresh C++-local declaration outlives this stmt
                    # (Python scoping); no paired discard needed.
                    self.ctx.register_frame_field_shadow(name)
                if already_declared:
                    out.write(f"{indent}{cpp_name} = {get_expr};\n")
                elif stmt.is_ref[i]:
                    if name in self.ctx.reassigned_vars or name in self.ctx.hoisted_vars:
                        self.ctx.pointer_locals.add(name)
                        cv = "const " if source_has_const_slots else ""
                        if source_has_const_slots:
                            self.ctx.const_indirect_locals.add(name)
                        out.write(f"{indent}{cv}{cpp_type}* {cpp_name} = "
                                  f"&{get_expr};\n")
                    else:
                        cv = "const " if source_has_const_slots else ""
                        out.write(f"{indent}{cv}{cpp_type}& {cpp_name} = "
                                  f"{get_expr};\n")
                elif (stmt.is_const_ref and i < len(stmt.is_const_ref)
                        and stmt.is_const_ref[i]):
                    out.write(f"{indent}const {cpp_type}& {cpp_name} = "
                              f"{get_expr};\n")
                else:
                    out.write(f"{indent}{cpp_type} {cpp_name} = "
                              f"{get_expr};\n")
            else:
                if name in self.ctx.pointer_locals:
                    rebind_slot = self.ctx.rebind_slots.get(name)
                    if rebind_slot:
                        is_optional_slot = rebind_slot not in self.ctx.plain_rebind_slots
                        deref = self._ptr_from_rvalue_slot(
                            rebind_slot, get_expr, False, is_optional_slot)
                        out.write(f"{indent}{cpp_name} = {deref};\n")
                    else:
                        slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = slot
                        is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
                        if is_hoisted:
                            hoist_kw = "static " if self.ctx.slots.global_scope else ""
                            slot_opt = f"std::optional<{cpp_type}>"
                            self.ctx.pending_hoist_decls.append(
                                f"{hoist_kw}{slot_opt} {slot};\n")
                            deref = self._ptr_from_rvalue_slot(
                                slot, get_expr, False, True)
                            out.write(f"{indent}{cpp_name} = {deref};\n")
                        else:
                            self.ctx.plain_rebind_slots.add(slot)
                            static_kw = "static " if self.ctx.slots.global_scope else ""
                            out.write(f"{indent}{static_kw}{cpp_type} {slot} = "
                                      f"{get_expr};\n")
                            out.write(f"{indent}{cpp_name} = &{slot};\n")
                else:
                    out.write(f"{indent}{cpp_name} = "
                              f"{get_expr};\n")

    def _gen_with(self, out: TextIO, stmt: 'TpyWith', indent: str) -> None:
        """Generate a with statement using the unified try-with-finally shape.

        Emits per context manager:
            auto __ctx_N = <context_expr>;
            auto& x = __ctx_N.__enter__();
            try {
                <body>
                __ctx_N.__exit__();      // normal fall-through
            } catch (...) {
                __ctx_N.__exit__();
                throw;
            }

        When the as-variable name is reused across multiple with blocks
        (detected by prescan as reassigned), the variable is emitted as a
        T* pointer-local so it can be rebound without C++ redeclaration.

        The ctx and as-variable are hoisted outside the try scope so they
        remain visible after the with block (matching CPython semantics).
        Multiple context managers nest -- innermost __exit__() runs first.
        """
        self.ctx.temps.flush(out, indent)

        ctx_ids: list[int] = []
        for item in stmt.items:
            self.ctx.with_counter += 1
            n = self.ctx.with_counter
            ctx_ids.append(n)

            ctx_expr = self.expressions.gen_expr(item.context_expr)
            # A global manager already renders as `CM*`; deref so the `auto&`
            # bind sees a `CM&` and the later `.`-accesses resolve.
            if (item.manager_borrowed
                    and self.ctx.is_already_pointer_source(item.context_expr)):
                ctx_expr = f"*({ctx_expr})"
            # Borrow an lvalue manager (`auto&`) so __enter__/__exit__ act on
            # the original; own an rvalue manager (`auto`).
            ctx_bind = "auto&" if item.manager_borrowed else "auto"
            out.write(f"{indent}{ctx_bind} __ctx_{n} = {ctx_expr};\n")

            if item.target is not None:
                assert item.enter_type is not None
                name = item.target
                is_reassigned = name in self.ctx.reassigned_vars
                already_declared = name in self.ctx.declared_vars

                if already_declared:
                    if name in self.ctx.optional_locals:
                        out.write(f"{indent}{name} = __ctx_{n}.__enter__();\n")
                    else:
                        out.write(f"{indent}{name} = &(__ctx_{n}.__enter__());\n")
                elif is_reassigned and not item.enter_type.is_value_type():
                    cpp_type = self.types.type_to_cpp(item.enter_type)
                    out.write(f"{indent}{cpp_type}* {name} = &(__ctx_{n}.__enter__());\n")
                    self.ctx.pointer_locals.add(name)
                else:
                    if item.enter_type.is_value_type():
                        out.write(f"{indent}auto {name} = __ctx_{n}.__enter__();\n")
                    else:
                        out.write(f"{indent}auto& {name} = __ctx_{n}.__enter__();\n")
                        # `auto& name = ...` outlives the with stmt
                        # (Python scoping); no paired discard needed.
                        self.ctx.register_frame_field_shadow(name)

                if not already_declared:
                    self.ctx.declared_vars.add(name)
                    self.ctx.local_scope_names.add(name)
                    self.ctx.var_types[name] = item.enter_type
            else:
                out.write(f"{indent}__ctx_{n}.__enter__();\n")

        # Nest try-catch blocks outermost-to-innermost; LIFO close so
        # innermost __exit__ runs first.
        body_terminates = stmts_terminate(stmt.body)

        def emit_innermost_body(o: TextIO, body_indent: str) -> None:
            # Persistent isinstance aliases emitted inside the with-body
            # (assert / early-return) declare references in the try-block's
            # C++ scope; restore narrowed_vars + the alias-name set after
            # the body so post-with reads don't reference out-of-scope locals.
            narrowed_saved = dict(self.ctx.narrowed_vars)
            alias_saved = self.ctx.declared_persistent_aliases.copy()
            for s in stmt.body:
                self.gen_stmt(o, s)
            self.ctx.narrowed_vars = narrowed_saved
            self.ctx.declared_persistent_aliases = alias_saved

        emit_body: Callable[[TextIO, str], None] = emit_innermost_body
        layer_terminates = body_terminates
        for ctx_n, item in zip(reversed(ctx_ids), reversed(stmt.items)):
            inner_emit = emit_body
            can_suppress = item.exit_can_suppress
            takes_exc_val = item.exit_takes_exc_val

            def make_layer(inner_emit_fn, ctx_n_val, can_suppress_val,
                           takes_exc_val_val, layer_terminates_val):
                def layer(o: TextIO, body_indent: str) -> None:
                    self._emit_with_try_catch(
                        o, body_indent, inner_emit_fn,
                        ctx_n=ctx_n_val,
                        can_suppress=can_suppress_val,
                        takes_exc_val=takes_exc_val_val,
                        body_terminates=layer_terminates_val)
                return layer
            emit_body = make_layer(inner_emit, ctx_n, can_suppress,
                                   takes_exc_val, layer_terminates)
            # Once an inner layer may suppress, the outer layer's body (the
            # inner try/catch) can fall through even when the Python body
            # always raises -- so propagate False to outer layers.
            if can_suppress:
                layer_terminates = False

        emit_body(out, indent)

    def nested_def_signature(self, func: TpyFunction) -> 'tuple[str, str | None]':
        """(params_str, ret_cpp-or-None-for-void) for a nested def -- shared
        by the lambda emission and the resumable-frame member emission."""
        params = []
        for pname, ptype in func.params:
            resolved = self.types.resolve_type(ptype)
            cpp_name = escape_cpp_name(pname)
            params.append(resolved.to_cpp_param(cpp_name))
        return_type = self.types.resolve_type(func.return_type)
        ret_cpp = (None if isinstance(return_type, VoidType)
                   else self.types.type_to_cpp(return_type))
        return ", ".join(params), ret_cpp

    def gen_nested_def_body(self, out: TextIO, func: TpyFunction,
                            ret_cpp: 'str | None') -> None:
        """Emit a nested def's body statements at the current indent level.

        The emission scope isolates per-function context (finally_stack,
        try/except labels, async/generator modes, return facts) -- the
        nested def is its own function, not a block of the enclosing one.
        Local-render state (pointer/frame-slot classifications) is
        deliberately NOT reset: captured outer locals must keep their
        enclosing-scope rendering.
        """
        scope_snap = self.ctx.snapshot_local_scope()
        for pname, _ in func.params:
            self.ctx.local_scope_names.add(pname)
        self.ctx.local_scope_names.add(func.name)
        self.ctx.nested_def_locals.add(func.name)
        try:
            with self.ctx.nested_def_emission_scope(
                    func.return_type, ret_cpp, None):
                for s in func.body:
                    self.gen_stmt(out, s)
        finally:
            self.ctx.restore_local_scope(scope_snap)
            # Re-add nested def name (must survive into outer scope)
            self.ctx.nested_def_locals.add(func.name)
            self.ctx.local_scope_names.add(func.name)

    def _gen_nested_def(self, out: TextIO, stmt: TpyNestedDef, indent: str) -> None:
        """Generate a C++ lambda for a nested function definition.

        In a resumable body (async def / resumable generator) the def is
        emitted as a MEMBER FUNCTION of the frame struct instead (see
        gen_async's frame emission): locals are frame fields a lambda
        cannot capture, and the member is callable from every resume case.
        The statement position then emits nothing -- calls resolve to the
        member unqualified.
        """
        func = stmt.func
        name = escape_cpp_name(func.name)

        # in_generator_body is set exclusively by _resumable_frame_ctx and
        # spans EVERY frame emission context -- the state-machine body AND
        # the __finally_<n> helper bodies (whose emission scope does not
        # carry the per-shape in_async_coro_body/in_generator_resumable_body
        # flags, so routing on those would miss a def inside a finally).
        if self.ctx.in_generator_body:
            self.ctx.nested_def_locals.add(func.name)
            self.ctx.local_scope_names.add(func.name)
            out.write(f"{indent}// def {func.name}: frame member\n")
            return

        # Build capture list. `self` renders as `this` in the body (same
        # rule as the name renderer / _genexpr_outer_captures), so its
        # capture is the pointer -- alias semantics in every capture mode.
        captures_this = self.ctx.self_captures_this()
        if stmt.captured_names:
            if stmt.escapes:
                # Mixed capture: ref for non-value outer params,
                # move for last-use locals, value (copy) for the rest
                parts = []
                for n in stmt.captured_names:
                    if n == "self" and captures_this:
                        parts.append("this")
                        continue
                    cpp_n = escape_cpp_name(n)
                    if n in stmt.ref_captures:
                        parts.append(f"&{cpp_n}")
                    elif n in stmt.move_captures:
                        parts.append(f"{cpp_n} = std::move({cpp_n})")
                    else:
                        parts.append(cpp_n)
                capture = f"[{', '.join(parts)}]"
            else:
                refs = ", ".join(
                    "this" if (n == "self" and captures_this)
                    else f"&{escape_cpp_name(n)}"
                    for n in stmt.captured_names)
                capture = f"[{refs}]"
        else:
            capture = "[]"

        params_str, ret_cpp = self.nested_def_signature(func)
        ret_annotation = f" -> {ret_cpp}" if ret_cpp is not None else ""

        # Emit lambda header
        out.write(f"{indent}auto {name} = {capture}({params_str}){ret_annotation} {{\n")
        self.ctx.indent_level += 1
        try:
            self.gen_nested_def_body(out, func, ret_cpp)
        finally:
            self.ctx.indent_level -= 1
        out.write(f"{indent}}};\n")

    def _gen_raise(self, stmt: TpyRaise, indent: str) -> str:
        """Generate a raise statement (return-tier, throw-tier, or bare re-raise).

        Phase 20 Stage 3: throw-tier `raise <expr>` / `raise X(args)` lower
        to `<expr>.__raise__()` / `X(args).__raise__()` so the dynamic type
        is preserved through the Throwable vtable. Single path -- no
        fast-path special case for fresh construction; the macro-emitted
        (and Stage 4 codegen-emitted) override does `throw *this` at the
        concrete class, producing the same C++ throw the pre-Stage-3 path
        emitted directly. Bare `raise;` re-raise and return-tier (`raise E`
        inside an @error_return function for a ReturnException E) are
        unchanged.
        """
        # Bare raise (re-raise)
        if stmt.exception_type is None and stmt.raise_expr is None:
            if self.ctx.in_except_tier == "return":
                assert self.ctx.try_except_err_opt is not None
                expr = (f"::tpy::make_unexpected("
                        f"std::move(*{self.ctx.try_except_err_opt}))")
                return self._make_return(indent, expr)
            else:
                # Throw-tier re-raise: no expression to peel; C++ rethrows
                # the active exception via its dynamic type already.
                return f"{indent}throw;\n"

        # Expression raise (throw-tier only): desugar to `<peeled>.__raise__()`.
        # `stmt.deref_depth` (set by sema's `_analyze_raise_expr`) is the
        # number of `.__deref__()` steps to insert before the virtual call,
        # so a `raise box` where `box: Box[Throwable]` lowers to
        # `box.__deref__().__raise__()` and dispatch lands on Throwable.
        # Virtual dispatch through __raise__() preserves the dynamic type
        # for borrow-shape sources (catch bindings, polymorphic-base
        # parameters, abstract Throwable through Box). A direct `throw expr`
        # peephole would slice in those cases.
        if stmt.raise_expr is not None:
            expr = self.expressions.gen_expr_deref(stmt.raise_expr)
            deref_chain = ".__deref__()" * stmt.deref_depth
            return f"{indent}{expr}{deref_chain}.__raise__();\n"

        cpp_type = error_return_to_cpp(stmt.exception_type, self.ctx.analyzer.ctx.module_name, self.ctx.analyzer.registry)
        is_cf = is_return_exception(stmt.exception_type)

        if is_cf:
            # Return-tier: return std::unexpected (no Throwable interaction).
            if stmt.args:
                args = self._gen_raise_ctor_args(stmt)
                return self._make_return(indent, f"::tpy::make_unexpected({cpp_type}({args}))")
            return self._make_return(indent, f"::tpy::make_unexpected({cpp_type}{{}})")
        else:
            # Throw-tier constructor form `raise X(args)`. Fresh construction
            # of the exact class -- the static and dynamic types coincide,
            # so a direct `throw X(args)` is mechanically equivalent to
            # `X(args).__raise__()` (whose macro override is `throw *this`).
            # Peephole optimization documented in EXCEPTION_DESIGN.md:520+
            # -- keeps generated C++ idiomatic and avoids the extra inlined
            # virtual call in stack traces / debug info.
            # EXCEPT for @virtual_raise classes, whose __raise__ dispatches
            # (OSError's errno -> subclass mapping): there the equivalence
            # doesn't hold and the virtual hop IS the semantics.
            if stmt.args:
                args = self._gen_raise_ctor_args(stmt)
                if stmt.raise_via_virtual:
                    return f"{indent}{cpp_type}({args}).__raise__();\n"
                return f"{indent}throw {cpp_type}({args});\n"
            if stmt.raise_via_virtual:
                return f"{indent}{cpp_type}{{}}.__raise__();\n"
            return f"{indent}throw {cpp_type}{{}};\n"

    def _gen_raise_ctor_args(self, stmt: TpyRaise) -> str:
        """Lower `raise X(args)` ctor args through the shared ctor loop, so the
        raise form gets the full per-arg dispatch, not just the fallback."""
        init = stmt.resolved_ctor_init
        init_params = init.params if init else []
        ctor_mutated = (init.mutated_params if init else None) or frozenset()
        return ", ".join(
            self.expressions._gen_record_ctor_args(
                stmt.args, init_params, ctor_mutated))

    def _gen_try(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate a try/except/else/finally statement."""
        tier = stmt.tier
        if tier == "finally_only":
            self._gen_try_finally_only(out, stmt, indent)
        elif tier == "return":
            self._gen_try_return(out, stmt, indent)
        else:
            self._gen_try_throw(out, stmt, indent)

    def _push_finally(self, emit_finally: Callable[[TextIO, str], None],
                      terminates: bool) -> FinallyContext:
        """Push a finally frame onto the active stack.

        loop_depth captures len(loop_else_labels) at push time so
        break/continue can identify finally frames inside the innermost
        active loop body.

        The guard name is allocated eagerly (an exit site inside the body
        needs it while the body emits) but only declared if an exit site
        actually used it -- see FinallyContext.guard_name.
        """
        self.ctx.finally_guard_counter += 1
        guard = f"__fin_ran_{self.ctx.finally_guard_counter}"
        fctx = FinallyContext(
            emit_finally=emit_finally,
            terminates=terminates,
            loop_depth=len(self.ctx.loop_else_labels),
            guard_name=guard,
        )
        self.ctx.finally_stack.append(fctx)
        return fctx

    def _emit_except_handler_header(self, out: TextIO, handler) -> None:
        """Emit a single ` catch (...) {` clause header for `handler`.
        Caller is responsible for emitting the handler body and the
        closing `}`. Shared by sync try/except codegen and the
        async-await try/except path in `gen_async.py`.
        """
        if handler.exception_type is None:
            out.write(" catch (...) {\n")
            return
        cpp_type = error_return_to_cpp(
            handler.exception_type,
            self.ctx.analyzer.ctx.module_name,
            self.ctx.analyzer.registry)
        if handler.binding:
            binding = escape_cpp_name(handler.binding)
            out.write(f" catch (const {cpp_type}& {binding}) {{\n")
        else:
            out.write(f" catch (const {cpp_type}&) {{\n")

    def _emit_finally_chain(self, out: TextIO, indent: str,
                            stop_at: int = 0) -> bool:
        """Emit finally bodies inline from innermost down to stop_at (exclusive).

        Each finally body is emitted with the corresponding frame popped, so
        any return/break/continue inside it redirects through the outer
        frames -- not back through itself. The stack is restored on exit so
        subsequent code in the caller's scope is unaffected (relevant when
        emitting the normal-fall-through finally before popping in the
        caller).

        ``indent_level`` is temporarily synced to the ``indent`` string so
        that gen_stmt-based emit_finally callbacks (which read
        self.ctx.indent_level rather than the ``ind`` argument) emit at the
        correct depth. Callers may pass an indent that doesn't correspond
        to the current emission point (e.g. _gen_propagate_check emits a
        nested return inside an `if` body); the level is restored after.

        Returns True if any finally body terminates (raise/return) -- the
        caller must suppress its own trailing return/break/continue/throw
        in that case, since control already left.
        """
        snapshot = list(self.ctx.finally_stack)
        prev_indent_level = self.ctx.indent_level
        target_level = len(indent) // len(INDENT)
        terminated = False
        try:
            self.ctx.indent_level = target_level
            while len(self.ctx.finally_stack) > stop_at:
                fctx = self.ctx.finally_stack.pop()
                # Set before the copy runs: if the copy raises, the frame's
                # own catch must not run it again. Frames further out still
                # have a false guard, so their finallies do run -- Python's
                # unwind semantics.
                if fctx.guard_name is not None:
                    # Record the guard live: the emitter (sync catch here, or
                    # a resumable region catch, whose frames outlive one
                    # _emit_finally_chain call) declares and tests it only
                    # when its name is present in live_finally_guards.
                    self.ctx.live_finally_guards.add(fctx.guard_name)
                    out.write(f"{indent}{fctx.guard_name} = true;\n")
                fctx.emit_finally(out, indent)
                if fctx.terminates:
                    terminated = True
                    break
        finally:
            self.ctx.indent_level = prev_indent_level
            self.ctx.finally_stack = snapshot
        return terminated

    def _make_try_finally_emit(self, stmt: TpyTry) -> tuple[Callable[[TextIO, str], None], bool]:
        """Build an emit callback and terminates flag for a try/finally's body.

        The finally body is emitted several times (catch path, normal path,
        and inline at every return/break/continue site), each into its own
        C++ scope. Snapshot/restore the local-scope state around each copy
        so per-copy artifacts (narrowing aliases, rebind slots) from one
        emission can't leak into the next, which lives in a scope where
        they were never declared. Finally-body first bindings are hoisted
        by sema, so every copy assigns the same pre-declared slot.
        """
        def emit(o: TextIO, ind: str) -> None:
            snap = self.ctx.snapshot_local_scope()
            try:
                for s in stmt.finally_body:
                    self.gen_stmt(o, s)
            finally:
                self.ctx.restore_local_scope(snap)
        last = stmt.finally_body[-1] if stmt.finally_body else None
        terminates = isinstance(last, (TpyRaise, TpyReturn))
        return emit, terminates

    def _make_return(self, indent: str, expr: str | None = None) -> str:
        """Generate a return statement, walking finally chain inline first.

        Each enclosing try/with's finally body is emitted as inline C++ code
        before the actual `return ...;`. If a finally body itself terminates
        (via raise/return), the trailing return is suppressed (the body
        already transferred control).

        Python evaluates the return expression BEFORE finally bodies (and
        `with` `__exit__`) run -- and still evaluates it when a finally
        overrides the return. With frames active, capture the value into a
        temp typed with the signature spelling first ('auto' cannot hold
        the braced / std::nullopt spellings some return sites pass), then
        run the chain, then return the temp (implicit move: local).
        """
        out = io.StringIO()
        if expr is not None and self.ctx.finally_stack:
            tmp = f"__tpy_ret_{self.ctx.iter_counter}"
            self.ctx.iter_counter += 1
            ret_cpp = self.ctx.current_return_cpp or "auto"
            chain = io.StringIO()
            terminated = self._emit_finally_chain(chain, indent)
            maybe_unused = "[[maybe_unused]] " if terminated else ""
            out.write(f"{indent}{maybe_unused}{ret_cpp} {tmp} = {expr};\n")
            out.write(chain.getvalue())
            if not terminated:
                out.write(f"{indent}return {tmp};\n")
            return out.getvalue()
        terminated = self._emit_finally_chain(out, indent)
        if terminated:
            return out.getvalue()
        if expr is None:
            out.write(f"{indent}return;\n")
        else:
            out.write(f"{indent}return {expr};\n")
        return out.getvalue()

    def _deferred_return_recipe(
            self, stmt: TpyReturn,
            ret_type) -> 'tuple[str, str, str] | None':
        """(ptr_name, capture_rhs, materialize_expr) for a sema-stamped
        finally-deferred return, or None when no recipe covers the shape.

        The capture binds only a pointer to the local's storage BEFORE the
        inline finally chain; the materialize expression moves the value out
        AFTER it, so finally mutations of the local are visible in the
        returned object (CPython's pending-return is an alias). The recipe
        allocates ptr_name itself (bumping iter_counter) so the name embedded
        in materialize_expr can never desync from the one the caller
        declares. On a None result the caller must retract the auto-move
        mark sema restored -- falling through to the eager arms with the
        mark present would move the value before the finally reads it.
        """
        name_expr = self.ctx.unwrap_copy(stmt.value)
        if not isinstance(name_expr, TpyName):
            return None
        base = self.expressions.gen_expr(name_expr)
        ret_u = unwrap_ref_type(ret_type)
        if isinstance(ret_u, OptionalType) and not ret_u.uses_pointer_repr():
            # Shape B: pointer-repr Optional local into the storage-Optional
            # return slot (the ptr_to_optional_move arm, deferred).
            if not self.ctx.is_indirect_name(name_expr):
                return None
            ptr = f"__tpy_retp_{self.ctx.iter_counter}"
            self.ctx.iter_counter += 1
            return ptr, base, f"::tpy::ptr_to_optional_move({ptr})"
        if isinstance(ret_u, (OptionalType, TupleType, UnionType)):
            return None
        # Shape A: plain reference-type local into an Own[T]-style by-value
        # return slot.
        lvalue = f"(*{base})" if self.ctx.is_indirect_name(name_expr) else base
        ptr = f"__tpy_retp_{self.ctx.iter_counter}"
        self.ctx.iter_counter += 1
        return ptr, f"&({lvalue})", f"std::move(*{ptr})"

    def _retract_deferred_return_mark(self, stmt: TpyReturn) -> None:
        """No recipe for a stamped shape: drop the restored auto-move mark so
        the eager arms capture a copy -- pre-mutation value, but never a
        moved-from read -- instead of moving storage the finally chain still
        reads."""
        name_expr = self.ctx.unwrap_copy(stmt.value)
        if isinstance(name_expr, TpyName):
            self.ctx.analyzer.ctx.all_last_uses.discard(id(name_expr))

    def _gen_finally_deferred_return(self, stmt: TpyReturn, ret_type,
                                     indent: str) -> 'str | None':
        recipe = self._deferred_return_recipe(stmt, ret_type)
        if recipe is None:
            self._retract_deferred_return_mark(stmt)
            return None
        ptr, capture_rhs, materialize = recipe
        out = io.StringIO()
        chain = io.StringIO()
        terminated = self._emit_finally_chain(chain, indent)
        maybe_unused = "[[maybe_unused]] " if terminated else ""
        out.write(f"{indent}{maybe_unused}auto* {ptr} = {capture_rhs};\n")
        out.write(chain.getvalue())
        if not terminated:
            out.write(f"{indent}return {materialize};\n")
        return out.getvalue()

    def _async_ret_to_borrow(self, value: TpyExpr, ret_type: TpyType,
                             expr_cpp: str) -> str:
        """When the coro return slot is borrow form (pointer-repr Optional --
        see gen_async `_ret_cpp`), lift a storage-form return source (`return
        h.opt`) into the borrow form via the chokepoint; a borrow source
        (pointer-local) passes through. None is handled separately (it renders
        as nullptr for a pointer-repr target). The pointer-variant Union return
        is excluded -- its await-result consumer is not yet borrow-form aware.
        """
        bare = unwrap_ref_type(ret_type)
        if not (isinstance(bare, OptionalType) and bare.uses_pointer_repr()):
            return expr_cpp
        src = self.ctx.unwrap_copy(value)
        if isinstance(src, TpyCoerce):
            src = src.expr
        return self.ctx.convert(
            FormValue(expr_cpp, bare, self.ctx.source_form(src),
                      is_const=self.ctx.is_const_storage_source(src)),
            dst_type=bare, dst_form=CppForm.BORROW)

    def _async_return_value_cpp(self, stmt: TpyReturn, ret_type,
                                *, to_borrow: bool,
                                allow_move: bool = False) -> str:
        """The value render for `_make_async_return`'s three scaffolding
        sites (pending-slot store / pre-finally capture / direct ready) --
        and the resumable THIR seam's return-value chokepoint: a routed
        body renders the value from its lowered node, the scaffolding
        around it is shared skeleton either way. `allow_move` is True only
        at the direct-ready site: the pre-finally sites must copy, because
        an alias bound before the try can still read the local from the
        finally body (liveness's alias tracking does not survive the
        return arm, so the last-use fact alone cannot rule that out)."""
        leaf = self.ctx.thir_resumable_leaf
        if leaf is not None:
            # A routed body renders position-blind, replacing only the value
            # string; the _wrap_view_to_storage / _async_ret_to_borrow wraps
            # it skips are no-ops for every admitted return shape (value
            # scalars, value-opt scalars, value tuples, container storage --
            # _async_ret_to_borrow short-circuits on anything but a
            # pointer-repr Optional), and the last-use move it skips is
            # mirrored at lowering (THIRMove in
            # _lower_resumable_return_value) with the same site rule via
            # allow_move below -- so every scaffolding site stays identical.
            # Serves ReturnT terminators AND nested leaf returns
            # (THIRResumableReturn's emit hook re-enters _make_async_return,
            # which lands back here). Widening the return-shape gate must
            # revisit this seam -- see the THIRResumableBody return_values
            # contract.
            return leaf.render_return_value(stmt, allow_move=allow_move)
        if isinstance(stmt.value, TpyNoneLiteral):
            # The coroutine return slot is storage form: None needs the
            # target-typed spelling (std::nullopt / monostate), not the
            # borrow-form nullptr.
            return self.expressions.gen_expr(stmt.value, target_type=ret_type)
        bare_ret = unwrap_qualifiers(ret_type) if ret_type is not None else None
        if (isinstance(stmt.value, TpyTupleLiteral)
                and isinstance(bare_ret, TupleType)
                and not bare_ret.has_pointer_repr_element()):
            # Value-tuple literal: spell the brace-init against the return
            # slot (the sync return arm's targeting). The untargeted render
            # would spell each element's OWN type -- a None element becomes
            # an uncompilable monostate/nullptr, an Own-element name copies
            # bare where the slot demands a move. Borrow-form tuples
            # (pointer-repr elements) keep the untargeted tail.
            return self.expressions.gen_expr(stmt.value, target_type=ret_type)
        expr_cpp = self.expressions.gen_expr_deref(stmt.value)
        # A proven-non-None value-Optional NAME into a plain-T return slot
        # reads the inner value (this untargeted tail otherwise misses the
        # sync return arm's target-driven unwrap). Names only -- gen_expr_deref
        # already unwraps a narrowed FIELD (its is_narrowed_optional_field
        # arm), and a field's narrow can go stale across a suspension anyway
        # (see the yield sink's note).
        if ret_type is not None and isinstance(stmt.value, TpyName):
            expr_cpp = self.expressions._maybe_unwrap_narrowed_optional(
                stmt.value, expr_cpp, self.ctx.is_indirect_name(stmt.value),
                target_type=ret_type)
        expr_cpp = self._wrap_view_to_storage(stmt.value, ret_type, expr_cpp)
        bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret_type)))
                if ret_type is not None else None)
        if (allow_move
                and not (isinstance(bare, OptionalType)
                         and bare.uses_pointer_repr())):
            # Direct-ready storage slot: a last-use movable bare name moves
            # out (the frame is completing, nothing can read it after).
            # Every ctx.movable_locals member is already trusted for
            # mid-body call-arg moves (_maybe_move), and this site is
            # strictly safer than any call arg; the union's point is the
            # SEMA-ONLY members -- an await-result frame field is assigned,
            # never declared, so the working set alone under-covers it.
            # A coerce-wrapped source is excluded (its render is a fresh
            # conversion temp), and wants_move keeps trivial scalars bare.
            # Borrow-form slots (pointer-repr Optional) alias, not move.
            if (isinstance(stmt.value, TpyName)
                    and self.expressions._is_last_use_movable(
                        stmt.value,
                        self.ctx.sema_movable_locals
                        | self.ctx.movable_locals)):
                vt = self.ctx.get_expr_type(stmt.value)
                if vt is not None and wants_move(vt):
                    expr_cpp = f"std::move({expr_cpp})"
        if to_borrow:
            expr_cpp = self._async_ret_to_borrow(stmt.value, ret_type, expr_cpp)
        return expr_cpp

    def _make_async_return(self, stmt: TpyReturn, indent: str) -> str:
        """Lower `return v` inside an `async def` body. When a CFG-based
        finally is active, ctx state routes the return through the
        pending-return slot: save value + flag, walk finally frames
        inside the finally's body (above the boundary), transition to
        the finally entry. AsyncFinallyExit emits the actual Poll::ready
        at the finally tail. Otherwise emit Poll::ready directly after
        walking the finally chain."""
        ret_type = unwrap_ref_type(self.ctx.current_return_type)
        done_state = self.ctx.async_coro_done_state or "S_DONE"
        out = io.StringIO()
        pending_flag = self.ctx.async_pending_return_flag
        if pending_flag is not None:
            if stmt.finally_deferred_capture:
                # The CFG pending-slot store has no deferred-capture recipe;
                # keep the retract invariant so the restored move mark can
                # never turn the eager slot store into a moved-from read.
                # (Liveness suppresses stamps under a suspending finally, so
                # this is a defensive backstop.)
                self._retract_deferred_return_mark(stmt)
            pending_slot = self.ctx.async_pending_return_slot
            target_state = self.ctx.async_pending_return_target_state
            boundary = self.ctx.async_pending_return_boundary
            assert target_state is not None
            if pending_slot is not None and stmt.value is not None:
                expr_cpp = self._async_return_value_cpp(stmt, ret_type,
                                                        to_borrow=False)
                # KNOWN-WRONG eager COPY for reference payloads: a mutation
                # of the returned local by the suspending finally is
                # invisible in the returned object (CPython's pending return
                # aliases), and a @nocopy payload fails to build. A move is
                # NOT the fix (an alias in the finally would read a gutted
                # object); the deferral needs a parked discriminant at
                # AsyncFinallyExit -- tracked in BUGS.md.
                out.write(f"{indent}this->{pending_slot} = {expr_cpp};\n")
            out.write(f"{indent}this->{pending_flag} = true;\n")
            # Walk finally frames pushed by regions INSIDE the CFG-
            # based finally (above the boundary). Frames pushed by
            # regions outside run later in AsyncFinallyExit.
            terminated = self._emit_finally_chain(out, indent,
                                                   stop_at=boundary)
            if not terminated:
                out.write(f"{indent}__state = {target_state};\n")
                out.write(f"{indent}continue;\n")
            return out.getvalue()
        # Walk enclosing finally chain (try/with around an `await` or just a
        # return inside try/finally). Same machinery as sync _make_return:
        # the return value must be captured BEFORE the chain runs (Python
        # evaluates the return expression first, then finally bodies).
        ret_tmp: str | None = None
        deferred_materialize: str | None = None
        ret_cpp = self.ctx.async_coro_return_cpp or "void"
        if (not isinstance(ret_type, VoidType) and stmt.value is not None
                and self.ctx.finally_stack):
            recipe = None
            if stmt.finally_deferred_capture:
                recipe = self._deferred_return_recipe(stmt, ret_type)
                if recipe is None:
                    self._retract_deferred_return_mark(stmt)
            if recipe is not None:
                ptr, capture_rhs, deferred_materialize = recipe
                chain = io.StringIO()
                terminated = self._emit_finally_chain(chain, indent)
                maybe_unused = "[[maybe_unused]] " if terminated else ""
                out.write(f"{indent}{maybe_unused}auto* {ptr} = {capture_rhs};\n")
                out.write(chain.getvalue())
            else:
                expr_cpp = self._async_return_value_cpp(stmt, ret_type,
                                                        to_borrow=True)
                ret_tmp = f"__tpy_async_ret_{self.ctx.iter_counter}"
                self.ctx.iter_counter += 1
                chain = io.StringIO()
                terminated = self._emit_finally_chain(chain, indent)
                maybe_unused = "[[maybe_unused]] " if terminated else ""
                # For deferral-INELIGIBLE reference shapes (declared unions,
                # tuples, ...) this eager capture is a KNOWN-WRONG pre-chain
                # COPY: a finally mutation of the local is invisible in the
                # returned object (CPython's pending return aliases) --
                # tracked in BUGS.md; a move here would be worse (the
                # finally can still read the local through an alias).
                out.write(f"{indent}{maybe_unused}{ret_cpp} {ret_tmp} = {expr_cpp};\n")
                out.write(chain.getvalue())
        else:
            terminated = self._emit_finally_chain(out, indent)
        if terminated:
            return out.getvalue()
        out.write(f"{indent}__state = {done_state};\n")
        if isinstance(ret_type, VoidType):
            out.write(f"{indent}{POLL_VOID_READY_RETURN}\n")
        else:
            if stmt.value is None:
                # Non-void async def with bare return -- sema should have
                # caught this; emit a panic as a guardrail.
                out.write(
                    f"{indent}::tpy::tpy_panic(\"non-void async def used bare return\");\n")
            else:
                if deferred_materialize is not None:
                    out.write(
                        f"{indent}return ::tpystd::tpy::Poll<{ret_cpp}>::ready("
                        f"{deferred_materialize});\n")
                    return out.getvalue()
                if ret_tmp is None:
                    # Direct ready: no finally chain follows, so this is the
                    # one site where a last-use move is unconditionally safe.
                    expr_cpp = self._async_return_value_cpp(stmt, ret_type,
                                                            to_borrow=True,
                                                            allow_move=True)
                    # Bind to a local first so `std::move` has a typed source:
                    # `std::move({1, 2, 3})` (braced initializer) doesn't
                    # compile because the template parameter can't be deduced.
                    ret_tmp = "__tpy_async_ret"
                    out.write(f"{indent}{ret_cpp} {ret_tmp} = {expr_cpp};\n")
                out.write(
                    f"{indent}return ::tpystd::tpy::Poll<{ret_cpp}>::ready("
                    f"std::move({ret_tmp}));\n")
        return out.getvalue()

    def _make_generator_resumable_return(self, stmt: TpyReturn,
                                         indent: str) -> str:
        """Lower a `return` inside a generator body lowered onto the
        resumable frame. Generators reject return-with-value (sema), so
        this is always a bare `return` meaning "stop iteration": walk the
        enclosing finally chain, then (if not already terminated) set the
        done state and return StopIteration. Parallels _make_async_return
        but with the generator's `expected<T, StopIteration>` done shape.

        When a CFG-based finally is active (ctx.async_pending_return_flag),
        the return is deferred: set the pending flag, walk finallies inside
        the boundary, transition the state machine to the finally entry.
        The finally tail will emit StopIteration once it completes."""
        out = io.StringIO()
        pending_flag = self.ctx.async_pending_return_flag
        if pending_flag is not None:
            # CFG-based finally (yield-in-finally): defer the StopIteration.
            target_state = self.ctx.async_pending_return_target_state
            boundary = self.ctx.async_pending_return_boundary
            assert target_state is not None
            # No return-value slot for generators (always StopIteration).
            out.write(f"{indent}this->{pending_flag} = true;\n")
            terminated = self._emit_finally_chain(out, indent, stop_at=boundary)
            if not terminated:
                out.write(f"{indent}__state = {target_state};\n")
                out.write(f"{indent}continue;\n")
            return out.getvalue()
        terminated = self._emit_finally_chain(out, indent)
        if terminated:
            return out.getvalue()
        done_state = self.ctx.generator_resumable_done_state or "S_DONE"
        out.write(f"{indent}__state = {done_state};\n")
        out.write(f"{indent}return ::tpy::make_unexpected("
                  f"::tpy::StopIteration{{}});\n")
        return out.getvalue()

    def _make_break_continue(self, indent: str, *, is_break: bool) -> str:
        """Generate break/continue, walking finally chain inline first.

        Only finally frames pushed inside the innermost active loop body run
        before the break/continue -- frames around the loop itself stay on
        the stack and run when their try-with-finally exits normally later.
        """
        out = io.StringIO()
        loop_count = len(self.ctx.loop_else_labels)
        # Stack is monotone non-decreasing in loop_depth (deeper-nested
        # frames push later). Find the first index whose loop_depth >=
        # loop_count: those frames sit inside the innermost loop body.
        boundary = len(self.ctx.finally_stack)
        for i, fctx in enumerate(self.ctx.finally_stack):
            if fctx.loop_depth >= loop_count:
                boundary = i
                break
        terminated = self._emit_finally_chain(out, indent, stop_at=boundary)
        if terminated:
            return out.getvalue()
        if is_break:
            else_label = (self.ctx.loop_else_labels[-1]
                          if self.ctx.loop_else_labels else None)
            if else_label:
                out.write(f"{indent}goto {else_label};\n")
            elif (self.ctx.match_switch_depth > 0
                  and self.ctx.loop_break_labels):
                # A match-lowering C++ switch sits between this break and
                # the loop; a bare `break;` would exit the switch instead.
                # (`continue` is unaffected: C++ continue passes through a
                # switch to the enclosing loop.)
                if not self.ctx.loop_break_labels[-1]:
                    self.ctx.loop_break_labels[-1] = (
                        f"__loop_break_{self.ctx.iter_counter}")
                    self.ctx.iter_counter += 1
                out.write(f"{indent}goto {self.ctx.loop_break_labels[-1]};\n")
            else:
                out.write(f"{indent}break;\n")
        else:
            out.write(f"{indent}continue;\n")
        return out.getvalue()

    def _emit_with_try_catch(
            self,
            out: TextIO,
            inner: str,
            emit_body: Callable[[TextIO, str], None],
            ctx_n: int,
            can_suppress: bool,
            takes_exc_val: bool,
            body_terminates: bool,
    ) -> None:
        """Emit the `with`-specific try/catch shape (v1.5 M1).

        Layout (full, when can_suppress or takes_exc_val):
            try {
                <body>
                goto __with_exit_N;                 // normal fall-through
            } catch (::tpy::BaseException& __exc_N) {
                // can_suppress=True (return type bool):
                if (!__ctx_N.__exit__({}, &__exc_N, {})) throw;
                goto __with_after_N;                // suppressed
                // can_suppress=False (return type None):
                __ctx_N.__exit__({}, &__exc_N, {});
                throw;
            } catch (...) {
                // Foreign (non-tpy) exception -- best-effort cleanup;
                // no suppression possible because exc_val typed
                // Optional[BaseException] can't carry a foreign value.
                __ctx_N.__exit__({}, nullptr, {});
                throw;
            }
            __with_exit_N:
            __ctx_N.__exit__({}, nullptr, {});
            __with_after_N:;

        When !can_suppress and !takes_exc_val, the BaseException& catch
        and the foreign catch would emit byte-identical bodies (both:
        `__exit__({}, {}, {}); throw;`). Elide the BaseException catch
        in that case -- cleanup-only managers (the common stdlib shape)
        emit one catch instead of two.

        The fall-through __exit__ sits AFTER the catches, so a throwing
        __exit__ propagates instead of being caught by this statement's
        own catch-all (which would run it a second time). A suppressing
        catch already ran __exit__, so it jumps past that copy rather
        than falling into it. Push a finally frame for
        return/break/continue through the body (matches
        `_emit_try_with_finally`'s contract).
        """
        exc_null_arg = "nullptr" if takes_exc_val else "{}"
        exc_obj_arg = f"&__exc_{ctx_n}" if takes_exc_val else "{}"
        emit_tpy_catch = can_suppress or takes_exc_val
        exit_label = f"__with_exit_{ctx_n}"
        after_label = f"__with_after_{ctx_n}"
        # The skip only has something to skip when the body can fall
        # through to the __exit__ copy at all.
        needs_after_label = can_suppress and not body_terminates

        def emit_normal_exit(o: TextIO, ind: str) -> None:
            o.write(f"{ind}__ctx_{ctx_n}.__exit__({{}}, {exc_null_arg}, {{}});\n")

        fctx = self._push_finally(emit_normal_exit, terminates=False)

        # Buffered so an exit site inside the body can decide whether this
        # frame's guard is needed before the `try {` is written.
        body_buf = io.StringIO()
        self.ctx.indent_level += 1
        emit_body(body_buf, self.ctx.indent())
        if not body_terminates:
            # Jumping out of the try to the after-catches label leaves the
            # try scope, so any with-body local is destroyed here (before
            # __exit__ runs), matching normal nested-RAII order.
            body_buf.write(f"{self.ctx.indent()}goto {exit_label};\n")
        self.ctx.indent_level -= 1

        # The fall-through copy sits outside the try, so only the inline
        # return/break/continue copies need guarding here.
        guard = (fctx.guard_name
                 if fctx.guard_name in self.ctx.live_finally_guards else None)
        if guard is not None:
            out.write(f"{inner}bool {guard} = false;\n")
        out.write(f"{inner}try {{\n")
        out.write(body_buf.getvalue())

        # Pop the frame before emitting catches so a nested raise/return
        # inside __exit__'s body walks outer frames, not back through
        # itself.
        if emit_tpy_catch:
            out.write(f"{inner}}} catch (::tpy::BaseException& __exc_{ctx_n}) {{\n")
            self.ctx.indent_level += 1
            self.ctx.finally_stack.pop()
            catch_ind = self.ctx.indent()
            # An exit-site __exit__ that raised IS the cleanup's own
            # exception: never re-call __exit__ with it, and never offer it
            # to __exit__ for suppression.
            if guard is not None:
                out.write(f"{catch_ind}if ({guard}) throw;\n")
            if can_suppress:
                out.write(
                    f"{catch_ind}if (!__ctx_{ctx_n}.__exit__({{}}, "
                    f"{exc_obj_arg}, {{}})) throw;\n")
                if needs_after_label:
                    out.write(f"{catch_ind}goto {after_label};\n")
            else:
                out.write(
                    f"{catch_ind}__ctx_{ctx_n}.__exit__({{}}, "
                    f"{exc_obj_arg}, {{}});\n")
                out.write(f"{catch_ind}throw;\n")
            self.ctx.indent_level -= 1
            out.write(f"{inner}}} catch (...) {{\n")
        else:
            out.write(f"{inner}}} catch (...) {{\n")
            self.ctx.finally_stack.pop()
        self.ctx.indent_level += 1
        catch_ind = self.ctx.indent()
        if guard is not None:
            out.write(f"{catch_ind}if ({guard}) throw;\n")
        emit_normal_exit(out, catch_ind)
        out.write(f"{catch_ind}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{inner}}}\n")
        if not body_terminates:
            out.write(f"{inner}{exit_label}:\n")
            emit_normal_exit(out, inner)
        if needs_after_label:
            out.write(f"{inner}{after_label}:;\n")

    def _emit_try_with_finally(
            self,
            out: TextIO,
            inner: str,
            stmt: TpyTry,
            emit_body: Callable[[TextIO, str], None],
            emit_finally: Callable[[TextIO, str], None],
            finally_terminates: bool,
    ) -> None:
        """Emit the unified `try { body } catch (...) { F; throw; } F;` shape.

        ``emit_body`` is invoked with the finally frame on the stack so
        return/break/continue inside the body walk it via _emit_finally_chain.
        ``emit_finally`` is called twice -- once on the catch path with the
        frame popped, once on the normal-fall-through path. When the finally
        body itself terminates, the trailing `throw;` after the catch path
        is suppressed; when the try body unconditionally terminates on every
        path, the normal-path emission is also skipped (no fall-through to
        worry about).

        That elision is derived here from ``stmt`` rather than passed in: it
        must describe only what ``emit_body`` emits (the try body + handlers).
        Asking whether the whole statement terminates folds in the finally's
        own termination and elides the fall-through copy of an always-raising
        finally, so it never runs.

        The body emits into a buffer first: an exit site inside it decides
        whether this frame's guard is needed, and the guard has to be
        declared before the `try {` that the body follows.
        """
        body_terminates = try_terminates_ignoring_finally(stmt)
        fctx = self._push_finally(emit_finally, finally_terminates)

        body_buf = io.StringIO()
        self.ctx.indent_level += 1
        emit_body(body_buf, self.ctx.indent())
        self.ctx.indent_level -= 1

        guard = (fctx.guard_name
                 if fctx.guard_name in self.ctx.live_finally_guards else None)
        if guard is not None:
            out.write(f"{inner}bool {guard} = false;\n")
        out.write(f"{inner}try {{\n")
        out.write(body_buf.getvalue())
        out.write(f"{inner}}} catch (...) {{\n")
        self.ctx.indent_level += 1
        # Pop the frame so a raise/return inside the finally body redirects
        # through the OUTER frames, not back through itself. The same pop
        # also affects the normal-path emission below.
        self.ctx.finally_stack.pop()
        catch_indent = self.ctx.indent()
        if guard is not None:
            out.write(f"{catch_indent}if (!{guard}) {{\n")
            self.ctx.indent_level += 1
            emit_finally(out, self.ctx.indent())
            self.ctx.indent_level -= 1
            out.write(f"{catch_indent}}}\n")
            # Always rethrow behind a guard: the guarded-true path reaches
            # here carrying the exit-site copy's own exception, which must
            # propagate even when the finally body itself terminates.
            out.write(f"{catch_indent}throw;\n")
        else:
            emit_finally(out, catch_indent)
            if not finally_terminates:
                out.write(f"{catch_indent}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{inner}}}\n")

        # Normal-path finally: emit only if the body might fall through.
        if not body_terminates:
            emit_finally(out, inner)

    def _gen_try_finally_only(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate try/finally with no except handlers.

        Unified shape (no goto, no __retval):
            {
                try {
                    <try body>
                } catch (...) {
                    <finally body>
                    throw;
                }
                <finally body>   // normal fall-through, omitted if body terminates
            }

        return/break/continue inside the try body emit their own inline
        finally call before the actual exit (see _make_return /
        _make_break_continue). The catch-path finally runs with the frame
        popped so internal raise/return redirect to outer frames.
        """
        out.write(f"{indent}{{\n")
        self.ctx.indent_level += 1
        inner = self.ctx.indent()

        emit_finally, terminates = self._make_try_finally_emit(stmt)

        def emit_body(o: TextIO, body_indent: str) -> None:
            # Persistent isinstance aliases declared inside the try body
            # live in the C++ `try { ... }` scope; restore narrowed_vars +
            # the alias-name set after the body so the catch/finally and
            # post-try code don't reference out-of-scope locals.
            narrowed_saved = dict(self.ctx.narrowed_vars)
            alias_saved = self.ctx.declared_persistent_aliases.copy()
            for s in stmt.try_body:
                self.gen_stmt(o, s)
            self.ctx.narrowed_vars = narrowed_saved
            self.ctx.declared_persistent_aliases = alias_saved

        self._emit_try_with_finally(
            out, inner, stmt, emit_body, emit_finally,
            finally_terminates=terminates)

        self.ctx.indent_level -= 1
        out.write(f"{indent}}}\n")

    def _gen_try_return(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate return-tier try/except (goto-based error dispatch).

        When finally is present, the entire try/except block is wrapped in
        the unified try-with-finally so throw-tier exceptions that escape
        the inner code still trigger cleanup.
        """
        handler = stmt.handlers[0]
        self.ctx.try_except_counter += 1
        n = self.ctx.try_except_counter
        except_label = f"__except_{n}"
        after_label = f"__after_try_{n}"
        has_finally = bool(stmt.finally_body)

        out.write(f"{indent}{{\n")
        self.ctx.indent_level += 1
        inner = self.ctx.indent()

        # Emit std::optional<E> for except binding
        err_opt_var: str | None = None
        prev_err_opt = self.ctx.try_except_err_opt
        if handler.binding:
            err_opt_var = f"__err_opt_{n}"
            cpp_err_type = error_return_to_cpp(
                qualify_exception_name(handler.exception_type,
                                       self.ctx.analyzer.registry,
                                       self.ctx.analyzer.ctx.module_name),
                self.ctx.analyzer.ctx.module_name,
                self.ctx.analyzer.registry)
            out.write(f"{inner}std::optional<{cpp_err_type}> {err_opt_var};\n")
            self.ctx.try_except_err_opt = err_opt_var

        def emit_try_except(o: TextIO, body_indent: str) -> None:
            br_snap = self.ctx.snapshot_local_scope()
            prev_label = self.ctx.try_except_label
            self.ctx.try_except_label = except_label

            for s in stmt.try_body:
                self.gen_stmt(o, s)

            self.ctx.try_except_label = prev_label

            if stmt.else_body:
                o.write(f"{body_indent}// else:\n")
                for s in stmt.else_body:
                    self.gen_stmt(o, s)

            o.write(f"{body_indent}goto {after_label};\n")

            exc_display = handler.exception_type or "..."
            o.write(f"{body_indent}// except {exc_display}:\n")
            o.write(f"{body_indent}{except_label}:;\n")

            self.ctx.restore_local_scope(br_snap)

            prev_except_tier = self.ctx.in_except_tier
            self.ctx.in_except_tier = "return"
            if handler.binding and err_opt_var:
                binding = escape_cpp_name(handler.binding)
                o.write(f"{body_indent}{{\n")
                self.ctx.indent_level += 1
                inner2 = self.ctx.indent()
                o.write(f"{inner2}auto& {binding} = *{err_opt_var};\n")
                for s in handler.body:
                    self.gen_stmt(o, s)
                self.ctx.indent_level -= 1
                o.write(f"{body_indent}}}\n")
            else:
                for s in handler.body:
                    self.gen_stmt(o, s)
            self.ctx.in_except_tier = prev_except_tier

            o.write(f"{body_indent}{after_label}:;\n")

        if has_finally:
            emit_finally, terminates = self._make_try_finally_emit(stmt)
            self._emit_try_with_finally(
                out, inner, stmt, emit_try_except, emit_finally,
                finally_terminates=terminates)
        else:
            emit_try_except(out, inner)

        self.ctx.try_except_err_opt = prev_err_opt
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}\n")

    def _gen_try_throw(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate throw-tier try/except (C++ try/catch).

        When finally is present, the inner try/except is wrapped in the
        unified try-with-finally so re-raises and exceptions from handler
        bodies still trigger cleanup.
        """
        has_finally = bool(stmt.finally_body)

        out.write(f"{indent}{{\n")
        self.ctx.indent_level += 1
        inner = self.ctx.indent()

        has_else = bool(stmt.else_body)
        if has_else:
            self.ctx.try_except_counter += 1
            after_else_label = f"__after_else_{self.ctx.try_except_counter}"
        else:
            after_else_label = ""

        def emit_try_except(o: TextIO, body_indent: str) -> None:
            # Each `{...}` block (try body, each handler body, else body) is
            # its own C++ scope. Persistent isinstance aliases declared inside
            # one block aren't visible in siblings, so restore narrowed_vars +
            # the alias-name set between blocks.
            outer_narrowed = dict(self.ctx.narrowed_vars)
            outer_aliases = self.ctx.declared_persistent_aliases.copy()
            o.write(f"{body_indent}try {{\n")
            self.ctx.indent_level += 1
            for s in stmt.try_body:
                self.gen_stmt(o, s)
            self.ctx.indent_level -= 1
            self.ctx.narrowed_vars = dict(outer_narrowed)
            self.ctx.declared_persistent_aliases = outer_aliases.copy()
            o.write(f"{body_indent}}}")

            prev_except_tier = self.ctx.in_except_tier
            for h in stmt.handlers:
                self._emit_except_handler_header(o, h)
                self.ctx.indent_level += 1
                self.ctx.in_except_tier = "throw"
                for s in h.body:
                    self.gen_stmt(o, s)
                if has_else:
                    o.write(f"{self.ctx.indent()}goto {after_else_label};\n")
                self.ctx.in_except_tier = prev_except_tier
                self.ctx.indent_level -= 1
                self.ctx.narrowed_vars = dict(outer_narrowed)
                self.ctx.declared_persistent_aliases = outer_aliases.copy()
                o.write(f"{body_indent}}}")

            o.write("\n")

            if has_else:
                o.write(f"{body_indent}// else:\n")
                for s in stmt.else_body:
                    self.gen_stmt(o, s)
                self.ctx.narrowed_vars = dict(outer_narrowed)
                self.ctx.declared_persistent_aliases = outer_aliases.copy()
                o.write(f"{body_indent}{after_else_label}:;\n")

        if has_finally:
            emit_finally, terminates = self._make_try_finally_emit(stmt)
            self._emit_try_with_finally(
                out, inner, stmt, emit_try_except, emit_finally,
                finally_terminates=terminates)
        else:
            emit_try_except(out, inner)

        self.ctx.indent_level -= 1
        out.write(f"{indent}}}\n")

    def _gen_error_goto(self, indent: str, tmp: str, label: str) -> str:
        """Generate the if-not-has_value goto, with optional error capture for 'as e'."""
        err_opt = self.ctx.try_except_err_opt
        if err_opt:
            return (f"{indent}if (!{tmp}.has_value()) "
                    f"{{ {err_opt} = std::move({tmp}.error()); goto {label}; }}\n")
        return f"{indent}if (!{tmp}.has_value()) goto {label};\n"

    def _gen_error_return_stmt_block(self, call_cpp: str, indent: str) -> str:
        """Wrap a fallible call as a statement block with the appropriate
        error-handling: goto-except (in try), propagate (in @error_return),
        or panic (top-level). Discards the success value."""
        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"
        if self.ctx.try_except_label:
            check = self._gen_error_goto(f"{indent}{INDENT}", tmp, self.ctx.try_except_label)
        elif self.ctx.current_error_return:
            check = self._gen_propagate_check(f"{indent}{INDENT}", tmp)
        else:
            check = (f"{indent}{INDENT}if (!{tmp}.has_value()) "
                     f"::tpy::tpy_panic(\"unhandled error return\");\n")
        return (f"{indent}{{\n"
                f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
                f"{check}"
                f"{indent}}}\n")

    def _gen_propagate_check(self, indent: str, tmp: str) -> str:
        """Emit the propagate-out check for an @error_return call result.

        When inside a try-with-finally, the propagate-out path must run any
        active finally bodies before returning the unexpected value -- the
        unified _make_return walks the finally stack inline. The bare-return
        fast-path (no finally active) keeps the single-line shape."""
        if not self.ctx.finally_stack:
            return (f"{indent}if (!{tmp}.has_value()) "
                    f"return ::tpy::make_unexpected({tmp}.error());\n")
        body = self._make_return(
            indent + INDENT, f"::tpy::make_unexpected({tmp}.error())")
        return f"{indent}if (!{tmp}.has_value()) {{\n{body}{indent}}}\n"

    def _gen_error_return_var_decl(self, stmt: TpyVarDecl, indent: str) -> str:
        """Generate a variable declaration where the init is an @error_return call.

        Emits:
            auto __try_tmp_N = call();
            if (!__try_tmp_N.has_value()) goto __except_N;
            T var = *__try_tmp_N;
        """
        assert stmt.init is not None
        assert self.ctx.try_except_label is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"
        label = self.ctx.try_except_label

        call_cpp = self._gen_error_return_call(stmt.init)
        cpp_name = escape_cpp_name(stmt.name)

        fi = self._get_error_return_fi(stmt.init)
        var_type = fi.return_type if fi else stmt.type
        aliases = self._error_return_result_aliases(stmt.init)
        out = self._error_return_decl_prefix(stmt, var_type, indent, aliases)

        out += f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_error_goto(f"{indent}{INDENT}", tmp, label)
        out += self._error_return_assign_to_name(stmt.name, tmp, f"{indent}{INDENT}", aliases)
        out += f"{indent}}}\n"

        return out

    @staticmethod
    def _error_return_success_expr(tmp: str) -> str:
        """The expected temp dies at the end of the emitted block, so owned
        payloads must move out; unwrap_ref_move keeps val_or_ref (borrow)
        payloads as plain lvalue references so a borrowed source is never
        moved from.
        """
        return f"::tpy::unwrap_ref_move(*{tmp})"

    def _error_return_result_aliases(self, expr: 'TpyExpr') -> bool:
        """True when the @error_return callee returns a borrow (the expected
        payload is a val_or_ref pointer to storage that outlives the call) --
        the unwrap must alias that storage, not copy it (CPython mutation
        visibility). Same value-category predicate the non-error_return
        binding paths use."""
        inner = expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        fi = self._get_error_return_fi(inner)
        if fi is None:
            return False
        return self.ctx._call_returns_cpp_ref(fi)

    def _error_return_decl_prefix(self, stmt: TpyVarDecl, var_type: 'TpyType | None',
                                  indent: str, aliases: bool) -> str:
        """Pre-declare the bound local before the unwrap block (the goto /
        early return would cross an initialized declaration). A borrow-
        aliasing result declares a pointer that will point at the live
        source instead of owned storage."""
        if stmt.name in self.ctx.declared_vars or not var_type:
            return ""
        self.ctx.declared_vars.add(stmt.name)
        cpp_name = escape_cpp_name(stmt.name)
        if aliases:
            const_pfx = "const " if self._is_const_indirect(var_type, stmt.init, stmt) else ""
            if const_pfx:
                self.ctx.const_indirect_locals.add(stmt.name)
            self.ctx.pointer_locals.add(stmt.name)
            bare = unwrap_readonly(unwrap_ref_type(var_type))
            return f"{indent}{const_pfx}{bare.to_cpp()}* {cpp_name};\n"
        return f"{indent}{unwrap_ref_type(var_type).to_cpp()} {cpp_name};\n"

    def _error_return_target_assign(self, target: 'TpyExpr', tmp: str, indent: str,
                                    aliases: bool = False) -> str:
        """Assign an unwrapped @error_return result to an assignment target."""
        if isinstance(target, TpyName):
            return self._error_return_assign_to_name(target.name, tmp, indent, aliases)
        return f"{indent}{self.expressions.gen_expr(target)} = {self._error_return_success_expr(tmp)};\n"

    def _error_return_assign_to_name(self, name: str, tmp: str, indent: str,
                                     aliases: bool = False) -> str:
        cpp_name = escape_cpp_name(name)
        if aliases and name in self.ctx.pointer_locals:
            # Borrow result: point at the live source the val_or_ref payload
            # wraps. Owned storage (or the rebind slot) would copy and sever
            # the alias.
            return f"{indent}{cpp_name} = &(::tpy::unwrap_ref(*{tmp}));\n"
        # A pointer-repr Optional / pointer local needs its result materialized
        # into the rebind slot and re-pointed -- a direct `T* = T` is ill-formed.
        # unwrap_ref_move already carries the value category (T&& owned, T&
        # borrow), so no extra std::move: it would steal from a borrowed source.
        value = self._error_return_success_expr(tmp)
        slot = self.ctx.rebind_slots.get(name)
        if name in self.ctx.pointer_locals and slot is not None:
            is_optional_slot = slot not in self.ctx.plain_rebind_slots
            rhs = self._ptr_from_rvalue_slot(
                slot, value, is_opt_field=False,
                is_optional_slot=is_optional_slot)
            return f"{indent}{cpp_name} = {rhs};\n"
        return f"{indent}{cpp_name} = {value};\n"

    def _gen_error_return_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate an assignment where the RHS is an @error_return call.

        Emits:
            auto __try_tmp_N = call();
            if (!__try_tmp_N.has_value()) goto __except_N;
            target = *__try_tmp_N;
        """
        assert self.ctx.try_except_label is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"
        label = self.ctx.try_except_label

        call_cpp = self._gen_error_return_call(stmt.value)

        out = f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_error_goto(f"{indent}{INDENT}", tmp, label)
        out += self._error_return_target_assign(
            stmt.target, tmp, f"{indent}{INDENT}",
            self._error_return_result_aliases(stmt.value))
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_propagate_var_decl(self, stmt: TpyVarDecl, indent: str) -> str:
        """Generate a variable declaration with auto-propagation.

        When an @error_return(E) function calls another @error_return(E) function
        outside a try/except, errors propagate automatically via early return.
        """
        assert stmt.init is not None
        assert self.ctx.current_error_return is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.init)
        cpp_name = escape_cpp_name(stmt.name)

        fi = self._get_error_return_fi(stmt.init)
        var_type = fi.return_type if fi else stmt.type
        aliases = self._error_return_result_aliases(stmt.init)
        out = self._error_return_decl_prefix(stmt, var_type, indent, aliases)

        out += f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_propagate_check(f"{indent}{INDENT}", tmp)
        out += self._error_return_assign_to_name(stmt.name, tmp, f"{indent}{INDENT}", aliases)
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_propagate_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate an assignment with auto-propagation."""
        assert self.ctx.current_error_return is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.value)

        out = f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_propagate_check(f"{indent}{INDENT}", tmp)
        out += self._error_return_target_assign(
            stmt.target, tmp, f"{indent}{INDENT}",
            self._error_return_result_aliases(stmt.value))
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_unwrap_var_decl(self, stmt: TpyVarDecl, indent: str) -> str:
        """Generate a variable declaration with panic-on-error unwrap (top-level)."""
        assert stmt.init is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.init)
        cpp_name = escape_cpp_name(stmt.name)

        fi = self._get_error_return_fi(stmt.init)
        var_type = fi.return_type if fi else stmt.type
        aliases = self._error_return_result_aliases(stmt.init)
        out = self._error_return_decl_prefix(stmt, var_type, indent, aliases)

        out += f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += f"{indent}{INDENT}if (!{tmp}.has_value()) ::tpy::tpy_panic(\"unhandled error return\");\n"
        out += self._error_return_assign_to_name(stmt.name, tmp, f"{indent}{INDENT}", aliases)
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_unwrap_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate an assignment with panic-on-error unwrap (top-level)."""
        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.value)

        out = f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += f"{indent}{INDENT}if (!{tmp}.has_value()) ::tpy::tpy_panic(\"unhandled error return\");\n"
        out += self._error_return_target_assign(
            stmt.target, tmp, f"{indent}{INDENT}",
            self._error_return_result_aliases(stmt.value))
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_call(self, expr: TpyExpr) -> str:
        """Generate an error_return call, suppressing expression-level unwrap.

        Statement-level handlers call this instead of gen_expr() directly
        so that the top-level call emits the raw std::expected (for the
        handler to unwrap), while nested error_return calls in arguments
        still get unwrapped via statement expressions.
        """
        self.ctx.error_return_stmt_handled = True
        return self.expressions.gen_expr(expr)

    def _get_error_return_fi(self, expr: TpyExpr) -> 'FunctionInfo | None':
        """Return FunctionInfo if expr is an @error_return call, else None."""
        if isinstance(expr, TpyCoerce):
            return self._get_error_return_fi(expr.expr)
        fi = None
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            fi = getattr(expr, 'resolved_function_info', None)
        if fi and fi.error_return_type:
            return fi
        return None

    def _gen_assert_throw(self, out: TextIO, stmt: TpyAssert, indent: str) -> str:
        """Generate the assertion-failure call for an assert statement."""
        if stmt.message is None:
            return '::tpy::raise_assertion_error()'
        if isinstance(stmt.message, TpyStrLiteral):
            msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
            return f'::tpy::raise_assertion_error("{msg}")'
        msg_expr = self.expressions.gen_expr(stmt.message)
        self.ctx.temps.flush(out, indent)
        return f'::tpy::raise_assertion_error({msg_expr})'

    def _gen_assert(self, out: TextIO, stmt: TpyAssert, indent: str) -> None:
        """Generate an assert statement with optional isinstance union narrowing."""
        # Constant-fold trivially-known assertions (no temps to flush).
        if isinstance(stmt.condition, TpyBoolLiteral):
            if stmt.condition.value:
                return
            throw = self._gen_assert_throw(out, stmt, indent)
            out.write(f'{indent}{throw};\n')
            return
        if isinstance(stmt.condition, TpyNoneLiteral):
            throw = self._gen_assert_throw(out, stmt, indent)
            out.write(f'{indent}{throw};\n')
            return
        bool_cond = self.expressions.gen_truthy_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        if stmt.message is None or isinstance(stmt.message, TpyStrLiteral):
            throw = self._gen_assert_throw(out, stmt, indent)
            out.write(f'{indent}if (!({bool_cond})) {throw};\n')
        else:
            # Evaluate message inside the if block (lazy, per Python semantics)
            inner = indent + "    "
            out.write(f'{indent}if (!({bool_cond})) {{\n')
            throw = self._gen_assert_throw(out, stmt, inner)
            out.write(f'{inner}{throw};\n')
            out.write(f'{indent}}}\n')
        # Emit std::get<T> extractions for isinstance-narrowed union variables.
        # Unlike if-branch narrowing, assert narrowing persists for the rest of scope,
        # so we do NOT call ctx.restore_narrowed_vars.
        self._emit_isinstance_extractions(
            out, stmt.then_type_facts, indent_extra=0, persistent=True)


    def _condition_static_true(self, condition: TpyExpr) -> bool:
        """True when sema folded `condition` to a constant `True` (e.g. an
        isinstance on a variable already narrowed to the checked type). The
        branch is then unconditionally taken, so its implicit-else fall-through
        is dead -- post-narrowing there would extract a member the enclosing
        flow already excluded (wrong type), so it must be skipped."""
        me = getattr(condition, "macro_expansion", None)
        return isinstance(me, TpyBoolLiteral) and me.value is True

    def _gen_if(self, out: TextIO, stmt: TpyIf, indent: str,
                emit_post_narrowing: bool = True,
                _skip_source_comment: bool = False) -> None:
        """Generate an if/elif/else chain as flat C++ if/else if/else."""
        # Collect the elif chain into a flat list of branches.
        # An elif is else_body == [TpyIf(...)] where the inner if has the
        # same column as the outer (genuinely nested else: if has deeper col).
        # We also only flatten when intermediate else_type_facts have no
        # concrete extractions (all union/none types).
        chain: list[TpyIf] = []
        current = stmt
        while True:
            chain.append(current)
            if (len(current.else_body) == 1
                    and isinstance(current.else_body[0], TpyIf)
                    and self._is_elif(current, current.else_body[0])
                    and not self._has_concrete_isinstance_facts(current.else_type_facts)):
                current = current.else_body[0]
            else:
                break

        # --- @overload dead branch elimination ---
        # When generating specialized overload code, isinstance checks on
        # parameters with known concrete types can be resolved statically.
        # Also applies to literal equality checks in literal specializations.
        if self.ctx.overload_param_types or self.ctx.literal_overload_facts:
            if self._gen_if_overload_specialized(out, chain, indent):
                return

        # Regular path: emit the `if` condition as a source comment.
        # _skip_source_comment is set by the elif-with-temps recursive path
        # which already emitted the comment before calling us.
        if not _skip_source_comment:
            self.ctx.emit_source_comment(out, stmt.loc, indent)

        # Pre-declare variables first declared inside branches (all levels).
        # Inner elif branch_decls are typically subsets of the outer's and
        # get skipped by the declared_vars check, but we emit them all for
        # correctness.
        for node in chain:
            self._emit_branch_decls(out, node, indent)

        # Snapshot after branch-decl hoisting so each branch starts with only
        # pre-hoisted vars visible (pointer-local slots created inside one branch
        # must not bleed into sibling branches).
        br_snap = self.ctx.snapshot_local_scope()

        # Emit if / else if / else chain
        for i, node in enumerate(chain):
            init_vars: list[str] = []
            # Nullable protocol param narrowing: replace runtime `x != nullptr`
            # with compile-time `if constexpr (!std::same_as<T_x, nullptr_t>)`.
            # The pointer is guaranteed non-null for real types (call site passes &expr),
            # so the constexpr check alone is sufficient and avoids a redundant branch.
            constexpr_guards = self._get_nullproto_constexpr_guards(node.condition)

            if i > 0:
                self.ctx.emit_source_comment(out, node.loc, indent)

            if constexpr_guards:
                # Replace the runtime condition with if constexpr
                guard_conds = " && ".join(
                    f"!std::same_as<T_{gvar}, std::nullptr_t>" for gvar in constexpr_guards
                )
                if i == 0:
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if constexpr ({guard_conds}) {{\n")
                elif not self.ctx.temps._pending and not self.ctx.temps._pending_named:
                    out.write(f"{indent}}} else if constexpr ({guard_conds}) {{\n")
                else:
                    self.ctx.temps._pending.clear()
                    out.write(f"{indent}}} else {{\n")
                    self.ctx.indent_level += 1
                    self.ctx.temps.flush(out, self.ctx.indent())
                    self._gen_if(out, node, self.ctx.indent(),
                                emit_post_narrowing=False,
                                _skip_source_comment=True)
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")
                    return
            else:
                # C++17 if-init for polymorphic isinstance: pre-bind the cast
                # local so the condition + the cast-and-cache extraction share
                # one dynamic_cast. Skip for the fallback-elif path (recursive
                # _gen_if below) -- the init local would never be emitted but
                # downstream readers would still consult isinstance_init_locals.
                will_emit_inline = (
                    i == 0
                    or (not self.ctx.temps._pending and not self.ctx.temps._pending_named)
                )
                if will_emit_inline:
                    init_clause, init_vars = self._build_isinstance_init_clause(
                        node.then_type_facts)
                else:
                    init_clause = ""
                probe_cp = self.ctx.temps.probe_checkpoint()
                cond = self.expressions.gen_truthy_expr(node.condition)
                is_constexpr = self._is_protocol_isinstance_condition(node.condition)
                if_kw = "if constexpr" if is_constexpr else "if"
                if i == 0:
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{if_kw} ({init_clause}{cond}) {{\n")
                elif not self.ctx.temps._pending and not self.ctx.temps._pending_named:
                    else_kw = "else if constexpr" if is_constexpr else "else if"
                    out.write(f"{indent}}} {else_kw} ({init_clause}{cond}) {{\n")
                else:
                    # Elif condition produced temp/walrus vars -- can't use
                    # flat else-if; nest in an else block and let recursive
                    # _gen_if regenerate the condition in a flushable
                    # position. A temp-only probe render is discarded
                    # wholesale (counter included), so the regeneration
                    # reissues the same __tmp_N names. Walrus pre-decls
                    # can't be rolled back (their registry side effects
                    # persist); they are flushed inside the else block
                    # instead (walrus_pre_declared prevents re-creation).
                    if self.ctx.temps.has_named_since(probe_cp):
                        self.ctx.temps._pending.clear()
                    else:
                        self.ctx.temps.rollback_discarded(probe_cp)
                    out.write(f"{indent}}} else {{\n")
                    self.ctx.indent_level += 1
                    self.ctx.temps.flush(out, self.ctx.indent())
                    self._gen_if(out, node, self.ctx.indent(),
                                emit_post_narrowing=False,
                                _skip_source_comment=True)
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")
                    return

            lit_snap = self.ctx.save_literal_facts()
            proto_snap = self.ctx.save_protocol_narrowings()
            then_saved = self._emit_isinstance_extractions(out, node.then_type_facts)

            self.ctx.indent_level += 1
            for s in node.then_body:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, node.then_body, self.ctx.indent())
            self.ctx.indent_level -= 1

            self.ctx.restore_narrowed_vars(then_saved)
            self.ctx.restore_protocol_narrowings(proto_snap)
            self.ctx.restore_literal_facts(lit_snap)
            self.ctx.restore_local_scope(br_snap)
            for _iv in init_vars:
                self.ctx.isinstance_init_locals.pop(_iv, None)
                self.ctx.deref_view_init_locals.pop(_iv, None)

        # Final else branch (from the last node in the chain)
        last = chain[-1]
        if last.else_body:
            self.ctx.emit_else_comment(out, last.else_body, indent)
            out.write(f"{indent}}} else {{\n")
            # Skip else_type_facts extraction when the else body is an elif
            # that will do its own isinstance checks against the original variant.
            is_elif_continuation = (
                len(last.else_body) == 1
                and isinstance(last.else_body[0], TpyIf)
                and self._is_elif(last, last.else_body[0])
            )
            else_lit_snap = self.ctx.save_literal_facts()
            else_proto_snap = self.ctx.save_protocol_narrowings()
            if is_elif_continuation:
                else_saved: dict[str, str | None] = {}
            else:
                else_saved = self._emit_isinstance_extractions(out, last.else_type_facts)

            self.ctx.indent_level += 1
            for s in last.else_body:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, last.else_body, self.ctx.indent())
            self.ctx.indent_level -= 1

            self.ctx.restore_narrowed_vars(else_saved)
            self.ctx.restore_protocol_narrowings(else_proto_snap)
            self.ctx.restore_literal_facts(else_lit_snap)
            self.ctx.restore_local_scope(br_snap)

        out.write(f"{indent}}}\n")

        # Early-return narrowing: when the then-body terminates (return/raise)
        # and there's no else block, code after the if is implicitly the else
        # branch. Emit else_type_facts extractions at the outer scope (like
        # assert narrowing).
        #
        # Restricted to two fact shapes for which the post-guard extraction is
        # safe in the face of later isinstance checks against the same source:
        #
        # - Recursive-union facts: std::get<T> extraction caches the narrowed
        #   value but the original variant survives in var_decl, so a later
        #   isinstance still dispatches against the right alternative.
        # - Polymorphic-class facts (Optional[Polymorphic] / bare Polymorphic):
        #   dynamic_cast cast-and-cache aliases a reference local; later
        #   isinstance reads var_decl (the declared source), not the alias.
        #
        # General (non-recursive) unions are excluded -- sequential isinstance
        # checks on the same variable would shadow the original variant.
        if (emit_post_narrowing
                and not last.else_body and last.else_type_facts
                and self._has_concrete_isinstance_facts(last.else_type_facts)
                and not self._is_protocol_isinstance_condition(last.condition)
                and not self._condition_static_true(last.condition)):
            then_body = last.then_body
            if then_body and isinstance(then_body[-1], (TpyReturn, TpyRaise)):
                registry = self.ctx.analyzer.registry
                def _recursive_union_shape(vt: TpyType | None) -> bool:
                    """True if vt's storage form is a recursive-union wrapper
                    struct. Handles the post-`_fix_recursive_optional_annotations`
                    shape `OptionalType(AliasRef(name))` -- declared `Tree | None`
                    is rewritten to that form, whose `needs_wrapper()` is False
                    despite the underlying alias being a wrapper struct."""
                    if vt is None:
                        return False
                    if vt.needs_wrapper():
                        return True
                    return isinstance(vt, OptionalType) and isinstance(vt.inner, AliasRef)
                def _narrows_to_union_member(vt: TpyType | None, narrowed: TpyType) -> bool:
                    """True when the early-returning branch leaves a plain union
                    narrowed to a single concrete member. Sequential isinstance on
                    a single-member narrowing is dead (the alternative is known),
                    so the post-guard extraction is safe -- unlike the multi-member
                    remaining case, which stays a union and must keep dispatching."""
                    base = unwrap_readonly(vt) if vt is not None else None
                    return (isinstance(base, UnionType)
                            and not isinstance(narrowed, UnionType)
                            and any(m == narrowed for m in base.members))
                post_facts = {
                    k: v for k, v in last.else_type_facts.items()
                    if (_recursive_union_shape(self.ctx.var_types.get(k))
                        or is_polymorphic_subclass_fact(
                            self.ctx.lookup_var_type(k), v, registry)
                        # A union member can't be re-narrowed to a different
                        # concrete type, so an already-live alias is correct as-is
                        # -- re-extracting would redeclare it in the same scope.
                        or (_narrows_to_union_member(self.ctx.lookup_var_type(k), v)
                            and k not in self.ctx.narrowed_vars))
                }
                if post_facts:
                    self._emit_isinstance_extractions(
                        out, post_facts, indent_extra=0, persistent=True)

    def _check_overload_return_type(self, stmt: TpyReturn, stub_ret: TpyType) -> bool:
        """Validate that a return expression's type is compatible with the stub's return type.

        Called during @overload specialization codegen. Returns True if
        compatible, False if incompatible (dead code after dead branch
        elimination -- skip the return).

        Delegates to sema's check_type_compatible to reuse all compatibility
        rules (Optional wrapping, inheritance, protocols, coercions, etc.).
        """
        value_type = stmt.value_type
        assert value_type is not None
        # Resolve pending types to concrete types
        if isinstance(value_type, IntLiteralType):
            value_type = BIGINT
        elif isinstance(value_type, PendingViewType):
            value_type = value_type.family.owned_type
        try:
            self.ctx.analyzer.compat.check_type_compatible(
                value_type, stub_ret, "return value",
                loc=stmt.loc, is_return=True,
            )
            return True
        except SemanticError:
            return False

    def _strip_wrong_overload_coerce(self, expr: TpyExpr, stub_ret: TpyType) -> TpyExpr:
        """Strip a TpyCoerce if it targets the wrong type for this overload stub.

        Sema coerces returns against the impl's union return type, which picks
        the first matching member. When generating a specialized stub, that
        coercion may target a different union member than the stub's return
        type. Stripping it lets codegen produce the raw expression, and C++
        implicit conversions handle the rest (e.g., int32_t -> BigInt).
        """
        if not isinstance(expr, TpyCoerce):
            return expr
        if expr.expected_type == stub_ret:
            return expr
        # The coercion targets a type compatible with the stub -- keep it
        # (e.g., coercion to inner type of Optional stub)
        if isinstance(stub_ret, OptionalType) and expr.expected_type == stub_ret.inner:
            return expr
        # Wrong target: unwrap to the raw expression
        return expr.expr

    def _gen_if_overload_specialized(
        self, out: TextIO, chain: list[TpyIf], indent: str,
    ) -> bool:
        """Try to generate an if/elif/else chain with dead branch elimination.

        Returns True if the chain was fully handled (at least one branch
        resolved statically). Returns False if no static resolution was
        possible (caller falls through to normal codegen).
        """
        resolutions = [self._resolve_isinstance_statically(node.condition) for node in chain]
        if all(r is None for r in resolutions):
            return False

        # Collect live (non-False) branches
        live: list[tuple[TpyIf, bool | None]] = []
        for node, resolved in zip(chain, resolutions):
            if resolved is True:
                # Always taken -- emit body directly, skip everything after
                for s in node.then_body:
                    self.gen_stmt(out, s)
                if node.then_body and isinstance(node.then_body[-1], (TpyReturn, TpyRaise)):
                    self.ctx.overload_terminated = True
                return True
            elif resolved is False:
                continue
            else:
                live.append((node, resolved))

        if not live:
            # All branches dead -- emit else body of the last original branch
            last = chain[-1]
            if last.else_body:
                for s in last.else_body:
                    self.gen_stmt(out, s)
            return True

        # Emit only the live (dynamic) branches as a clean if/elif chain
        for node, _ in live:
            self._emit_branch_decls(out, node, indent)
        br_snap = self.ctx.snapshot_local_scope()
        for i, (node, _) in enumerate(live):
            cond = self.expressions.gen_truthy_expr(node.condition)
            self.ctx.temps.flush(out, indent)
            if i == 0:
                out.write(f"{indent}if ({cond}) {{\n")
            else:
                out.write(f"{indent}}} else if ({cond}) {{\n")
            lit_snap = self.ctx.save_literal_facts()
            proto_snap = self.ctx.save_protocol_narrowings()
            then_saved = self._emit_isinstance_extractions(out, node.then_type_facts)
            self.ctx.indent_level += 1
            for s in node.then_body:
                self.gen_stmt(out, s)
            self.ctx.indent_level -= 1
            self.ctx.restore_narrowed_vars(then_saved)
            self.ctx.restore_protocol_narrowings(proto_snap)
            self.ctx.restore_literal_facts(lit_snap)
            self.ctx.restore_local_scope(br_snap)

        # Else body from the last original branch
        last = chain[-1]
        if last.else_body:
            out.write(f"{indent}}} else {{\n")
            self.ctx.indent_level += 1
            for s in last.else_body:
                self.gen_stmt(out, s)
            self.ctx.indent_level -= 1
            self.ctx.restore_local_scope(br_snap)
        out.write(f"{indent}}}\n")
        return True

    @staticmethod
    def _is_elif(outer: TpyIf, inner: TpyIf) -> bool:
        """True when inner is an elif of outer (not a nested else: if).

        Python's AST represents both as orelse=[If(...)]. We distinguish
        them by column: elif keeps the same column, nested else: if is
        indented deeper. Macro-emitted bodies have their locs stripped
        (builder_trace's `_strip_fragment_locs`) so user diagnostics
        don't pick up fragment line numbers; in that case both locs
        are None and we fall back to treating the chain as elif --
        macros emit structurally-equivalent chains and benefit from
        the flat ``else if`` codegen.
        """
        if outer.loc is None and inner.loc is None:
            return True
        if outer.loc is None or inner.loc is None:
            return False
        return inner.loc.column == outer.loc.column

    def _resolve_isinstance_statically(self, condition: TpyExpr) -> bool | None:
        """Check if a condition can be resolved statically in @overload context.

        Returns True if always-true, False if always-false, None if dynamic.
        Handles isinstance checks (union flattening) and literal equality
        checks (literal flattening).
        """
        # Union flattening: isinstance checks on known param types
        if self.ctx.overload_param_types:
            # Direct isinstance: isinstance(x, T)
            if isinstance(condition, TpyCall) and condition.isinstance_var is not None:
                var_name = condition.isinstance_var
                check_type = condition.isinstance_type
                concrete = self.ctx.overload_param_types.get(var_name)
                if concrete is not None and check_type is not None:
                    if concrete == check_type:
                        return True
                    if isinstance(check_type, UnionType) and concrete in check_type.members:
                        return True
                    return False
            # `x is None` / `x is not None` where x is a narrowed param
            if isinstance(condition, TpyBinOp) and condition.op in ("is", "is not"):
                for var_side, none_side in [
                    (condition.left, condition.right),
                    (condition.right, condition.left),
                ]:
                    if (isinstance(var_side, TpyName)
                            and isinstance(none_side, TpyNoneLiteral)
                            and var_side.name in self.ctx.overload_param_types):
                        concrete = self.ctx.overload_param_types[var_side.name]
                        is_none = isinstance(concrete, NoneType)
                        return is_none if condition.op == "is" else not is_none

        # Literal flattening: equality checks and bool truthiness
        if self.ctx.literal_facts:
            result = self._resolve_literal_eq_statically(condition)
            if result is not None:
                return result
            # Bool truthiness: `if x:` where x is Literal[True] or Literal[False]
            if isinstance(condition, TpyName):
                lit_type = self.ctx.literal_facts.get(condition.name)
                if (isinstance(lit_type, LiteralType) and len(lit_type.values) == 1
                        and lit_type.values[0].tag is LiteralTag.BOOL):
                    return bool(lit_type.values[0].value)

        # Logical chains: && / ||
        if isinstance(condition, TpyBinOp) and condition.op in ("&&", "||"):
            left = self._resolve_isinstance_statically(condition.left)
            right = self._resolve_isinstance_statically(condition.right)
            if condition.op == "||":
                if left is True or right is True:
                    return True
                if left is False and right is False:
                    return False
            else:
                if left is False or right is False:
                    return False
                if left is True and right is True:
                    return True
            # Coverage / contradiction on unresolved operands
            if self.ctx.literal_facts and left is None and right is None:
                return self._resolve_literal_chain_statically(condition)
            return None

        # Containment: x in (a, b, ...) / x not in (a, b, ...)
        if (isinstance(condition, TpyBinOp) and condition.op in ("in", "not in")
                and self.ctx.literal_facts):
            result = self._resolve_literal_in_statically(condition)
            if result is not None:
                return result

        # Negated condition
        if isinstance(condition, TpyUnaryOp) and condition.op == "!":
            inner = self._resolve_isinstance_statically(condition.operand)
            if inner is not None:
                return not inner

        return None

    def _resolve_literal_eq_statically(self, condition: TpyExpr) -> bool | None:
        """Resolve `x == lit` / `x != lit` statically using literal_facts.

        Single-value: x == val -> True/False. Multi-value: x == val -> False
        if val not in set (can't resolve True since we don't know which value).
        """
        if not isinstance(condition, TpyBinOp) or condition.op not in ("==", "!="):
            return None
        for var_side, lit_side in [(condition.left, condition.right), (condition.right, condition.left)]:
            if not isinstance(var_side, TpyName):
                continue
            lit_type = self.ctx.literal_facts.get(var_side.name)
            if not isinstance(lit_type, LiteralType):
                continue
            lit_val = literal_value_from_expr(lit_side)
            if lit_val is None:
                continue
            in_set = lit_val in lit_type.values
            if len(lit_type.values) == 1:
                matches = in_set
                return matches if condition.op == "==" else not matches
            # Multi-value: can only resolve when value is NOT in set
            if not in_set:
                return False if condition.op == "==" else True
        return None

    def _resolve_literal_chain_statically(self, condition: TpyBinOp) -> bool | None:
        """Resolve || / && chains of == comparisons using literal_facts."""
        from .expressions import _check_literal_chain
        return _check_literal_chain(condition, self.ctx.literal_facts)

    def _resolve_literal_in_statically(self, condition: TpyBinOp) -> bool | None:
        """Resolve `x in (a, b, ...)` / `x not in (a, b, ...)` using literal_facts."""
        from .expressions import _check_literal_in
        return _check_literal_in(condition, self.ctx.literal_facts)

    def _has_concrete_isinstance_facts(self, type_facts: dict[str, TpyType]) -> bool:
        """Check if type_facts contain any concrete types that would emit extractions."""
        return any(
            not (isinstance(ty, (UnionType, LiteralType)) or is_void_like_type(ty))
            and not is_protocol_type(ty)
            for ty in type_facts.values()
        )

    def _emit_branch_decls(self, out: TextIO, stmt: TpyStmt, indent: str) -> None:
        """Pre-declare variables first declared inside if/elif/match branches."""
        branch_decls = self.ctx.analyzer.if_branch_decls.get(id(stmt), {})
        for name, raw_var_type in branch_decls.items():
            var_type = unwrap_ref_type(raw_var_type)
            # Resumable body (generator / async): a branch-first-declared
            # local that is a frame field must NOT be re-declared as a C++
            # local here -- the local would shadow the struct member and the
            # branch writes would land on it instead of the field, losing the
            # value across any later suspension. Record it as declared (so the
            # branch assignments emit `name = ...` against the member) and emit
            # no decl.
            if self.ctx.in_generator_body and name in self.ctx.generator_field_names:
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                if var_type is not None:
                    self.ctx.var_types[name] = var_type
                    if self.ctx.current_ns:
                        self.ctx.current_ns.bind_variable(name, var_type)
                continue
            if (name not in self.ctx.declared_vars
                    and name not in self.ctx.global_declared_vars
                    and name not in self.ctx.native_global_names):
                # @dynamic protocol branch-declared vars: just pre-declare Base* pointer.
                # Per-assignment slots are created by rebind (hoisted to function scope).
                if self._is_dynamic_protocol_type(var_type):
                    base_type = self.protocols.get_dynamic_base_name(var_type)
                    out.write(f"{indent}{base_type}* {name};\n")
                    self.ctx.pointer_locals.add(name)
                    self.ctx.declared_vars.add(name)
                    self.ctx.local_scope_names.add(name)
                    self.ctx.var_types[name] = var_type
                    if self.ctx.current_ns and var_type:
                        self.ctx.current_ns.bind_variable(name, var_type)
                    continue
                # OptionalType uses inner type (pointer-local adds T*).
                # var_type.inner may be ReadonlyType(T) when sema readonly-propagation
                # wrote OptionalType(ReadonlyType(T)) into if_branch_decls.
                # Unwrap both the Optional and the inner ReadonlyType to get the bare C++ type.
                resolve_type = var_type
                is_const = False
                if isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
                    is_const = isinstance(var_type.inner, ReadonlyType)
                    resolve_type = var_type.inner
                if isinstance(resolve_type, ReadonlyType):
                    is_const = True
                    resolve_type = resolve_type.wrapped
                cpp_type = self.types.type_to_cpp(resolve_type)
                self.ctx.declared_vars.add(name)
                # Any predecl-hoisted name (loop OR if/try/with/match branch) is
                # declared in the outer scope, so a same-named tuple-unpack
                # target must ASSIGN, not re-declare (the _gen_tuple_unpack
                # is_new check). A loop-only set would miss the branch hoists.
                self.ctx.predecl_hoisted_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = var_type
                if self.ctx.current_ns and var_type:
                    self.ctx.current_ns.bind_variable(name, var_type)
                # A post-loop-used target of a borrow-ref tuple unpack
                # (`for k, v in d.items(): ...; use(v)`) hoists in POINTER
                # form: the per-iteration binding aliases the live container
                # element, so the hoisted slot must hold that alias (CPython:
                # the leaked loop var IS the last element). An owned
                # std::optional hoist would stay disengaged -- the in-loop
                # `auto&&` binding can't engage it -- making the post-loop
                # read UB.
                idx = (self._unpack_borrow_target_index(stmt, name)
                       if isinstance(stmt, TpyForEach) and stmt.is_tuple_unpack
                       and self._is_plain_nonvalue(var_type) else None)
                if idx is not None:
                    up = stmt.body[0]
                    self.ctx.pointer_locals.add(name)
                    t_const = bool(up.is_const_ref and idx < len(up.is_const_ref)
                                   and up.is_const_ref[idx])
                    if t_const:
                        self.ctx.const_indirect_locals.add(name)
                    const_pfx = "const " if t_const else ""
                    out.write(f"{indent}{const_pfx}{cpp_type}* {name} = nullptr;\n")
                    continue
                # A plain non-value local bound only by statement-level
                # borrows (never a fresh rvalue) must alias, not own: the
                # optional-storage form would copy and sever the alias.
                # Sema materializes the fact (sema_stmt_borrow_decls excludes
                # with-as / unpack-loop / match-capture names -- their binding
                # machinery owns the storage form; single-target for-loop
                # vars are not excluded, but a loop-var-only name has no
                # statement-level binding and so never enters the fact).
                borrow_only = (self._is_plain_nonvalue(var_type)
                               and name not in self.ctx.sema_ever_owned_locals
                               and name in self.ctx.sema_stmt_borrow_decls)
                if (self._is_plain_nonvalue(var_type)
                        and name not in self.ctx.reassigned_vars
                        and not borrow_only):
                    # Non-value, not reassigned, rvalue-bound: std::optional<T>
                    # avoids pointer indirection and unnecessary default
                    # construction.
                    self.ctx.pointer_locals.add(name)
                    self.ctx.optional_locals.add(name)
                    if is_const:
                        self.ctx.const_indirect_locals.add(name)
                    self.ctx.movable_locals.add(name)
                    out.write(f"{indent}std::optional<{cpp_type}> {name};\n")
                elif borrow_only or self._needs_indirection(var_type, name, None):
                    # Reassigned or borrow-bound non-value: T* pointer-local
                    # (with slot storage for rvalue rebinds). Mark as
                    # branch-hoisted so _gen_pointer_local_rebind puts
                    # rvalue slots into pending_hoist_decls (not block-scoped).
                    self.ctx.pointer_locals.add(name)
                    self.ctx.branch_hoisted_vars.add(name)
                    is_const = is_const or self.ctx.sema_stmt_borrow_decls.get(name, False)
                    if is_const:
                        self.ctx.const_indirect_locals.add(name)
                    if name in self.ctx.sema_movable_locals:
                        self.ctx.movable_locals.add(name)
                    const_pfx = "const " if is_const else ""
                    if name in self.ctx.rvalue_reassigned_vars:
                        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                        slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = slot
                        out.write(f"{indent}{static_kw}std::optional<{cpp_type}> {slot};\n")
                    out.write(f"{indent}{const_pfx}{cpp_type}* {name};\n")
                elif (isinstance(resolve_type, TupleType)
                        and resolve_type.has_pointer_repr_element()):
                    # Borrow-form tuple local: forward-declare `std::tuple<..., T*>`
                    # (default-constructs, pointers null) so branch assignments
                    # alias rather than copy. Its writes stay borrow form.
                    # const iff any binding source is const storage (the decl
                    # must be at least as const as every source feeding it; see
                    # `_compute_borrow_tuple_const`).
                    # branch_hoisted so an owning-call binding in a branch puts
                    # its storage slot in pending_hoist_decls (function scope) --
                    # a block-scoped slot would leave this function-scoped local
                    # dangling after the branch.
                    self.ctx.borrow_form_tuple_locals.add(name)
                    self.ctx.branch_hoisted_vars.add(name)
                    elem_const = is_const or name in self.ctx.const_borrow_form_tuple_locals
                    if elem_const:
                        self.ctx.const_borrow_form_tuple_locals.add(name)
                    borrow_cpp = self.types.tuple_borrow_cpp(resolve_type, const=elem_const)
                    out.write(f"{indent}{borrow_cpp} {name};\n")
                elif (isinstance(var_type, OptionalType)
                        and var_type.wraps_pointer_repr_tuple()):
                    # Nullable borrow-form tuple first-declared in a branch:
                    # forward-declare `std::optional<std::tuple<..., T*>>`
                    # (default nullopt) and register the tracking set so branch
                    # assignments take the OPTIONAL_BORROW_TUPLE path (alias, not
                    # copy). Mirrors the main-path decl; branch_hoisted so an
                    # owning-call binding's slot is function-scoped (parallel to
                    # the BORROW_TUPLE arm above).
                    self.ctx.optional_borrow_tuple_locals.add(name)
                    self.ctx.branch_hoisted_vars.add(name)
                    elem_const = is_const or name in self.ctx.const_optional_borrow_tuple_locals
                    if elem_const:
                        self.ctx.const_optional_borrow_tuple_locals.add(name)
                    inner_borrow = self.types.tuple_borrow_cpp(var_type.inner, const=elem_const)
                    out.write(f"{indent}std::optional<{inner_borrow}> {name};\n")
                else:
                    out.write(f"{indent}{cpp_type} {name};\n")

    def _is_loop_var_hoisted(self, stmt: TpyForEach) -> bool:
        """Check if the loop variable was hoisted for post-loop use."""
        return stmt.hoist_loop_var

    @staticmethod
    def _unpack_borrow_target_index(stmt: TpyForEach, name: str) -> int | None:
        """Index of `name` among the loop's tuple-unpack targets when its
        element binds as a borrow (Ref-typed / is_ref), else None."""
        if not (stmt.is_tuple_unpack and stmt.body
                and isinstance(stmt.body[0], TpyTupleUnpack)):
            return None
        up = stmt.body[0]
        for i, tname in enumerate(up.targets):
            if tname != name:
                continue
            if isinstance(up.target_types[i], RefType):
                return i
            if up.is_ref and i < len(up.is_ref) and up.is_ref[i]:
                return i
            return None
        return None

    def _gen_while(self, out: TextIO, stmt: TpyWhile, indent: str) -> None:
        """Generate a while loop."""
        has_else = bool(stmt.orelse)
        label = ""
        if has_else:
            label = f"__after_else_{self.ctx.iter_counter}"
            self.ctx.iter_counter += 1
        self.ctx.loop_else_labels.append(label)
        self.ctx.loop_break_labels.append("")
        saved_switch_depth = self.ctx.match_switch_depth
        self.ctx.match_switch_depth = 0

        cond_checkpoint = self.ctx.temps.checkpoint()
        cond = self.expressions.gen_truthy_expr(stmt.condition)
        if (self.ctx.temps.has_pending_since(cond_checkpoint)
                and not contains_named_expr(stmt.condition)):
            # The condition registered anonymous temps (arg materializations).
            # A while header re-evaluates per iteration, so they must live in
            # the loop head, not before the loop -- a pre-loop flush would
            # freeze per-iteration state into a stale snapshot. Gated to
            # walrus-free conditions: an in-head temp could run before the
            # walrus assignment it reads, and a borrow-form walrus aliasing
            # into a per-iteration temp would dangle after the loop, so the
            # mixed shape keeps the legacy single-eval flush below (BUGS.md).
            cond_temps = io.StringIO()
            self.ctx.temps.flush_since(cond_temps, cond_checkpoint,
                                       indent + INDENT)
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}while (true) {{\n")
            out.write(cond_temps.getvalue())
            out.write(f"{indent}{INDENT}if (!({cond})) break;\n")
        else:
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}while ({cond}) {{\n")

        lit_snap = self.ctx.save_literal_facts()
        proto_snap = self.ctx.save_protocol_narrowings()
        # Snapshot narrowed_vars + declared_persistent_aliases before the body.
        # Persistent narrowings made INSIDE the body (assert isinstance /
        # `if not isinstance(...): return` in the body) would otherwise leak
        # past the closing brace, leaving these maps pointing at C++ aliases
        # whose declarations went out of scope. The full-dict restore at body
        # exit also reverts the condition-level extractions emitted for
        # `while isinstance(...)`, so no separate restore_narrowed_vars is
        # needed for the condition narrowings.
        body_narrowed_snap = dict(self.ctx.narrowed_vars)
        body_alias_snap = self.ctx.declared_persistent_aliases.copy()
        self._emit_isinstance_extractions(out, stmt.then_type_facts)

        self.ctx.indent_level += 1
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.emit_block_trailing_comments(out, stmt.body, self.ctx.indent())
        self.ctx.indent_level -= 1

        self.ctx.narrowed_vars = body_narrowed_snap
        self.ctx.declared_persistent_aliases = body_alias_snap
        self.ctx.restore_protocol_narrowings(proto_snap)
        self.ctx.restore_literal_facts(lit_snap)
        out.write(f"{indent}}}\n")
        self.ctx.loop_else_labels.pop()
        self.ctx.match_switch_depth = saved_switch_depth
        break_label = self.ctx.loop_break_labels.pop()

        if has_else:
            self.ctx.emit_else_comment(out, stmt.orelse, indent)
            out.write(f"{indent}{{\n")
            self.ctx.indent_level += 1
            for s in stmt.orelse:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, stmt.orelse, self.ctx.indent())
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}\n")
            out.write(f"{indent}{label}:;\n")
        if break_label:
            out.write(f"{indent}{break_label}:;\n")

    def _gen_loop_body(self, out: TextIO, stmt: TpyForEach, indent: str,
                        elem_type: TpyType | None,
                        range_counter: str | None = None,
                        consuming: bool = False) -> None:
        """Generate loop body statements with namespace/scope tracking.

        Shared by _gen_begin_end_loop, _gen_range_counter_loop, and _gen_for_each.
        Writes the body statements, the closing brace, and cleans up the loop
        variable from var_types.

        range_counter: when the loop variable is hoisted, this is the hidden
        counter name; emit `var = counter;` at the start of the body so the
        user variable holds the current (not post-increment) value.

        consuming: when True, the loop variable is bound via auto&& into owned
        storage and can be moved at last use within the iteration body.
        """
        was_declared = stmt.var in self.ctx.declared_vars
        self.ctx.local_scope_names.add(stmt.var)
        self.ctx.declared_vars.add(stmt.var)
        # A fresh (non-hoisted) loop var gets a new C++ binding here, so any
        # stale pointer-form classification from an earlier sibling loop reusing
        # the same name (e.g. `for i, p in ...` then `for p in ...`) must be
        # cleared -- otherwise field access on this loop's value-form var would
        # wrongly emit `->`. Generator borrow-form loop vars are pointer-form by
        # design (seeded before the body) and left intact.
        if (not stmt.hoist_loop_var
                and stmt.var not in self.ctx.generator_borrow_form_loop_vars):
            self.ctx.pointer_locals.discard(stmt.var)
            self.ctx.const_indirect_locals.discard(stmt.var)
        # For-loop iter var is a C++-scoped binding; register a shadow
        # for the loop body so a paired discard at body exit cleanly
        # reverses just this site's addition. Skip for hoisted / pre-
        # declared vars -- their C++ shape was decided elsewhere.
        loop_shadows_frame_field = (
            not stmt.hoist_loop_var
            and not was_declared
            and self.ctx.register_frame_field_shadow(stmt.var))
        if elem_type:
            self.ctx.var_types[stmt.var] = elem_type
        self.ctx.register_loop_var_storage_form(stmt.var, elem_type, stmt.iterable)
        # Consuming loop: the loop variable is bound via auto&& into owned
        # storage (OwnIter), so it can be std::move'd at last use.
        # Also applies when sema resolved the element type as Own[T] (e.g.
        # iterating over Iterable[Own[T]] parameters).
        is_consuming = consuming or isinstance(stmt.elem_type, OwnType)
        if is_consuming and not stmt.hoist_loop_var:
            self.ctx.movable_locals.add(stmt.var)
        old_ns = self.ctx.current_ns
        if self.ctx.current_ns and elem_type:
            inner_ns = Namespace(parent=self.ctx.current_ns)
            inner_ns.bind_variable(stmt.var, elem_type)
            self.ctx.current_ns = inner_ns
        self.ctx.indent_level += 1
        if range_counter is not None:
            var = escape_cpp_name(stmt.var)
            out.write(f"{self.ctx.indent()}{var} = {range_counter};\n")
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.emit_block_trailing_comments(out, stmt.body, self.ctx.indent())
        self.ctx.indent_level -= 1
        self.ctx.local_scope_names.discard(stmt.var)
        if is_consuming and not stmt.hoist_loop_var:
            self.ctx.movable_locals.discard(stmt.var)
        # Only undo the loop's flag-add when the var was loop-scoped: hoisted
        # vars survive past the body, and pre-existing vars came in flagged
        # by an earlier site (var-decl) so the flag must persist.
        if not stmt.hoist_loop_var and not was_declared:
            self.ctx.storage_form_tuple_locals.discard(stmt.var)
            self.ctx.const_storage_form_tuple_locals.discard(stmt.var)
            self.ctx.storage_form_optional_locals.discard(stmt.var)
            self.ctx.const_storage_form_optional_locals.discard(stmt.var)
        if loop_shadows_frame_field:
            self.ctx.frame_field_shadows.discard(stmt.var)
        self.ctx.current_ns = old_ns

        out.write(f"{indent}}}\n")

        if stmt.var in self.ctx.var_types:
            del self.ctx.var_types[stmt.var]
        # Non-hoisted loop vars are scoped to the for block; remove from
        # declared_vars so a later loop reusing the same name can re-declare.
        # Keep if it was already declared before the loop (e.g. global vars).
        if not stmt.hoist_loop_var and not was_declared:
            self.ctx.declared_vars.discard(stmt.var)

    def _gen_begin_end_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                            iterable_expr: str, elem_type: TpyType,
                            is_lvalue: bool | None = None,
                            consuming: bool = False) -> None:
        """Generate the canonical begin/end iterator loop.

        Produces:
            auto& __obj_N = <lvalue_expr>;   // or: auto __obj_N = <rvalue_expr>;
            auto __beg_N = __obj_N.begin();
            auto __end_N = __obj_N.end();
            for (; __beg_N != __end_N; ++__beg_N) {
                T var = *__beg_N;            // value types: typed copy
                auto&& var = *__beg_N;       // non-value types: forwarding ref
                // body
            }

        If is_lvalue is None, it's determined from stmt.iterable.
        """
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1
        obj_name = f"__obj_{n}"
        beg_name = f"__beg_{n}"
        end_name = f"__end_{n}"

        if is_lvalue is None:
            is_lvalue = self._is_lvalue_iterable(stmt.iterable)
        obj_binding = "auto&" if is_lvalue else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{obj_binding} {obj_name} = {iterable_expr};\n")
        out.write(f"{indent}auto {beg_name} = {obj_name}.begin();\n")
        out.write(f"{indent}auto {end_name} = {obj_name}.end();\n")
        out.write(f"{indent}for (; {beg_name} != {end_name}; ++{beg_name}) {{\n")

        inner_indent = indent + INDENT
        cpp_var = escape_cpp_name(stmt.var)
        hoisted = self._is_loop_var_hoisted(stmt)
        lift_cpp = None
        if hoisted and stmt.var in self.ctx.borrow_form_tuple_locals:
            elem_bare = unwrap_readonly(unwrap_ref_type(elem_type))
            if (isinstance(elem_bare, TupleType)
                    and elem_bare.has_pointer_repr_element()):
                lift_cpp = self.types.tuple_borrow_cpp(elem_bare)
        binding = loop_var_binding(elem_type, cpp_var, f"*{beg_name}",
                                   stmt.const_loop_var, hoisted,
                                   consuming=consuming,
                                   hoisted_tuple_lift_cpp=lift_cpp)
        out.write(f"{inner_indent}{binding}\n")

        self._gen_loop_body(out, stmt, indent, elem_type, consuming=consuming)

    def _gen_direct_next_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                               iterable_expr: str, elem_type: TpyType,
                               call: str = ".__next__()",
                               iter_name: str | None = None,
                               consuming: bool = False) -> None:
        """Generate direct for(;;) loop calling a next-method.

        call is the method suffix appended to the iterator name, e.g.:
          ".__next__()"       -- error_return user iterators

        When iter_name is provided, the iterator variable is already allocated
        by the caller and no capture line is emitted.

        Uses ::tpy::unwrap_ref() to unwrap val_or_ref from native_iterator
        __next__(). For user-defined iterators returning plain T, unwrap
        is a transparent pass-through.

        Produces:
            auto& __iter_N = <expr>;   (skipped when iter_name is provided)
            for (;;) {
                auto __r_N = <call>;
                if (!__r_N.has_value()) break;
                T x = ::tpy::unwrap_ref(*__r_N);
                // body
            }
        """
        scope_outer: str | None = None
        if iter_name is None:
            n = self.ctx.iter_counter
            self.ctx.iter_counter += 1
            iter_name = f"__iter_{n}"
            r_name = f"__r_{n}"

            is_lvalue_src = self._is_lvalue_iterable(stmt.iterable)
            if not is_lvalue_src:
                # Temporary iterable: brace-scope the loop so the temp dies
                # at loop exit, like CPython's refcount drop -- observable
                # when the iterator owns cleanup (a generator frame's
                # pending finally, a file handle).
                scope_outer = indent
                out.write(f"{indent}{{\n")
                indent = indent + INDENT
            src_binding = "auto&" if is_lvalue_src else "auto"
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{src_binding} {iter_name} = {iterable_expr};\n")
        else:
            self.ctx.temps.flush(out, indent)
            n = self.ctx.iter_counter
            self.ctx.iter_counter += 1
            r_name = f"__r_{n}"
        cpp_var = escape_cpp_name(stmt.var)
        out.write(f"{indent}for (;;) {{\n")
        inner_indent = indent + INDENT
        out.write(f"{inner_indent}auto {r_name} = {iter_name}{call};\n")
        out.write(f"{inner_indent}if (!{r_name}.has_value()) break;\n")

        deref = f"::tpy::unwrap_ref(*{r_name})"
        hoisted = self._is_loop_var_hoisted(stmt)
        if hoisted:
            binding = f"{cpp_var} = {deref};"
        elif elem_type:
            binding = loop_var_binding(elem_type, cpp_var, deref,
                                       stmt.const_loop_var,
                                       consuming=consuming)
        else:
            binding = f"auto {cpp_var} = {deref};"
        out.write(f"{inner_indent}{binding}\n")

        self._gen_loop_body(out, stmt, indent, elem_type, consuming=consuming)
        if scope_outer is not None:
            out.write(f"{scope_outer}}}\n")

    def _gen_direct_next_loop_with_iter(self, out: TextIO, stmt: TpyForEach, indent: str,
                                         iterable_expr: str, elem_type: TpyType,
                                         iter_call: str = ".__iter__",
                                         next_call: str = ".__next__()") -> None:
        """Call __iter__() on source, then direct loop on the resulting iterator.

        iter_call: how to get the iterator (".__iter__" for method, "::tpy::__iter__" for free fn)
        next_call: how to advance (".__next__()")
        """
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1
        src_name = f"__src_{n}"
        iter_name = f"__itr_{n}"
        is_lvalue_src = self._is_lvalue_iterable(stmt.iterable)
        src_binding = "auto&" if is_lvalue_src else "auto"

        scope_outer: str | None = None
        if not is_lvalue_src:
            # Temporary iterable: brace-scope the loop so the temp dies at
            # loop exit, like CPython's refcount drop -- observable when
            # the source owns cleanup (a generator frame's pending finally,
            # a file handle).
            scope_outer = indent
            out.write(f"{indent}{{\n")
            indent = indent + INDENT
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{src_binding} {src_name} = {iterable_expr};\n")
        # auto&& preserves reference returns from __iter__ (iterator-shaped
        # sources return self& -- needed for move-only owning iterators and
        # in-place consumption of user iterators) and lifetime-extends value
        # returns (container -> native_iterator fallback).
        if iter_call.startswith("."):
            out.write(f"{indent}auto&& {iter_name} = {src_name}{iter_call}();\n")
        else:
            out.write(f"{indent}auto&& {iter_name} = {iter_call}({src_name});\n")

        self._gen_direct_next_loop(out, stmt, indent, iterable_expr, elem_type,
                                   call=next_call, iter_name=iter_name)
        if scope_outer is not None:
            out.write(f"{scope_outer}}}\n")

    def _is_lvalue_iterable(self, expr: TpyExpr) -> bool:
        """Check if the iterable expression is a C++ lvalue."""
        return is_lvalue_iterable(
            expr, self.ctx.analyzer.registry.get_record,
            self.types.get_resolved_type)

    @staticmethod
    def _is_literal_range_arg(expr: TpyExpr) -> bool:
        """Check if a range arg is a compile-time literal (safe to inline).

        Only literals can be inlined in the for-loop condition. Variable names
        must be pre-evaluated into temps because Python's range() captures args
        at call time, but the for-loop condition re-evaluates each iteration.
        """
        return StatementGenerator._extract_int_literal(expr) is not None

    @staticmethod
    def _extract_int_literal(expr: TpyExpr) -> int | None:
        """Extract a compile-time integer value from a range argument.

        Handles bare literals (3), negated literals (-3), and fixed-int
        constructor calls with a literal arg (Int32(3)).
        Returns the integer value or None if not a compile-time constant.
        """
        return fixed_int_literal_value_from_expr(expr)

    def _gen_range_counter_loop(self, out: TextIO, stmt: TpyForEach,
                                 indent: str, elem_type: TpyType) -> bool:
        """Optimize range() to a C-style for-loop.

        Returns True if the optimization was applied, False if the caller
        should fall back to the generic while-loop codegen.
        """
        assert isinstance(stmt.iterable, TpyCall) and stmt.iterable.func_name == "range"
        range_call = stmt.iterable
        nargs = len(range_call.args)

        # Classify the step from the original AST (unwrap TpyCoerce from sema)
        if nargs == 3:
            step_lit = self._extract_int_literal(range_call.args[2])
            if step_lit is not None:
                if step_lit == 0:
                    return False  # zero step panics at runtime -- use Range ctor
                elif step_lit > 0:
                    step_kind = "literal_pos"
                    step_val = step_lit
                else:
                    step_kind = "literal_neg"
                    step_val = step_lit
            else:
                step_kind = "variable"
                step_val = None
        else:
            step_kind = "plus_one"
            step_val = 1

        gen_args = self.builtins.gen_range_args(range_call)

        # Determine start/stop/step C++ expressions
        if nargs == 1:
            start_expr, stop_expr = "0", gen_args[0]
        elif nargs == 2:
            start_expr, stop_expr = gen_args[0], gen_args[1]
        else:
            start_expr, stop_expr = gen_args[0], gen_args[1]

        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1

        self.ctx.temps.flush(out, indent)

        var = escape_cpp_name(stmt.var)
        cpp_elem = elem_type.to_cpp()
        # If loop var was pre-declared (hoisted for post-loop use), use a
        # hidden counter and assign the user variable inside the body so it
        # holds the last-yielded value (not the post-increment overshoot).
        hoisted = self._is_loop_var_hoisted(stmt)
        if hoisted:
            counter = f"__range_{n}"
            var_decl = f"{cpp_elem} {counter}"
        else:
            counter = var
            var_decl = f"{cpp_elem} {var}"

        # Pre-evaluate non-literal args into temps (left-to-right, matching
        # Python's argument evaluation order).  Literals are safe to inline
        # since they can't change; everything else must be captured once.
        if nargs >= 2:
            start_arg_ast = range_call.args[0]
            if not self._is_literal_range_arg(start_arg_ast):
                temp_name = f"__start_{n}"
                out.write(f"{indent}{cpp_elem} {temp_name} = {start_expr};\n")
                start_expr = temp_name

        stop_arg_ast = range_call.args[0] if nargs == 1 else range_call.args[1]
        if not self._is_literal_range_arg(stop_arg_ast):
            temp_name = f"__stop_{n}"
            out.write(f"{indent}{cpp_elem} {temp_name} = {stop_expr};\n")
            stop_expr = temp_name

        if step_kind == "plus_one":
            out.write(f"{indent}for ({var_decl} = {start_expr}; "
                      f"{counter} < {stop_expr}; ++{counter}) {{\n")
        elif step_kind == "literal_pos":
            if step_val == 1:
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} < {stop_expr}; ++{counter}) {{\n")
            else:
                step_cpp = gen_args[2]
                if is_big_int_type(elem_type):
                    step_temp = f"__step_{n}"
                    out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
                    step_cpp = step_temp
                self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} < {stop_expr}; "
                          f"{counter} += {step_cpp}) {{\n")
        elif step_kind == "literal_neg":
            if step_val == -1:
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} > {stop_expr}; --{counter}) {{\n")
            else:
                step_cpp = gen_args[2]
                if is_big_int_type(elem_type):
                    step_temp = f"__step_{n}"
                    out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
                    step_cpp = step_temp
                self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} > {stop_expr}; "
                          f"{counter} += {step_cpp}) {{\n")
        else:
            # Variable step -- capture, zero-check, upfront overflow check, ternary condition
            step_cpp = gen_args[2]
            step_temp = f"__step_{n}"
            out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
            step_cpp = step_temp
            out.write(f'{indent}::tpy::range_check_step_nonzero({step_cpp});\n')
            self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
            out.write(f"{indent}for ({var_decl} = {start_expr}; "
                      f"{step_cpp} > 0 ? {counter} < {stop_expr} : {counter} > {stop_expr}; "
                      f"{counter} += {step_cpp}) {{\n")

        self._gen_loop_body(out, stmt, indent, elem_type,
                            range_counter=counter if hoisted else None)
        return True

    def _gen_range_overflow_check(self, out: TextIO, indent: str,
                                    start_expr: str, stop_expr: str,
                                    step_expr: str, elem_type: TpyType) -> None:
        """Emit upfront overflow check for fixed-int range loops with step != ±1."""
        if is_fixed_int_type(elem_type):
            cpp_t = elem_type.to_cpp()
            out.write(f"{indent}::tpy::range_check_overflow<{cpp_t}>({start_expr}, {stop_expr}, {step_expr});\n")

    def _gen_for_each(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a for-each loop over a collection or iterator.

        Dispatch is handled by _gen_for_each_loop; see its docstring for
        the full dispatch order.
        """
        # Snapshot narrowed_vars + declared_persistent_aliases so persistent
        # narrowings inside the loop body (e.g. `if not isinstance(x, T): return`
        # followed by reads of x in the body, or `assert isinstance`) don't
        # leak past the body's C++ `{...}` -- the alias declarations live
        # only inside the loop block.
        body_narrowed_snap = dict(self.ctx.narrowed_vars)
        body_alias_snap = self.ctx.declared_persistent_aliases.copy()

        has_else = bool(stmt.orelse)
        label = ""
        if has_else:
            label = f"__after_else_{self.ctx.iter_counter}"
            self.ctx.iter_counter += 1
        self.ctx.loop_else_labels.append(label)
        self.ctx.loop_break_labels.append("")
        saved_switch_depth = self.ctx.match_switch_depth
        self.ctx.match_switch_depth = 0

        self._gen_for_each_loop(out, stmt, indent)
        self.ctx.narrowed_vars = body_narrowed_snap
        self.ctx.declared_persistent_aliases = body_alias_snap
        self.ctx.loop_else_labels.pop()
        self.ctx.match_switch_depth = saved_switch_depth
        break_label = self.ctx.loop_break_labels.pop()

        if has_else:
            self.ctx.emit_else_comment(out, stmt.orelse, indent)
            out.write(f"{indent}{{\n")
            self.ctx.indent_level += 1
            for s in stmt.orelse:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, stmt.orelse, self.ctx.indent())
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}\n")
            out.write(f"{indent}{label}:;\n")
        if break_label:
            out.write(f"{indent}{break_label}:;\n")

    def _for_iterable_deref(self, stmt: TpyForEach) -> str:
        """Render the for-loop iterable as its contained container value
        (narrowed-Optional unwrap, indirect-name deref) -- see
        `render_for_iterable` for the ownership split."""
        return self.expressions.render_for_iterable(stmt.iterable)

    def _gen_for_each_loop(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate the loop part of a for-each (without else handling).

        Dispatch order (peepholes first, then universal default):
        - Enum iteration: range over EnumUtil::members (begin/end).
        - OwnIter[T] / CopyIter[T]: begin/end (already exposes begin/end).
        - Auto-consuming iteration (`consuming_iter_fi`): native or user path.
        - range(...) call: C-style counter loop (or Range<T> begin/end fallback).
        - Concrete NativeIterable type, or NativeIterable[T]/Spannable[T]
          protocol param: plain C++ begin/end range-for, skipping the
          native_iterator adapter. Spannable[T] works because the compiler
          synthesizes begin()/end() from __span__() for conforming types.
          NativeIterable[T] as a parameter type is typically used as an
          opt-in fast path via `Iterable[T] | NativeIterable[T]` + isinstance
          narrowing.
        - Universal default: auto&& __itr = ::tpy::__iter__(src); for(;;) __itr.__next__().
          Handles all remaining shapes uniformly -- protocol Iterator/Iterable,
          error_return __next__ iterators, user __iter__() methods, and
          move-only owning iterators (map/filter/zip results).
        """
        # Enum iteration: `for c in Color` -> range over EnumUtil<Color>::members
        if stmt.enum_iterable is not None:
            enum_type = stmt.enum_iterable
            cpp_type = enum_type.to_cpp()
            iterable = f"::tpy::EnumUtil<{cpp_type}>::members"
            self._gen_begin_end_loop(out, stmt, indent, iterable, enum_type)
            return

        from tpyc.modules import is_native_iterable
        # A narrowed value-Optional iterable (`str | None` / `bytes | None`
        # proven non-None) keeps its declared `std::optional<V>` C++ type but
        # must be iterated as its contained value -- dispatch on the narrowed
        # inner (native begin/end path); `_for_iterable_deref` renders `(*v)`.
        iterable_type = self.expressions.narrowed_value_optional_iter_type(
            stmt.iterable, unwrap_ref_type(self.types.get_resolved_type(stmt.iterable)))

        # Resolve sema-stored elem_type (handles PendingViewType -> concrete).
        # Strip Ref -- codegen loop binding handles reference semantics via
        # is_value_type() / loop_var_binding(), not through Ref.
        sema_elem = unwrap_ref_type(self.types.resolve_type(stmt.elem_type)) if stmt.elem_type else None

        # OwnIter / CopyIter: explicit own_iter() / copy_iter() call.
        # These have begin/end, so use standard begin/end loop.
        # OwnIter uses auto&& binding (move-ready for future per-element moves).
        from ..type_def_registry import is_own_iter, is_copy_iter, is_list
        if is_own_iter(iterable_type) or is_copy_iter(iterable_type):
            iterable = self.expressions.gen_expr(stmt.iterable)
            elem_type = sema_elem
            assert elem_type is not None
            consuming = is_own_iter(iterable_type)
            self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type,
                                     consuming=consuming)
            return

        # Auto-consuming iteration: iterable at last use with consuming __iter__.
        # Only triggers when the loop variable is mutated (not const_loop_var).
        # Uses shared _gen_consuming_iter (also used by call-site arg generation).
        # Skip when loop var is hoisted (used after loop) -- the hoisted var may
        # be a view (string_view) into the container, so the container must stay alive.
        if stmt.consuming_iter_fi is not None and not stmt.hoist_loop_var:
            iterable = self.expressions.gen_expr(stmt.iterable)
            consuming_call = self.expressions._gen_consuming_iter(stmt.iterable, iterable)
            if consuming_call is not None:
                elem_type = sema_elem
                assert elem_type is not None
                if stmt.consuming_iter_fi.native_name:
                    # Native consuming iter (e.g. tpy::own_iter) returns a C++ range
                    self._gen_begin_end_loop(out, stmt, indent, consuming_call, elem_type,
                                             consuming=True, is_lvalue=False)
                else:
                    # User-defined consuming __iter__ returns a TPy Iterator
                    n = self.ctx.iter_counter
                    self.ctx.iter_counter += 1
                    iter_name = f"__itr_{n}"
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}auto {iter_name} = {consuming_call};\n")
                    self._gen_direct_next_loop(out, stmt, indent, "", elem_type,
                                               call=".__next__()", iter_name=iter_name,
                                               consuming=True)
                return

        # Resolve TypeParamRef to its bound for protocol-based iteration
        resolved_type = iterable_type
        if isinstance(iterable_type, TypeParamRef):
            bound = self.ctx.current_type_param_bounds.get(iterable_type.name)
            if bound is not None and is_protocol_type(bound):
                resolved_type = bound

        # Optimize range() calls to C-style counter loops
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func_name == "range":
            elem_type = sema_elem
            if elem_type and self._gen_range_counter_loop(out, stmt, indent, elem_type):
                return
            # Counter optimization didn't apply; fall back to Range<T> begin/end
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            if elem_type:
                self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type, is_lvalue=False)
                return

        # NativeIterable peephole: built-in types (list, dict, set, Span,
        # Array, str, bytes, etc.) and NativeIterable[T] / Spannable[T]
        # protocol params use C++ range-based-for with begin/end. User
        # records are NOT NativeIterable (they use the universal
        # __iter__+__next__ default). Spannable[T] protocol params work
        # because the compiler synthesizes begin()/end() from __span__()
        # for concrete types that satisfy Spannable (see records.py).
        # NativeIterable[T] is typically used as the fast-path arm of an
        # `Iterable[T] | NativeIterable[T]` union narrowed with
        # `isinstance(x, NativeIterable)`: the narrowed branch hits this
        # peephole (range-for), the other branch falls to the universal
        # default (__iter__/__next__).
        is_native = (
            is_native_iterable(iterable_type, registry=self.ctx.analyzer.registry)
            or (is_protocol_type(resolved_type)
                and resolved_type.qualified_name() in ("tpy.NativeIterable", "tpy.Spannable"))
        )
        if is_native:
            iterable = self._for_iterable_deref(stmt)
            if isinstance(stmt.iterable, TpyStrLiteral):
                # C string literals include the null terminator, so wrap in string_view
                iterable = f"std::string_view({iterable})"
            assert sema_elem is not None, "sema should always resolve for-loop element type"
            elem_type = sema_elem
            if isinstance(elem_type, IntLiteralType):
                elem_type = self.ctx.analyzer.ctx.default_int_type
            self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type)
            return

        # Universal default: ::tpy::__iter__(src) + .__next__() loop.
        # Covers protocol-typed Iterator[T] and Iterable[T], error_return
        # __next__ iterators, user records with __iter__() returning a
        # separate iterator type, and iterator-shaped sources (map/filter/
        # zip results, user iterator records). The runtime's ::tpy::__iter__
        # dispatches to the user's __iter__() method (or the auto-synthesized
        # one for pure iterators). `auto&&` binding in _gen_direct_next_loop_with_iter
        # preserves reference returns so move-only owning iterators work and
        # user iterator consumption semantics are preserved.
        iterable = self._for_iterable_deref(stmt)
        assert sema_elem is not None, "sema should always resolve for-loop element type"
        elem_type = sema_elem
        if isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        self._gen_direct_next_loop_with_iter(out, stmt, indent, iterable, elem_type,
                                              iter_call="::tpy::__iter__",
                                              next_call=".__next__()")
