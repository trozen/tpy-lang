"""
TurboPython Call Analysis

Function and constructor call analysis.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, ListType, PendingListType, IntLiteralType,
    StrType, CharType, ListLiteralInfo, FunctionInfo, RecordInfo, TypeParamRef,
    PtrType, ConstPtrType, VoidType, SpanType, ParamInfo, FixedIntType, BigIntType,
    VOID, BIGINT, is_protocol_type, unwrap_readonly,
)
from ..parse import TpyCall, TpyStrLiteral, TpyName, TpyFunction
from ..namespace import BindingKind
from ..coercions import CoercionContext
from .diagnostics import SemanticError
from .overloads import type_matches_numeric, resolve_overload

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .list_literals import ListLiteralTracker
    from .expressions import ExpressionAnalyzer

from tpyc import modules as builtin_modules
from tpyc.modules import MethodDef


def _method_def_to_function_info(m: MethodDef) -> FunctionInfo:
    """Convert a MethodDef (module-level constructor) to FunctionInfo for resolved_function_info."""
    return FunctionInfo(
        name="__init__",
        params=[ParamInfo(p.name, p.type, p.requires_lvalue, p.requires_mutable) for p in m.params],
        return_type=m.returns,
        is_readonly=m.is_readonly,
        cpp_template=m.cpp,
    )


def _has_type_param_ref(t: TpyType) -> bool:
    """Check if a type contains an unresolved TypeParamRef (e.g. Span[T])."""
    if isinstance(t, TypeParamRef):
        return True
    return any(isinstance(a, TypeParamRef) for a in t.inner_types())


def _has_type_param_ref_in_params(func: "FunctionInfo") -> bool:
    """Check if any parameter type in a FunctionInfo contains TypeParamRef."""
    return any(_has_type_param_ref(p.type) for p in func.params)


class CallAnalyzer:
    """Function and constructor call analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        protocols: ProtocolChecker,
        compat: TypeCompatibility,
        list_tracker: ListLiteralTracker,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols
        self.compat = compat
        self.list_tracker = list_tracker
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None

    def set_cross_deps(self, expr: ExpressionAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_call)."""
        self.expr = expr

    def _set_record_constructor_info(
        self,
        expr: TpyCall,
        record: RecordInfo,
        return_type: TpyType,
        type_subst: dict[str, TpyType] | None = None,
    ) -> None:
        """Attach resolved constructor metadata for readonly/effect checks."""
        init_overloads = record.get_method_overloads("__init__")
        if init_overloads:
            ctor = init_overloads[0]
            resolved_ctor = self.type_ops.substitute_method_type_params(ctor, type_subst) if type_subst else ctor
            expr.resolved_function_info = FunctionInfo(
                name=record.name,
                params=resolved_ctor.params,
                return_type=return_type,
                is_readonly=False,
            )
            return

        # Implicit default constructor (no user __init__)
        expr.resolved_function_info = FunctionInfo(
            name=record.name,
            params=[],
            return_type=return_type,
            is_readonly=False,
        )

    def analyze_call(self, expr: TpyCall) -> TpyType:
        """Analyze a function or constructor call."""
        # Handle super() call
        if expr.func == "super":
            from .methods import MethodAnalyzer
            return MethodAnalyzer._analyze_super_call_static(self.ctx, expr)

        # Generic type instantiation (e.g., Container[T, N](), StaticList[Int32, 8]())
        # Only if it's actually a type - for generic functions with uppercase names,
        # call_type may be set but we should use type_args instead
        if expr.call_type is not None:
            # Check if this is a user-defined generic function
            is_known_function = self.ctx.registry.get_function(expr.func) is not None
            if not is_known_function:
                # Check if it's a user-defined record - use _analyze_record_constructor for bound validation
                record = self.ctx.registry.get_record(expr.func)
                if record:
                    return self._analyze_record_constructor(expr, record)
                # It's a builtin type instantiation -- validate constructor args
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                if arg_types:
                    self._validate_generic_constructor(expr, arg_types)
                if isinstance(expr.call_type, (PtrType, ConstPtrType)) and expr.args:
                    self._validate_ptr_constructor(expr)
                    # Set resolved constructor for codegen (the &{0} template)
                    lookup = builtin_modules.lookup_generic_type(expr.func)
                    if lookup:
                        for ctor in lookup.type_def.constructors:
                            if len(ctor.params) == len(expr.args) and ctor.cpp:
                                expr.resolved_function_info = _method_def_to_function_info(ctor)
                                break
                    self._validate_lvalue_params(expr)
                # Builtin type constructors -- not readonly (constructor calls
                # are not allowed in readonly contexts; see READONLY_DESIGN.md)
                if expr.resolved_function_info is None:
                    expr.resolved_function_info = FunctionInfo(
                        name="__init__",
                        params=[],
                        return_type=expr.call_type,
                        is_readonly=False,
                    )
                return expr.call_type
            # Otherwise fall through to function handling (type_args will be used)

        # Check registry for built-in functions (global builtins like chr)
        if overloads := self.ctx.registry.get_builtin_function_overloads(expr.func):
            if not overloads[0].special_handling:
                return self._analyze_builtin_function_overloads(expr, overloads)

        # Track if we found an imported generic type (allows fallthrough to generic handling)
        # Stores the original name (not alias) for lookup_generic_type
        imported_generic_name: str | None = None

        # Use namespace for unified lookup - handles shadowing automatically
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(expr.func)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    raise self.ctx.error(f"'{expr.func}' is not callable", expr)
                elif binding.kind == BindingKind.FUNCTION:
                    return self._analyze_user_function_call(expr, binding.func_info)
                elif binding.kind == BindingKind.RECORD:
                    return self._analyze_record_constructor(expr, binding.record_info)
                elif binding.kind == BindingKind.IMPORTED_NAME:
                    module_name, func_name = binding.import_source
                    # Special handling for copy() from tpy - truly generic function
                    if module_name == "tpy" and func_name == "copy":
                        return self._analyze_tpy_copy(expr)
                    # Special handling for builtins with custom sema
                    if module_name == "builtins":
                        if func_name == "print":
                            for arg in expr.args:
                                self.expr.analyze_expr(arg)
                            expr.resolved_function_info = FunctionInfo(
                                name="print",
                                params=[],
                                return_type=VOID,
                                is_readonly=True,
                                is_builtin_function=True,
                                special_handling=True,
                                qualified_name="builtins.print",
                            )
                            return VOID
                        elif func_name in ("enumerate", "zip"):
                            raise SemanticError(f"{func_name}() is not yet implemented", expr.loc)
                    # Check for user module function (registered via _register_user_module_import)
                    if func_info := self.ctx.registry.get_function(expr.func):
                        return self._analyze_user_function_call(expr, func_info)
                    # Check for user module record (registered via _register_user_module_import)
                    if record_info := self.ctx.registry.get_record(expr.func):
                        return self._analyze_record_constructor(expr, record_info)
                    # Check for module function (e.g., math.sqrt)
                    from .registration import TypeRegistrar
                    if overloads := self._get_module_function_overloads(module_name, func_name):
                        if overloads[0].special_handling:
                            return self._analyze_special_builtin(expr, func_name, overloads)
                        return self._analyze_builtin_function_overloads(expr, overloads)
                    # Check for type constructor (e.g., Int32 from tpy, int from builtins)
                    qname = f"{module_name}.{func_name}"
                    if record_info := self.ctx.registry.get_builtin_record(qname):
                        if record_info.constructors and not record_info.type_params:
                            return self._check_builtin_constructor(expr, record_info)
                    # Generic types (StaticList, Array, list) - mark as found and fall through
                    if builtin_modules.lookup_generic_type(func_name):
                        imported_generic_name = func_name
                    else:
                        raise SemanticError(f"Unknown function '{func_name}' in module '{module_name}'", expr.loc)
                elif binding.kind == BindingKind.MODULE:
                    raise SemanticError(f"Cannot call module '{expr.func}' directly; use module.function()", expr.loc)
                elif binding.kind == BindingKind.BUILTIN:
                    raise SemanticError(f"'{expr.func}' is not callable", expr.loc)

        # Check if it's a tpy type that requires explicit import
        # Only check if we didn't find it in namespace (i.e., not imported)
        # Python builtins (int, str, list) are in builtins_ns and would be found above
        if imported_generic_name is None:
            tpy_qname = f"tpy.{expr.func}"
            if record_info := self.ctx.registry.get_builtin_record(tpy_qname):
                if record_info.constructors and not record_info.type_params:
                    raise self.ctx.error(
                        f"'{expr.func}' is not defined. Did you mean: from tpy import {expr.func}",
                        expr
                    )
            # Also check generic tpy types (StaticList, Array, Span)
            if lookup := builtin_modules.lookup_generic_type(expr.func):
                if lookup.qualified_name.startswith("tpy."):
                    raise self.ctx.error(
                        f"'{expr.func}' is not defined. Did you mean: from tpy import {expr.func}",
                        expr
                    )

        # Fallback: Check if it's a record constructor
        record = self.ctx.registry.get_record(expr.func)
        if record:
            # Analyze arguments
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            return NamedType(expr.func)

        # Fallback: Check if it's a function call
        func = self.ctx.registry.get_function(expr.func)
        if func:
            return self._analyze_legacy_function_call(expr, func)

        # Generic type constructor without context for type inference
        # Only proceed if we found an imported generic type in namespace
        if imported_generic_name and (lookup := builtin_modules.lookup_generic_type(imported_generic_name)):
            type_def = lookup.type_def
            params = ", ".join(type_def.type_params)

            # Check for constructors that can infer type from arguments
            if expr.args:
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                if type_def.constructors:
                    for ctor in type_def.constructors:
                        if len(ctor.params) != len(arg_types):
                            continue
                        # Try to match and infer type parameters
                        inferred_params = self.type_ops.match_generic_constructor(ctor.params, arg_types)
                        if inferred_params is not None:
                            # Use type_factory to create the result type
                            elem_type = inferred_params.get("T")
                            if elem_type and type_def.type_factory:
                                # Resolve IntLiteralType using configured default.
                                if isinstance(elem_type, IntLiteralType):
                                    elem_type = self.ctx.default_int_for_literal(elem_type)
                                result_type = type_def.type_factory(elem_type)
                                expr.call_type = result_type
                                if isinstance(result_type, (PtrType, ConstPtrType)):
                                    self._validate_ptr_constructor(expr)
                                if ctor.cpp:
                                    expr.resolved_function_info = _method_def_to_function_info(ctor)
                                self._validate_lvalue_params(expr)
                                return result_type

                # Show specific error when a non-literal type with element info
                # can't match any constructor (e.g., Range passed to list())
                if (len(arg_types) == 1
                        and not isinstance(arg_types[0], PendingListType)
                        and arg_types[0].get_element_type() is not None):
                    raise self.ctx.error(
                        f"{expr.func}() cannot be constructed from {arg_types[0]}",
                        expr
                    )
                raise self.ctx.error(
                    f"Cannot infer element type for {expr.func}() from these arguments; "
                    f"use {expr.func}[{params}]() or provide a type annotation",
                    expr
                )
            raise self.ctx.error(
                f"Cannot infer element type for {expr.func}(); "
                f"use {expr.func}[{params}](), provide a type annotation, or pass an iterable",
                expr
            )

        raise self.ctx.error(f"Unknown function or type: '{expr.func}'", expr)

    def _analyze_special_builtin(
        self, expr: TpyCall, func_name: str, overloads: list[FunctionInfo],
    ) -> TpyType:
        """Handle builtin functions with special_handling=True."""
        if func_name == "unsafe_cast":
            return self._analyze_unsafe_cast(expr)
        if func_name == "copy":
            return self._analyze_tpy_copy(expr)
        if func_name == "print":
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            expr.resolved_function_info = FunctionInfo(
                name="print",
                params=[],
                return_type=VOID,
                is_readonly=True,
                is_builtin_function=True,
                special_handling=True,
                qualified_name="builtins.print",
            )
            return VOID
        raise self.ctx.error(f"Unknown special builtin: '{func_name}'", expr)

    def _analyze_unsafe_cast(self, expr: TpyCall) -> TpyType:
        """Analyze unsafe_cast[T](ptr) or unsafe_cast(ptr) with annotation hint.

        Target type is determined by:
        1. Explicit type arg: unsafe_cast[UInt32](p) -> preserves pointer kind from arg
        2. Variable annotation: q: Ptr[UInt32] = unsafe_cast(p) -> uses full annotation type
        """
        if len(expr.args) != 1:
            raise self.ctx.error("unsafe_cast() takes exactly 1 argument", expr)

        arg_type = self.expr.analyze_expr(expr.args[0])
        if not isinstance(arg_type, (PtrType, ConstPtrType)):
            raise self.ctx.error(
                f"unsafe_cast() requires a Ptr or ConstPtr argument, got {arg_type}", expr
            )

        target_type: PtrType | ConstPtrType | None = None

        # 1. Explicit type arg: unsafe_cast[T](p)
        if expr.type_args:
            if len(expr.type_args) != 1:
                raise self.ctx.error("unsafe_cast() takes exactly 1 type argument", expr)
            pointee = expr.type_args[0]
            # Preserve pointer kind from arg
            if isinstance(arg_type, ConstPtrType):
                target_type = ConstPtrType(pointee)
            else:
                target_type = PtrType(pointee)

        # 2. Fall back to variable annotation hint
        if target_type is None:
            raw_hint = self.ctx.expr_type_hint
            if raw_hint is None:
                raise self.ctx.error(
                    "unsafe_cast() requires a type argument or target type annotation "
                    "(e.g., unsafe_cast[UInt32](p) or q: Ptr[UInt32] = unsafe_cast(p))", expr
                )
            hint = raw_hint
            if isinstance(hint, OwnType):
                hint = hint.wrapped
            hint = unwrap_readonly(hint)
            if not isinstance(hint, (PtrType, ConstPtrType)):
                raise self.ctx.error(
                    f"unsafe_cast() target must be Ptr[T] or ConstPtr[T], got {raw_hint}", expr
                )
            target_type = hint

        # reinterpret_cast cannot drop const -- use unsafe_const_cast first
        if isinstance(arg_type, ConstPtrType) and isinstance(target_type, PtrType):
            raise self.ctx.error(
                "unsafe_cast() cannot cast ConstPtr to Ptr (use unsafe_const_cast first)", expr
            )

        # cpp_template is built at codegen time so that native type names
        # (from _native_cpp_names) are resolved correctly across modules.
        expr.resolved_function_info = FunctionInfo(
            name="unsafe_cast",
            params=[ParamInfo("p", arg_type)],
            return_type=target_type,
            is_builtin_function=True,
            special_handling=True,
            qualified_name="tpy.unsafe.unsafe_cast",
        )
        return target_type

    def _get_module_function_overloads(self, module_name: str, func_name: str) -> list[FunctionInfo] | None:
        """Look up function overloads in a module using the unified registry."""
        module_info = self.ctx.registry.get_module(module_name)
        if module_info and func_name in module_info.functions:
            return module_info.functions[func_name]
        return None

    def _analyze_tpy_copy(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.copy() - explicit copy for ownership transfer.

        copy() is truly generic (works with any type T, returns Own[T]).
        This is handled specially because the module system doesn't support
        truly generic functions yet.
        """
        if len(expr.args) != 1:
            raise self.ctx.error("copy() takes exactly 1 argument", expr)
        arg_type = self.expr.analyze_expr(expr.args[0])
        # Unwrap OwnType if already wrapped
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        # @nocopy types cannot be copied (unwrap readonly to catch readonly[Handle])
        inner_type = unwrap_readonly(arg_type)
        if isinstance(inner_type, NamedType):
            record_info = self.ctx.registry.get_record(inner_type.name)
            if record_info and record_info.is_nocopy:
                raise self.ctx.error(
                    f"Cannot copy @nocopy type '{inner_type}'. "
                    f"@nocopy values can only be moved (pass directly at last use).",
                    expr,
                )
        expr.resolved_function_info = FunctionInfo(
            name="copy",
            params=[ParamInfo("x", arg_type)],
            return_type=OwnType(arg_type),
            is_readonly=True,
            is_builtin_function=True,
            qualified_name="tpy.copy",
        )
        return OwnType(arg_type)

    def _is_nocopy_type(self, typ: TpyType) -> bool:
        """Check if a type is @nocopy (move-only, copy deleted).

        Unwraps ReadonlyType so readonly[Handle] is detected as @nocopy.
        """
        inner = unwrap_readonly(typ)
        if isinstance(inner, NamedType):
            record_info = self.ctx.registry.get_record(inner.name)
            return record_info is not None and record_info.is_nocopy
        return False

    def _check_own_param_arg(self, arg: TpyExpr, arg_type: TpyType,
                              pname: str, ptype: OwnType) -> None:
        """Check lvalue passed to Own[T] param -- auto-move at last use or error."""
        is_last_use_movable = (isinstance(arg, TpyName)
                               and id(arg) in self.ctx.all_last_uses
                               and self.compat._is_movable_var(arg.name))
        if not is_last_use_movable:
            if self._is_nocopy_type(arg_type):
                is_movable = (isinstance(arg, TpyName)
                              and self.compat._is_movable_var(arg.name))
                if is_movable:
                    # Movable owner, but not at last use (used later)
                    raise self.ctx.error(
                        f"@nocopy type '{arg_type}' is used after this point "
                        f"and cannot be moved into '{pname}: Own[{ptype.wrapped}]'. "
                        f"Remove later uses or restructure the code.",
                        arg
                    )
                # Not movable (T& alias, readonly param, etc.)
                raise self.ctx.error(
                    f"@nocopy type '{arg_type}' cannot be copied into "
                    f"'{pname}: Own[{ptype.wrapped}]'. "
                    f"Only the original owner can be moved at its last use.",
                    arg
                )
            raise self.ctx.error(
                f"Cannot pass '{arg_type}' to parameter '{pname}: Own[{ptype.wrapped}]' "
                f"(would be implicit copy)",
                arg
            )

    def _warn_unnecessary_copy(self, arg: TpyExpr) -> None:
        """Warn when copy(x) is passed to Own[T] param but x is at last use."""
        if not (isinstance(arg, TpyCall) and len(arg.args) == 1
                and arg.resolved_function_info
                and arg.resolved_function_info.qualified_name == "tpy.copy"):
            return
        inner = arg.args[0]
        if (isinstance(inner, TpyName)
                and id(inner) in self.ctx.all_last_uses
                and self.compat._is_movable_var(inner.name)):
            self.ctx.warning(
                f"unnecessary copy() -- '{inner.name}' is at its last use and would be moved automatically",
                arg,
            )

    def _validate_generic_constructor(self, expr: TpyCall, arg_types: list[TpyType]) -> None:
        """Validate and resolve generic type constructor calls.

        When call_type is set (e.g., list[int](iterable)), checks that args
        conform to constructor params and sets resolved_function_info for codegen.
        For params with TypeParamRef (e.g. Span[T]), structural compatibility is
        checked (arg must be a container with matching element type) even though
        T itself is unresolved.
        """
        lookup = builtin_modules.lookup_generic_type(expr.func)
        if lookup is None or not lookup.type_def.constructors:
            return
        for ctor in lookup.type_def.constructors:
            if len(ctor.params) != len(arg_types):
                continue
            rejected = False
            fully_checked = True
            for p, at in zip(ctor.params, arg_types):
                if is_protocol_type(p.type):
                    if not builtin_modules.type_extends_any(at, p.type.name):
                        rejected = True
                        break
                elif _has_type_param_ref(p.type):
                    # Can't fully resolve T, but reject clearly incompatible
                    # types. For Span[T]: arg must have an element type, and
                    # if T is known from call_type, element types must match.
                    if isinstance(p.type, SpanType):
                        arg_elem = at.get_element_type()
                        if arg_elem is None:
                            rejected = True
                            break
                        expected_elem = expr.call_type.get_element_type() if expr.call_type else None
                        if expected_elem is not None and not type_matches_numeric(arg_elem, expected_elem):
                            rejected = True
                            break
                    fully_checked = False
                elif not type_matches_numeric(at, p.type):
                    rejected = True
                    break
            if not rejected:
                if fully_checked and ctor.cpp:
                    expr.resolved_function_info = _method_def_to_function_info(ctor)
                return
        # No constructor matched -- emit error for single-arg case
        if len(arg_types) == 1:
            raise self.ctx.error(
                f"{expr.func}() cannot be constructed from {arg_types[0]}",
                expr
            )

    def _validate_lvalue_params(self, expr: TpyCall) -> None:
        """Validate requires_lvalue / requires_mutable constraints on resolved params."""
        fi = expr.resolved_function_info
        if fi is None:
            return
        for i, param in enumerate(fi.params):
            if i >= len(expr.args):
                break
            if param.requires_mutable:
                if not self.compat.is_mutable_lvalue(expr.args[i]):
                    raise self.ctx.error(
                        f"argument '{param.name}' must be a mutable lvalue", expr)
            elif param.requires_lvalue:
                if not self.compat.is_lvalue(expr.args[i]):
                    raise self.ctx.error(
                        f"argument '{param.name}' must be an lvalue", expr)

    def _validate_ptr_constructor(self, expr: TpyCall) -> None:
        """Validate Ptr/ConstPtr constructor arguments (type match, no void args).

        Lvalue checking is handled generically by _validate_lvalue_params.
        """
        assert isinstance(expr.call_type, (PtrType, ConstPtrType))
        pointee = expr.call_type.pointee
        kind = "Ptr" if isinstance(expr.call_type, PtrType) else "ConstPtr"

        if len(expr.args) != 1:
            raise self.ctx.error(f"{kind}() takes 0 or 1 argument, got {len(expr.args)}", expr)

        arg = expr.args[0]
        arg_type = self.ctx.get_expr_type(arg)

        if isinstance(pointee, VoidType):
            raise self.ctx.error(f"{kind}[None]() does not accept arguments", expr)

        if arg_type != pointee:
            raise self.ctx.error(
                f"{kind}[{pointee}]() expects {pointee}, got {arg_type}", expr)

    def _check_builtin_constructor(self, expr: TpyCall, record_info: RecordInfo) -> TpyType:
        """Check a builtin type constructor call using unified RecordInfo.constructors."""
        type_name = expr.func
        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]

        # Find a matching constructor overload
        for ctor in record_info.constructors:
            if len(ctor.params) != len(arg_types):
                continue
            if all(type_matches_numeric(arg_type, ptype)
                   for (pname, ptype), arg_type in zip(ctor.params, arg_types)):
                # Reject int literals that are out of range for the target fixed-int type
                if (isinstance(ctor.return_type, FixedIntType) and len(arg_types) == 1
                        and isinstance(arg_types[0], IntLiteralType)):
                    lit = arg_types[0]
                    target = ctor.return_type
                    if lit.value is not None and not (target.min_value <= lit.value <= target.max_value):
                        raise self.ctx.error(
                            f"{target} overflow: {lit.value} is outside range "
                            f"[{target.min_value}, {target.max_value}]",
                            expr,
                        )
                expr.resolved_function_info = ctor
                return ctor.return_type

        # No matching overload found
        if not record_info.constructors:
            raise self.ctx.error(f"{type_name}() is not callable", expr)
        elif len(arg_types) == 0:
            raise self.ctx.error(f"{type_name}() requires an argument", expr)
        elif len(arg_types) == 1:
            raise self.ctx.error(f"{type_name}() cannot convert {arg_types[0]}", expr)
        else:
            raise self.ctx.error(f"{type_name}() takes at most 1 argument, got {len(arg_types)}", expr)

    def _analyze_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> TpyType:
        """Type-check a call to a builtin function using unified FunctionInfo overloads.

        Uses two-pass overload resolution: prefer exact type matches over coercion matches.
        For generic overloads (with type_params), uses type inference.
        """
        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
        protocol_checker = self.protocols.type_conforms_to_protocol

        # Split overloads: non-generic use standard resolution, generic use inference
        non_generic = [o for o in overloads if not _has_type_param_ref_in_params(o)]
        generic = [o for o in overloads if _has_type_param_ref_in_params(o)]

        # Try non-generic overloads first (standard two-pass resolution)
        if non_generic:
            matched = resolve_overload(non_generic, arg_types, protocol_checker,
                                       deref_checker=self.type_ops.get_deref_coercion_target,
                                       default_int_type=self.ctx.default_int_type)
            if matched is not None:
                expr.resolved_function_info = matched
                for i, (arg, arg_t, (pname, ptype)) in enumerate(zip(expr.args, arg_types, matched.params)):
                    if arg_t != ptype:
                        expr.args[i] = self.compat.coerce_expr(arg, arg_t, ptype,
                                                                f"argument '{pname}'",
                                                                coercion_ctx=CoercionContext.ARG)
                return matched.return_type

        # Try generic overloads with type inference
        for overload in generic:
            type_subst = self.type_ops.infer_type_params_for_function(
                overload, arg_types, protocol_checker,
                expected_return_type=self.ctx.expr_type_hint,
            )
            if type_subst is not None:
                resolved = self.type_ops.substitute_method_type_params(overload, type_subst)
                # Resolve type param placeholders in cpp_template (e.g. {T} -> int32_t)
                if resolved.cpp_template and "{" in resolved.cpp_template:
                    for name, typ in type_subst.items():
                        placeholder = f"{{{name}}}"
                        if placeholder in resolved.cpp_template and hasattr(typ, "to_cpp"):
                            resolved.cpp_template = resolved.cpp_template.replace(
                                placeholder, typ.to_cpp()
                            )
                expr.resolved_function_info = resolved
                expr.inferred_type_args = tuple(type_subst[p] for p in overload.type_params)
                for i, (arg, arg_t, (pname, ptype)) in enumerate(zip(expr.args, arg_types, resolved.params)):
                    if arg_t != ptype:
                        expr.args[i] = self.compat.coerce_expr(arg, arg_t, ptype,
                                                                f"argument '{pname}'",
                                                                coercion_ctx=CoercionContext.ARG)
                return resolved.return_type

        # No matching overload found - try to give a helpful error
        arg_type_strs = ", ".join(str(t) for t in arg_types)

        # For generic overloads, check for conflicting type parameter inference
        for overload in generic:
            if len(arg_types) != len(overload.params):
                continue
            partial: dict[str, TpyType] = {}
            conflict_param = None
            for (pname, ptype), arg_t in zip(overload.params, arg_types):
                before = dict(partial)
                if not self.type_ops.match_type_with_inference(ptype, arg_t, partial):
                    # Find which type param conflicted
                    for tp in overload.type_params:
                        if tp in before:
                            expected = before[tp]
                            # Try to extract what this arg would infer
                            trial: dict[str, TpyType] = {}
                            self.type_ops.match_type_with_inference(ptype, arg_t, trial)
                            if tp in trial and trial[tp] != expected:
                                conflict_param = (tp, expected, trial[tp], pname)
                                break
                    break
            if conflict_param:
                tp_name, first_t, second_t, param_name = conflict_param
                raise self.ctx.error(
                    f"No matching overload for {expr.func}({arg_type_strs}): "
                    f"type parameter {tp_name} inferred as {first_t} and {second_t}",
                    expr
                )

        # Check for ConstPtr passed at a position where all overloads expect Ptr
        for i, arg_t in enumerate(arg_types):
            if isinstance(arg_t, ConstPtrType):
                all_need_ptr_at_i = all(
                    i < len(o.params) and isinstance(o.params[i].type, PtrType)
                    for o in overloads
                )
                if all_need_ptr_at_i:
                    raise self.ctx.error(
                        f"{expr.func}() requires a mutable Ptr, got {arg_t}", expr
                    )

        raise self.ctx.error(f"No matching overload for {expr.func}({arg_type_strs})", expr)

    def _analyze_user_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a user-defined function."""
        # Handle generic functions
        if func.is_generic():
            return self._analyze_generic_function_call(expr, func)

        expr.resolved_function_info = func
        if len(expr.args) != len(func.params):
            raise self.ctx.error(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}", expr)
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            # Handle list() constructor - infer type from parameter
            arg_type = self.expr.analyze_expr_with_hint(arg, ptype)

            # Check for Own[T] passed directly to object type parameter
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType) and not ptype.is_value_type():
                if isinstance(arg, TpyName):
                    hint = f"Declare the variable as '{arg_type.wrapped}' instead of 'Own[{arg_type.wrapped}]'"
                else:
                    hint = "Assign to a variable first: x = func(); other_func(x)"
                raise self.ctx.error(
                    f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                    f"(object types are passed by reference). {hint}",
                    arg
                )

            # Check for T passed to Own[T] parameter - would be implicit copy
            if isinstance(ptype, OwnType) and not isinstance(arg_type, OwnType) and not arg_type.is_value_type():
                self._check_own_param_arg(arg, arg_type, pname, ptype)

            if isinstance(ptype, OwnType):
                self._warn_unnecessary_copy(arg)

            # Special case: single-char string literal can be passed as Char
            if not (isinstance(ptype, CharType) and isinstance(arg_type, StrType) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            # Track parameter context for list inference
            if isinstance(arg_type, PendingListType):
                self.list_tracker.mark_list_param_context(arg, ptype)

        return func.return_type

    def _analyze_generic_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a generic function."""
        # Check for invalid type arguments (e.g., first[123](x) or first[var](x))
        if expr.type_args_parse_error:
            raise self.ctx.error(expr.type_args_parse_error, expr)

        # Check argument count first
        if len(expr.args) != len(func.params):
            raise self.ctx.error(
                f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}",
                expr
            )

        # Get type substitution from explicit args or inference
        if expr.type_args:
            # Explicit: first[Int32](items)
            if len(expr.type_args) != len(func.type_params):
                raise self.ctx.error(
                    f"Function '{expr.func}' expects {len(func.type_params)} type arguments, "
                    f"got {len(expr.type_args)}",
                    expr
                )
            # Validate each explicit type argument
            for i, type_arg in enumerate(expr.type_args):
                # Protocol types cannot be used as type arguments
                if is_protocol_type(type_arg):
                    raise self.ctx.error(
                        f"Protocol type '{type_arg.name}' cannot be used as a type argument. "
                        f"Protocols are only valid for function parameters",
                        expr
                    )
                # Check for unknown record types (no forward references allowed at call sites)
                if isinstance(type_arg, NamedType) and type_arg.is_record and not type_arg.type_args:
                    if self.ctx.registry.get_record(type_arg.name) is None:
                        raise self.ctx.error(f"Unknown type: {type_arg.name}", expr)
                # Validate the type (checks for missing generic args, etc.)
                in_generic = bool(
                    (isinstance(self.ctx.current_function, TpyFunction) and self.ctx.current_function.type_params)
                    or self.ctx.record_ctx.type_params
                )
                self.type_ops.validate_type(type_arg, allow_type_param_ref=in_generic, loc=expr.loc)
            type_subst = dict(zip(func.type_params, expr.type_args))
            # Validate type parameter bounds
            for param_name, type_arg in type_subst.items():
                if param_name in func.type_param_bounds:
                    bound = func.type_param_bounds[param_name]
                    if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                        raise self.ctx.error(
                            f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                            f"for type parameter '{param_name}' of '{func.name}'",
                            expr
                        )
        else:
            # Infer from arguments
            arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
            type_subst = self.type_ops.infer_type_params_for_function(
                func, arg_types, self.protocols.type_conforms_to_protocol,
                expected_return_type=self.ctx.expr_type_hint,
            )
            if type_subst is None:
                raise self.ctx.error(
                    f"Cannot infer type arguments for '{func.name}'. "
                    f"Specify explicitly: {func.name}[{', '.join(func.type_params)}](...)",
                    expr
                )

        # Store inferred type args for codegen
        expr.inferred_type_args = tuple(type_subst[p] for p in func.type_params)

        # Resolve and check parameters
        resolved_func = self.type_ops.substitute_method_type_params(func, type_subst)
        expr.resolved_function_info = resolved_func
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst)
            arg_type = self.expr.analyze_expr_with_hint(arg, resolved_ptype)

            # Check for Own[T] passed directly to object type parameter
            if isinstance(arg_type, OwnType) and not isinstance(resolved_ptype, OwnType) and not resolved_ptype.is_value_type():
                if isinstance(arg, TpyName):
                    hint = f"Declare the variable as '{arg_type.wrapped}' instead of 'Own[{arg_type.wrapped}]'"
                else:
                    hint = "Assign to a variable first: x = func(); other_func(x)"
                raise self.ctx.error(
                    f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                    f"(object types are passed by reference). {hint}",
                    arg
                )

            # Check for T passed to Own[T] parameter - would be implicit copy
            if isinstance(resolved_ptype, OwnType) and not isinstance(arg_type, OwnType) and not arg_type.is_value_type():
                self._check_own_param_arg(arg, arg_type, pname, resolved_ptype)

            if isinstance(resolved_ptype, OwnType):
                self._warn_unnecessary_copy(arg)

            # Special case: single-char string literal can be passed as Char
            if not (isinstance(resolved_ptype, CharType) and isinstance(arg_type, StrType) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, resolved_ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            # Track parameter context for list inference
            if isinstance(arg_type, PendingListType):
                self.list_tracker.mark_list_param_context(arg, resolved_ptype)

        # Resolve return type
        return self.type_ops.substitute_type_params(func.return_type, type_subst)

    def _analyze_record_constructor(self, expr: TpyCall, record: RecordInfo) -> TpyType:
        """Analyze a call to a record constructor."""
        # Check if this is a generic record instantiation (e.g., Stack[Int32]())
        if expr.call_type is not None and isinstance(expr.call_type, NamedType) and expr.call_type.is_record:
            # Validate type arguments
            if record.is_generic():
                if not expr.call_type.type_args:
                    # No explicit type args -- fall through to inference section
                    expr.call_type = None
                else:
                    if len(expr.call_type.type_args) != len(record.type_params):
                        raise self.ctx.error(
                            f"Record '{record.name}' expects {len(record.type_params)} type arguments, "
                            f"got {len(expr.call_type.type_args)}",
                            expr
                        )
                    # Validate type parameter bounds
                    for param_name, type_arg in zip(record.type_params, expr.call_type.type_args):
                        if param_name in record.type_param_bounds:
                            bound = record.type_param_bounds[param_name]
                            if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                                raise self.ctx.error(
                                    f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                                    f"for type parameter '{param_name}' of '{record.name}'",
                                    expr
                                )
        if expr.call_type is not None and isinstance(expr.call_type, NamedType) and expr.call_type.is_record:
            # Analyze and type-check constructor arguments with type substitution
            type_subst = self.type_ops.build_type_substitution(expr.call_type)
            if record.has_init:
                # Type-check __init__ parameters
                if len(expr.args) != len(record.init_params):
                    raise self.ctx.error(
                        f"{record.name}() takes {len(record.init_params)} argument(s), got {len(expr.args)}",
                        expr
                    )
                for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                    arg_type = self.expr.analyze_expr(arg)
                    resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst) if type_subst else ptype
                    expr.args[i] = self.compat.coerce_expr(arg, arg_type, resolved_ptype, f"argument '{pname}'",
                                                           coercion_ctx=CoercionContext.ARG)
            else:
                for arg in expr.args:
                    self.expr.analyze_expr(arg)
            self._set_record_constructor_info(expr, record, expr.call_type, type_subst)
            return expr.call_type
        # Generic record without explicit type args - try type inference
        if record.is_generic():
            if record.has_init:
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                inferred = self.type_ops.infer_type_params_for_record(
                    record, arg_types, expected_type=self.ctx.expr_type_hint,
                )
                if inferred:
                    # Resolve pending types for codegen.
                    for k, v in list(inferred.items()):
                        if isinstance(v, IntLiteralType):
                            inferred[k] = self.ctx.default_int_for_literal(v)
                        elif isinstance(v, PendingListType):
                            # Resolve PendingListType to ListType
                            elem_type = v.element_type
                            if isinstance(elem_type, IntLiteralType):
                                elem_type = self.ctx.default_int_for_literal(elem_type)
                            inferred[k] = ListType(elem_type)
                    # Validate type parameter bounds
                    for param_name, type_arg in inferred.items():
                        if param_name in record.type_param_bounds:
                            bound = record.type_param_bounds[param_name]
                            if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                                raise self.ctx.error(
                                    f"Inferred type '{type_arg}' does not satisfy bound '{bound}' "
                                    f"for type parameter '{param_name}' of '{record.name}'",
                                    expr
                                )
                    type_args = tuple(inferred[p] for p in record.type_params)
                    # Use expr.func (local name) not record.name (original) for alias support
                    inferred_type = NamedType(expr.func, type_args)
                    expr.call_type = inferred_type
                    # Coerce arguments with substitution
                    type_subst = inferred
                    for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                        resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst)
                        expr.args[i] = self.compat.coerce_expr(
                            arg, arg_types[i], resolved_ptype,
                            f"argument '{pname}'", coercion_ctx=CoercionContext.ARG
                        )
                    self._set_record_constructor_info(expr, record, inferred_type, type_subst)
                    return inferred_type
            else:
                # No __init__ -- try contextual inference only
                if self.ctx.expr_type_hint is not None:
                    inferred: dict[str, TpyType] = {}
                    exp = self.ctx.expr_type_hint
                    if isinstance(exp, OwnType):
                        exp = exp.wrapped
                    record_pattern = NamedType(record.name, tuple(
                        TypeParamRef(tp) for tp in record.type_params
                    ))
                    if self.type_ops.match_type_with_inference(record_pattern, exp, inferred):
                        if all(tp in inferred for tp in record.type_params):
                            # Validate type parameter bounds
                            for param_name, type_arg in inferred.items():
                                if param_name in record.type_param_bounds:
                                    bound = record.type_param_bounds[param_name]
                                    if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                                        raise self.ctx.error(
                                            f"Inferred type '{type_arg}' does not satisfy bound '{bound}' "
                                            f"for type parameter '{param_name}' of '{record.name}'",
                                            expr
                                        )
                            type_args = tuple(inferred[p] for p in record.type_params)
                            inferred_type = NamedType(expr.func, type_args)
                            expr.call_type = inferred_type
                            self._set_record_constructor_info(expr, record, inferred_type, inferred)
                            for arg in expr.args:
                                self.expr.analyze_expr(arg)
                            return inferred_type
            # Inference failed - require explicit type args
            raise self.ctx.error(
                f"Cannot infer type arguments for '{record.name}'. "
                f"Please specify explicitly: {record.name}[{', '.join(record.type_params)}](...)",
                expr
            )
        # Non-generic record
        if record.has_init:
            # Type-check __init__ parameters
            if len(expr.args) != len(record.init_params):
                raise self.ctx.error(
                    f"{record.name}() takes {len(record.init_params)} argument(s), got {len(expr.args)}",
                    expr
                )
            for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)
        else:
            for arg in expr.args:
                self.expr.analyze_expr(arg)
        # Use expr.func (local name) not record.name (original) for alias support
        result_type = NamedType(expr.func)
        self._set_record_constructor_info(expr, record, result_type)
        return result_type

    def _analyze_legacy_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a legacy function call (fallback path)."""
        expr.resolved_function_info = func
        if len(expr.args) != len(func.params):
            raise self.ctx.error(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}", expr)
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            # Handle list() constructor - infer type from parameter
            arg_type = self.expr.analyze_expr_with_hint(arg, ptype)

            # Check for Own[T] passed directly to object type parameter
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType) and not ptype.is_value_type():
                if isinstance(arg, TpyName):
                    hint = f"Declare the variable as '{arg_type.wrapped}' instead of 'Own[{arg_type.wrapped}]'"
                else:
                    hint = "Assign to a variable first: x = func(); other_func(x)"
                raise self.ctx.error(
                    f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                    f"(object types are passed by reference). {hint}",
                    arg
                )

            # Check for T passed to Own[T] parameter - would be implicit copy
            if isinstance(ptype, OwnType) and not isinstance(arg_type, OwnType) and not arg_type.is_value_type():
                self._check_own_param_arg(arg, arg_type, pname, ptype)

            if isinstance(ptype, OwnType):
                self._warn_unnecessary_copy(arg)

            # Special case: single-char string literal can be passed as Char
            if not (isinstance(ptype, CharType) and isinstance(arg_type, StrType) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            # Track parameter context for list inference
            if isinstance(arg_type, PendingListType):
                self.list_tracker.mark_list_param_context(arg, ptype)

        return func.return_type
