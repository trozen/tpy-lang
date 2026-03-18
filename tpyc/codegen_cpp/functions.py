"""
TurboPython Function Code Generation

Generates C++ function declarations, definitions, and global variables.
"""

from __future__ import annotations
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, ReadonlyType, OptionalType, PendingListType, ListType, ArrayType, IntLiteralType,
    UnionType,
    BIGINT, is_protocol_type, FunctionInfo, TypeParamRef, unwrap_readonly, is_constexpr_eligible,
    Int32Type, BoolType, FloatType, Float32Type, CharType, PtrType, StrType, is_any_str_type, SpanType,
    resolve_int_literals, CONST_PARAMS_METHODS,
    error_return_to_cpp,
)
from ..parse import TpyFunction, TpyVarDecl, VarLinkage
from ..parse.nodes import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral,
    TpyNoneLiteral, TpyUnaryOp, TpyTypeParamConstruct, TpyCall,
)
from ..namespace import Namespace
from .context import INDENT, module_to_cpp_namespace, escape_cpp_name
from .type_resolution import resolve_stmt_type_cascade

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .protocols import ProtocolGenerator, ProtocolParamInfo
    from .statements import StatementGenerator

_FIXED_INT_NAMES = {
    "Int8", "Int16", "Int32", "Int64",
    "UInt8", "UInt16", "UInt32", "UInt64",
    "int", "float", "bool",
}


def factory_default_to_cpp(field_type: TpyType) -> str:
    """Return the C++ default value for a factory-default field.

    User records need explicit ctor call (their ctors are explicit);
    containers (list, dict, etc.) use aggregate init.
    """
    inner = field_type.wrapped if isinstance(field_type, OwnType) else field_type
    if isinstance(inner, NamedType) and inner.is_user_record:
        return f"{inner.to_cpp()}()"
    return "{}"


class FunctionGenerator:
    """Generates C++ functions, methods, and globals."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        protocols: ProtocolGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.protocols = protocols
        # Will be set after statements is created
        self.statements: StatementGenerator | None = None

    def set_statements(self, statements: StatementGenerator):
        """Set statements generator (to break circular dependency)."""
        self.statements = statements

    @staticmethod
    def default_to_cpp(expr: TpyExpr, ptype: TpyType) -> str:
        """Convert a constant default expression to its C++ representation."""
        if isinstance(expr, TpyTypeParamConstruct):
            return f"{expr.param_name}{{}}"
        if isinstance(expr, TpyIntLiteral):
            return str(expr.value)
        if isinstance(expr, TpyFloatLiteral):
            v = repr(expr.value)
            if '.' not in v and 'e' not in v and 'E' not in v:
                v += '.0'
            return v
        if isinstance(expr, TpyBoolLiteral):
            return "true" if expr.value else "false"
        if isinstance(expr, TpyStrLiteral):
            if isinstance(ptype, CharType) and len(expr.value) == 1:
                ch = expr.value[0]
                if ch == "'":
                    return "'\\''"
                if ch == '\\':
                    return "'\\\\'"
                return f"'{ch}'"
            escaped = (expr.value
                       .replace('\\', '\\\\')
                       .replace('"', '\\"')
                       .replace('\n', '\\n')
                       .replace('\r', '\\r')
                       .replace('\t', '\\t'))
            return f'"{escaped}"'
        if isinstance(expr, TpyNoneLiteral):
            if isinstance(ptype, OptionalType) and not ptype.uses_pointer_repr():
                return "std::nullopt"
            return "nullptr"
        if isinstance(expr, TpyUnaryOp) and expr.op == "-":
            inner = FunctionGenerator.default_to_cpp(expr.operand, ptype)
            return f"-{inner}"
        if isinstance(expr, TpyCall):
            # Int32(5) -> just the literal value
            if expr.args:
                return FunctionGenerator.default_to_cpp(expr.args[0], ptype)
            # Zero-arg call: Int32() -> 0, list()/dict()/Record() -> {}
            if expr.func in _FIXED_INT_NAMES:
                return "0"
            return factory_default_to_cpp(ptype)
        return "0"

    def gen_params(self, params: list[tuple[str, TpyType]],
                   func_type_params: list[str] | None = None,
                   *, const_params: bool = False,
                   reassigned_params: set[str] | None = None,
                   mutated_params: frozenset[int] | None = None,
                   defaults: list | None = None,
                   emit_defaults: bool = False,
                   class_type_params: set[str] | None = None) -> str:
        """Generate function parameter list.

        Own[T] params are emitted as T&& (rvalue ref) for concrete T, or
        std::type_identity_t<T>&& for template T (prevents forwarding-ref
        deduction). Callers always pass std::move() at last use; non-last-use
        copies are inserted by gen_call_arg with a warning.

        const_params: use to_cpp_const_param (const T& for generics). Needed
        for constructors and const method overloads that must accept temporaries.

        reassigned_params: params that are reassigned in the function body.
        Types normally passed as const ref (BigInt, str) get a renamed C++
        param (__param_X) so the body can shadow it with a mutable local copy.

        mutated_params: frozenset of param indices with confirmed mutations.
        When provided, non-mutated params that are not reassigned get const T&
        instead of T& (read-only binding). None = conservative (T& for all).

        defaults: list of TpyExpr | None aligned with params.
        emit_defaults: if True, append ' = <value>' for params with defaults.
        """
        parts = []
        for i, (pname, ptype) in enumerate(params):
            cpp_pname = escape_cpp_name(pname)
            own = unwrap_readonly(ptype)
            if (reassigned_params and pname in reassigned_params
                    and ptype.param_needs_copy_for_reassign()):
                # Rename param so the body can declare a mutable local with the original name
                part = ptype.to_cpp_param(f"__param_{cpp_pname}")
            elif const_params:
                part = ptype.to_cpp_const_param(cpp_pname)
            elif (mutated_params is not None and i not in mutated_params
                    and (ptype.is_ref_param()
                         or (isinstance(ptype, UnionType) and ptype.uses_pointer_repr()))
                    and not isinstance(ptype, TypeParamRef)
                    and not (reassigned_params and pname in reassigned_params)):
                # Param is provably not mutated and not rebound -- safe to use const.
                # For ref params: T& -> const T&.
                # For pointer-variant unions: variant<T*...> -> const variant<T*...>
                # (const on the variant, not on the pointers -- no conversion issues).
                # TypeParamRef excluded: its to_cpp_param() uses ::tpy::param_val_or_ref_t<T>
                # (trait-based), not T& directly; to_cpp_const_param() gives const T& which
                # differs in ABI for value-type instantiations.
                # Guard on reassigned_params: rebinding (param = x) would generate p = x
                # in C++, which is copy-assignment through the reference on a const ref --
                # a compile error. Only skip this guard for types that copy on reassign
                # (BigInt, str), which are already handled by param_needs_copy_for_reassign.
                part = ptype.to_cpp_const_param(cpp_pname)
            else:
                part = ptype.to_cpp_param(cpp_pname)
            # Own[T] where T is a class-level type param: std::type_identity_t is
            # redundant (T is already bound, T&& is a plain rvalue ref, not forwarding).
            # Only function-level type params need the deduction guard.
            if (isinstance(ptype, OwnType) and isinstance(ptype.wrapped, TypeParamRef)
                    and class_type_params is not None
                    and ptype.wrapped.name in class_type_params):
                tp_cpp = ptype.wrapped.to_cpp()
                part = part.replace(f"std::type_identity_t<{tp_cpp}>&&", f"{tp_cpp}&&")
            if emit_defaults and defaults and i < len(defaults) and defaults[i] is not None:
                part += f" = {self.default_to_cpp(defaults[i], ptype)}"
            parts.append(part)
        return ", ".join(parts)

    def gen_c_params(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate parameter list for extern \"C\" declarations.

        Uses C-compatible types: str maps to const char* instead of
        std::string_view (which is not ABI-compatible with C).
        """
        parts = []
        for pname, ptype in params:
            cpp_pname = escape_cpp_name(pname)
            if is_any_str_type(ptype):
                parts.append(f"const char* {cpp_pname}")
            else:
                parts.append(ptype.to_cpp_param(cpp_pname))
        return ", ".join(parts)

    def gen_params_with_protocols(self, params: list[tuple[str, TpyType]],
                                   func_type_params: list[str] | None = None,
                                   *, const_params: bool = False,
                                   mutated_params: frozenset[int] | None = None,
                                   defaults: list | None = None,
                                   emit_defaults: bool = False) -> str:
        """Generate function parameter list, using template types for protocol params.

        Static protocols use template types (T_paramname).
        @dynamic protocols use concrete base class& reference params.
        Nullable protocol params emit const T_paramname* (pointer repr).
        const_params: emit const T_x& for static protocols, const Base& for @dynamic,
        and to_cpp_const_param for non-protocol params.
        mutated_params: frozenset of param indices with confirmed mutations (same
        semantics as gen_params). When provided, non-mutated protocol params use
        const T_x& / const Base& instead of T_x& / Base&.
        """
        result = []
        for i, (pname, ptype) in enumerate(params):
            cpp_pname = escape_cpp_name(pname)
            unwrapped = unwrap_readonly(ptype)
            if self.protocols.is_static_protocol_param(ptype):
                # Unified static protocol handling (single, optional, or union)
                info = self._find_protocol_param_info(pname, ptype)
                if info and info.has_none:
                    part = f"const T_{pname}* {cpp_pname}"
                elif (const_params or isinstance(ptype, ReadonlyType)
                        or self._all_protocols_readonly(info)
                        or (mutated_params is not None and i not in mutated_params)):
                    part = f"const T_{pname}& {cpp_pname}"
                else:
                    part = f"T_{pname}& {cpp_pname}"
            else:
                # Check for @dynamic protocol
                inner_unwrapped = unwrapped.inner if isinstance(unwrapped, OptionalType) else unwrapped
                resolved = self.protocols.resolve_type_for_codegen(inner_unwrapped)
                if (is_protocol_type(resolved)
                        and (pi := self.ctx.analyzer.registry.get_protocol(resolved.name))
                        and pi.is_dynamic):
                    base_type = self.protocols.get_dynamic_base_name(resolved.name)
                    if (isinstance(ptype, ReadonlyType)
                            or (mutated_params is not None and i not in mutated_params)):
                        part = f"const {base_type}& {cpp_pname}"
                    else:
                        part = f"{base_type}& {cpp_pname}"
                else:
                    part = ptype.to_cpp_const_param(cpp_pname) if const_params else ptype.to_cpp_param(cpp_pname)
            if emit_defaults and defaults and i < len(defaults) and defaults[i] is not None:
                part += f" = {self.default_to_cpp(defaults[i], ptype)}"
            result.append(part)
        return ", ".join(result)

    def _find_protocol_param_info(self, pname: str, ptype: TpyType):
        """Find ProtocolParamInfo for a single param (helper for gen_params_with_protocols)."""
        infos = self.protocols.get_all_protocol_params([(pname, ptype)])
        return infos[0] if infos else None

    def _all_protocols_readonly(self, info: ProtocolParamInfo | None) -> bool:
        """Check if all protocols in a ProtocolParamInfo are functionally const."""
        if info is None or not info.protocols:
            return False
        return all(self.protocols.is_protocol_const(p.name) for p in info.protocols)

    def _resolve_return_type(self, return_type: TpyType, *, const: bool = False,
                             error_return: str | None = None) -> str:
        """Map a return type to C++, using Base& for @dynamic protocols.

        If error_return is set, wraps the return type in std::expected<T, E>.
        """
        cpp_error = error_return_to_cpp(error_return) if error_return else None
        unwrapped = unwrap_readonly(return_type)
        if is_protocol_type(unwrapped) and isinstance(unwrapped, NamedType):
            pi = self.ctx.analyzer.registry.get_protocol(unwrapped.name)
            if pi and pi.is_dynamic:
                base = self.protocols.get_dynamic_base_name(unwrapped.name)
                if const or isinstance(return_type, ReadonlyType):
                    ret = f"const {base}&"
                else:
                    ret = f"{base}&"
                if cpp_error:
                    return f"std::expected<{ret}, {cpp_error}>"
                return ret
        if const:
            ret = return_type.to_cpp_return_const()
        else:
            ret = return_type.to_cpp_return()
        if cpp_error:
            return f"std::expected<{ret}, {cpp_error}>"
        return ret

    def _build_const_ref_params(
        self,
        params: list[tuple[str, 'TpyType']],
        mutated_params: 'frozenset[int] | None',
        reassigned_params: 'set[str] | None' = None,
        use_const_params: bool = False,
    ) -> set[str]:
        """Return the set of param names that will be emitted as const T& in C++.

        Mirrors the constness logic in gen_params() so that statement codegen
        can emit explicit const T& / T& for element-borrow locals instead of auto&.
        """
        from ..typesys import TypeParamRef
        result: set[str] = set()
        if use_const_params:
            for pname, ptype in params:
                if ((ptype.is_ref_param()
                     or (isinstance(ptype, UnionType) and ptype.uses_pointer_repr()))
                        and not isinstance(ptype, TypeParamRef)):
                    result.add(pname)
            return result
        if mutated_params is None:
            return result
        for i, (pname, ptype) in enumerate(params):
            if (i not in mutated_params
                    and (ptype.is_ref_param()
                         or (isinstance(ptype, UnionType) and ptype.uses_pointer_repr()))
                    and not isinstance(ptype, TypeParamRef)
                    and not (reassigned_params and pname in reassigned_params)):
                result.add(pname)
        return result

    def _get_reassigned_params(self, func: TpyFunction) -> set[str] | None:
        """Get the set of param names reassigned in the function body, or None."""
        scan = self.ctx.analyzer.function_scan_results.get(id(func))
        if not scan:
            return None
        param_names = {pname for pname, _ in func.params}
        result = scan.reassigned & param_names
        return result if result else None

    def _get_func_mutated_params(self, func: TpyFunction) -> frozenset[int] | None:
        """Return finalized mutated_params for a free function, or None if unavailable."""
        # Declaration-only stubs (native, extern_c) have no body -- no mutation analysis.
        # @overload stubs share a name with their implementation; overloads[-1] is the
        # implementation's FI, which has the analyzed mutated_params.
        if func.is_stub and not func.is_overload_stub:
            return None
        overloads = self.ctx.analyzer.registry.get_function(func.name)
        if overloads:
            return overloads[-1].mutated_params
        return None

    def _get_method_mutated_params(self, method: TpyFunction, record_name: str) -> frozenset[int] | None:
        """Return finalized mutated_params for a record method, or None if unavailable."""
        if method.is_stub or method.is_overload_stub:
            return None
        record_info = self.ctx.analyzer.registry.get_record(record_name)
        if record_info:
            overloads = record_info.get_method_overloads(method.name)
            if overloads:
                return overloads[-1].mutated_params
        return None

    def _has_dynamic_protocol_params(self, params: list[tuple[str, TpyType]]) -> bool:
        """Check if any params are @dynamic protocol types (need Base& codegen)."""
        for _, ptype in params:
            unwrapped = unwrap_readonly(ptype)
            if isinstance(unwrapped, OptionalType):
                unwrapped = unwrapped.inner
            resolved = self.protocols.resolve_type_for_codegen(unwrapped)
            if is_protocol_type(resolved):
                protocol_info = self.ctx.analyzer.registry.get_protocol(resolved.name)
                if protocol_info and protocol_info.is_dynamic:
                    return True
        return False

    def is_template_function(self, func: TpyFunction) -> bool:
        """Check if a function needs a C++ template (generic type params or protocol params)."""
        if func.type_params:
            return True
        if self.protocols.get_all_protocol_params(func.params):
            return True
        return False

    def gen_function_forward_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate a function forward declaration (signature only, no body).

        Emitted before record definitions so that inline constructor/method
        bodies can call free functions declared later in the header.
        Returns True if a declaration was emitted.
        """
        from ..parse.nodes import FunctionLinkage
        if func.linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C, FunctionLinkage.EXTERN_C):
            return False
        if func.cpp_template:
            return False
        # @overload implementation: emit forward decls for each stub instead
        overload_stubs = self.ctx.analyzer.overload_groups.get(id(func))
        if overload_stubs:
            for stub in overload_stubs:
                self._gen_function_forward_decl_single(out, stub)
            return True
        if func.is_stub and not func.is_overload_stub:
            return False
        # @overload stubs encountered directly: skip (handled via implementation)
        if func.is_overload_stub:
            return False

        self._gen_function_forward_decl_single(out, func)
        return True

    def _gen_function_forward_decl_single(self, out: TextIO, func: TpyFunction) -> None:
        """Emit a single function forward declaration."""
        proto_params = self.protocols.get_all_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)
        rp = self._get_reassigned_params(func)
        mp = self._get_func_mutated_params(func)
        has_proto_params = bool(proto_params)

        dfl = func.defaults if func.defaults else None
        if is_generic or has_proto_params:
            out.write(self.protocols.gen_combined_template_header(
                func.type_params, proto_params, func.type_param_bounds,
            ))
            ret_type = self._resolve_return_type(func.return_type, error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params, func.type_params,
                                                     mutated_params=mp,
                                                     defaults=dfl, emit_defaults=True)
                      if has_proto_params or has_dynamic
                      else self.gen_params(func.params, func.type_params, reassigned_params=rp,
                                           mutated_params=mp, defaults=dfl, emit_defaults=True))
            out.write(f"{ret_type} {escape_cpp_name(func.name)}({params});\n")
        else:
            ret_type = self._resolve_return_type(func.return_type, error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params,
                                                     mutated_params=mp,
                                                     defaults=dfl, emit_defaults=True)
                      if has_dynamic
                      else self.gen_params(func.params, func.type_params, reassigned_params=rp,
                                           mutated_params=mp, defaults=dfl, emit_defaults=True))
            out.write(f"{ret_type} {escape_cpp_name(func.name)}({params});\n")

    def gen_function_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate a function declaration (or full definition for template functions).

        Non-template non-stub functions are skipped (already forward-declared).
        Returns True if something was emitted.
        """
        from ..parse.nodes import FunctionLinkage
        if func.linkage == FunctionLinkage.NATIVE:
            return False
        # @cpp_template functions expand inline at call sites -- no C++ declaration
        if func.cpp_template:
            return False

        # @overload stubs are handled via the implementation function
        if func.is_overload_stub:
            return False

        # @overload implementation with template params: emit specialized defs in header
        overload_stubs = self.ctx.analyzer.overload_groups.get(id(func))
        if overload_stubs:
            is_generic = bool(func.type_params)
            has_proto_params = bool(self.protocols.get_all_protocol_params(func.params))
            if is_generic or has_proto_params:
                for stub in overload_stubs:
                    self._gen_overload_specialized_function(out, func, stub)
                    out.write("\n")
                return True
            return False

        # @native_c and @extern_c both use extern "C" linkage
        if func.linkage in (FunctionLinkage.NATIVE_C, FunctionLinkage.EXTERN_C):
            c_name = func.native_name or func.name
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_c_params(func.params)
            out.write(f'extern "C" {ret_type} {c_name}({params});\n')
            return True

        proto_params = self.protocols.get_all_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)
        rp = self._get_reassigned_params(func)
        mp = self._get_func_mutated_params(func)
        has_proto_params = bool(proto_params)

        if is_generic or has_proto_params:
            # Template functions: emit full definition in header so that
            # importing modules can instantiate them.
            if func.is_stub:
                out.write(self.protocols.gen_combined_template_header(
                    func.type_params, proto_params, func.type_param_bounds,
                ))
                ret_type = self._resolve_return_type(func.return_type)
                params = (self.gen_params_with_protocols(func.params, func.type_params,
                                                         mutated_params=mp)
                          if has_proto_params or has_dynamic
                          else self.gen_params(func.params, func.type_params,
                                               reassigned_params=rp, mutated_params=mp))
                out.write(f"{ret_type} {escape_cpp_name(func.name)}({params});\n")
            else:
                self.gen_function_def(out, func)
            return True

        # Non-template non-stub: already forward-declared
        if not func.is_stub:
            return False
        ret_type = self._resolve_return_type(func.return_type, error_return=func.error_return)
        params = (self.gen_params_with_protocols(func.params, mutated_params=mp) if has_dynamic
                  else self.gen_params(func.params, func.type_params,
                                       reassigned_params=rp, mutated_params=mp))
        out.write(f"{ret_type} {escape_cpp_name(func.name)}({params});\n")
        return True

    def gen_extern_c_redecl(self, out: TextIO, func_info: FunctionInfo) -> None:
        """Emit an extern "C" re-declaration for a C-linkage function.

        This makes the C symbol visible in the current namespace without
        needing to trace through re-export chains or cross-module using
        declarations. Legal because extern "C" functions can be declared
        multiple times.
        """
        c_name = func_info.native_name or func_info.name
        ret_type = func_info.return_type.to_cpp_return()
        params = self.gen_c_params(func_info.params)
        out.write(f'extern "C" {ret_type} {c_name}({params});\n')

    def gen_function_def(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function definition."""
        from ..parse.nodes import FunctionLinkage
        # @overload stubs have no body -- skip (the implementation emits all overloads)
        if func.is_overload_stub:
            return
        # Stubs have no body -- declaration only
        if func.is_stub:
            return
        # @native (C++ import) exports are handled outside the namespace by generator.py
        if func.linkage == FunctionLinkage.NATIVE:
            return

        # @overload implementation: emit per-stub specialized functions
        overload_stubs = self.ctx.analyzer.overload_groups.get(id(func))
        if overload_stubs:
            for stub in overload_stubs:
                self._gen_overload_specialized_function(out, func, stub)
                out.write("\n")
            return

        self.ctx.emit_preceding_comments(out, func.loc)
        self.ctx.emit_source_comment(out, func.loc)

        if func.linkage == FunctionLinkage.EXTERN_C:
            c_name = func.native_name or func.name
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_c_params(func.params)
            out.write(f'extern "C" {ret_type} {c_name}({params}) {{\n')

            local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
            for pname, ptype in func.params:
                local_ns.bind_variable(pname, ptype)
            self.statements.gen_body(out, func.body, func.params, func.return_type,
                                     func, local_ns)
            out.write("}\n")
            return

        proto_params = self.protocols.get_all_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)
        rp = self._get_reassigned_params(func)
        mp = self._get_func_mutated_params(func)
        has_proto_params = bool(proto_params)

        if is_generic or has_proto_params:
            # Generate combined template header for generic functions and/or protocol params
            # Skip default template args -- already emitted in the forward declaration
            out.write(self.protocols.gen_combined_template_header(
                func.type_params, proto_params, func.type_param_bounds,
                emit_defaults=False,
            ))
            ret_type = self._resolve_return_type(func.return_type, error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params, func.type_params,
                                                     mutated_params=mp)
                      if has_proto_params or has_dynamic
                      else self.gen_params(func.params, func.type_params,
                                           reassigned_params=rp, mutated_params=mp))
            out.write(f"{ret_type} {escape_cpp_name(func.name)}({params}) {{\n")
        else:
            ret_type = self._resolve_return_type(func.return_type, error_return=func.error_return)
            params = (self.gen_params_with_protocols(func.params, mutated_params=mp) if has_dynamic
                      else self.gen_params(func.params, func.type_params,
                                           reassigned_params=rp, mutated_params=mp))
            out.write(f"{ret_type} {escape_cpp_name(func.name)}({params}) {{\n")

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.statements.gen_body(out, func.body, func.params, func.return_type,
                                 func, local_ns,
                                 const_ref_params=self._build_const_ref_params(func.params, mp, rp))

        out.write("}\n")

    def _gen_overload_specialized_function(
        self, out: TextIO, impl: TpyFunction, stub: TpyFunction,
    ) -> None:
        """Generate a specialized C++ function for one @overload stub.

        Uses the implementation's body but with the stub's parameter types
        and return type. Sets overload_param_types so dead branch elimination
        kicks in for isinstance/match checks.
        """
        self.ctx.emit_preceding_comments(out, stub.loc)
        self.ctx.emit_source_comment(out, stub.loc)

        # Build the overload param type map: param_name -> concrete type
        overload_types: dict[str, TpyType] = {}
        for (impl_pname, impl_ptype), (stub_pname, stub_ptype) in zip(impl.params, stub.params):
            if isinstance(impl_ptype, UnionType) and not isinstance(stub_ptype, UnionType):
                overload_types[stub_pname] = stub_ptype

        is_generic = bool(impl.type_params)
        rp = self._get_reassigned_params(impl)
        mp = self._get_func_mutated_params(impl)
        proto_params = self.protocols.get_all_protocol_params(stub.params)
        has_dynamic = self._has_dynamic_protocol_params(stub.params)
        has_proto_params = bool(proto_params)

        if is_generic or has_proto_params:
            out.write(self.protocols.gen_combined_template_header(
                impl.type_params, proto_params, impl.type_param_bounds,
                emit_defaults=False,
            ))
            ret_type = self._resolve_return_type(stub.return_type)
            params = (self.gen_params_with_protocols(stub.params, impl.type_params,
                                                     mutated_params=mp)
                      if has_proto_params or has_dynamic
                      else self.gen_params(stub.params, impl.type_params,
                                           reassigned_params=rp, mutated_params=mp))
            out.write(f"{ret_type} {escape_cpp_name(stub.name)}({params}) {{\n")
        else:
            ret_type = self._resolve_return_type(stub.return_type)
            params = (self.gen_params_with_protocols(stub.params, mutated_params=mp) if has_dynamic
                      else self.gen_params(stub.params, impl.type_params,
                                           reassigned_params=rp, mutated_params=mp))
            out.write(f"{ret_type} {escape_cpp_name(stub.name)}({params}) {{\n")

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in stub.params:
            local_ns.bind_variable(pname, ptype)

        # Set overload context so dead branch elimination kicks in
        self.ctx.overload_param_types = overload_types
        try:
            self.statements.gen_body(out, impl.body, stub.params, stub.return_type,
                                     impl, local_ns,
                                     const_ref_params=self._build_const_ref_params(stub.params, mp, rp))
        finally:
            self.ctx.overload_param_types = {}

        out.write("}\n")

    def _gen_overload_specialized_method(
        self, out: TextIO, impl: TpyFunction, stub: TpyFunction,
        record_name: str, *,
        record_type_param_bounds: dict[str, TpyType] | None = None,
        dynamic_overrides: dict[str, bool] | None = None,
    ) -> None:
        """Generate a specialized C++ method for one @overload stub.

        Delegates to _gen_method_overload with stub's signature but impl's body,
        with the overload context set for dead branch elimination.
        """
        # Build overload param type map
        overload_types: dict[str, TpyType] = {}
        for (impl_pname, impl_ptype), (stub_pname, stub_ptype) in zip(impl.params, stub.params):
            if isinstance(impl_ptype, UnionType) and not isinstance(stub_ptype, UnionType):
                overload_types[stub_pname] = stub_ptype

        # Create a synthetic TpyFunction with stub's types but impl's body.
        # Method const-ness (is_readonly) comes from the stub, since the stub's
        # decorators (@readonly, @auto_readonly) define the overload's const contract.
        # With parser cloning, @auto_readonly stubs are already split into separate
        # mutable/const clones before codegen runs.
        synth = TpyFunction(
            name=stub.name,
            params=list(stub.params),
            return_type=stub.return_type,
            body=impl.body,
            is_method=impl.is_method,
            is_staticmethod=impl.is_staticmethod,
            is_readonly=stub.is_readonly,
            readonly_opt_out=stub.readonly_opt_out,
            is_pure=impl.is_pure,
            is_consuming=impl.is_consuming,
            type_params=list(impl.type_params),
            type_param_bounds=dict(impl.type_param_bounds),
            defaults=list(stub.defaults) if stub.defaults else [],
            loc=stub.loc,
        )
        # Copy scan results from impl so codegen can find pre-scan data
        self.ctx.analyzer.function_scan_results[id(synth)] = (
            self.ctx.analyzer.function_scan_results.get(id(impl), None)
        )
        if id(impl) in self.ctx.analyzer.function_hoisted_vars:
            self.ctx.analyzer.function_hoisted_vars[id(synth)] = (
                self.ctx.analyzer.function_hoisted_vars[id(impl)]
            )
        if id(impl) in self.ctx.analyzer.function_move_through_vars:
            self.ctx.analyzer.function_move_through_vars[id(synth)] = (
                self.ctx.analyzer.function_move_through_vars[id(impl)]
            )

        # Set overload context
        self.ctx.overload_param_types = overload_types
        try:
            self.gen_method_def(out, synth, record_name, dynamic_overrides,
                                record_type_param_bounds=record_type_param_bounds)
        finally:
            self.ctx.overload_param_types = {}

    def _get_dynamic_override_info(self, record_name: str) -> dict[str, bool]:
        """Get map of method_name -> is_const for methods overriding @dynamic protocol virtuals.

        Collects the full inherited surface (own + ancestor methods) since the C++
        base class emits pure virtuals for all inherited protocol methods.
        """
        record_info = self.ctx.analyzer.registry.get_record(record_name)
        if not record_info:
            return {}
        result: dict[str, bool] = {}
        for proto in record_info.implemented_protocols:
            proto_info = self.ctx.analyzer.registry.get_protocol(proto.name)
            if proto_info and proto_info.is_dynamic:
                all_methods = self.protocols.collect_concept_methods(proto.name)
                for method_sig in all_methods:
                    is_const = method_sig.is_readonly or proto_info.is_readonly
                    result[method_sig.name] = is_const
        return result

    def gen_method_def(self, out: TextIO, method: TpyFunction, record_name: str,
                       dynamic_overrides: dict[str, bool] | None = None,
                       record_type_param_bounds: dict[str, TpyType] | None = None) -> None:
        """Generate a method definition inside a struct."""
        cpp_name = method.name
        cpp_return_type = method.return_type

        is_const = method.is_readonly
        is_static = method.is_staticmethod

        # Determine if this method overrides a @dynamic protocol virtual
        override_const: bool | None = None
        if dynamic_overrides and method.name in dynamic_overrides:
            override_const = dynamic_overrides[method.name]

        if is_const and not is_static:
            # @readonly: single const overload. C++ allows calling const methods
            # on non-const objects, so no non-const duplicate needed.
            is_override = override_const is True
            self._gen_method_overload(out, method, record_name, cpp_name, cpp_return_type,
                                      const=True, override=is_override,
                                      record_type_param_bounds=record_type_param_bounds)
        else:
            is_override = override_const is False and not is_static  # base is non-const
            self._gen_method_overload(out, method, record_name, cpp_name, cpp_return_type, const=False,
                                      static=is_static, override=is_override,
                                      record_type_param_bounds=record_type_param_bounds)

    def _gen_method_overload(
        self, out: TextIO, method: TpyFunction, record_name: str,
        cpp_name: str, cpp_return_type: TpyType, *, const: bool, static: bool = False,
        override: bool = False, record_type_param_bounds: dict[str, TpyType] | None = None,
    ) -> None:
        """Emit a single method overload (const or non-const)."""
        # Inplace dunders return T& (reference to self) in C++
        is_inplace_dunder = method.name in CONST_PARAMS_METHODS
        if is_inplace_dunder:
            ret_type = f"{escape_cpp_name(record_name)}&"
        elif override and is_any_str_type(cpp_return_type):
            # @dynamic protocol virtual returns std::string; override must match
            # even if the impl declares -> StrView or -> String.
            ret_type = "std::string"
        else:
            ret_type = self._resolve_return_type(cpp_return_type, const=const,
                                                  error_return=method.error_return)
        dfl = method.defaults if method.defaults else None

        proto_params = self.protocols.get_all_protocol_params(method.params)
        has_dynamic = self._has_dynamic_protocol_params(method.params)
        use_protocol_params = bool(proto_params) or has_dynamic

        # Determine method-level type params (not in the class template)
        record_info = self.ctx.analyzer.registry.get_record(record_name)
        class_type_params = set(record_info.type_params) if record_info and record_info.type_params else set()
        new_method_params = [tp for tp in (method.type_params or []) if tp not in class_type_params]
        class_param_bounds = {tp: method.type_param_bounds[tp]
                              for tp in (method.type_params or [])
                              if tp in class_type_params and tp in method.type_param_bounds}

        # Inplace dunders (__iadd__ etc.) mutate self but take const params
        use_const_params = const or method.name in CONST_PARAMS_METHODS
        rp = self._get_reassigned_params(method)
        mp = self._get_method_mutated_params(method, record_name)
        if use_protocol_params:
            if use_const_params:
                params = self.gen_params_with_protocols(method.params, method.type_params,
                                                        const_params=True,
                                                        defaults=dfl, emit_defaults=True)
            else:
                params = self.gen_params_with_protocols(method.params, method.type_params,
                                                        mutated_params=mp,
                                                        defaults=dfl, emit_defaults=True)
        else:
            ctp = class_type_params or None
            if use_const_params:
                params = self.gen_params(method.params, method.type_params, const_params=True,
                                         defaults=dfl, emit_defaults=True,
                                         class_type_params=ctp)
            else:
                params = self.gen_params(method.params, method.type_params,
                                         reassigned_params=rp, mutated_params=mp,
                                         defaults=dfl, emit_defaults=True,
                                         class_type_params=ctp)
        const_suffix = " const" if const else ""
        rvalue_suffix = " &&" if method.is_consuming else ""
        override_suffix = " override" if override else ""
        static_prefix = "static " if static else ""

        # Build requires clause for per-method bounds on class type params
        requires_clause = ""
        if class_param_bounds:
            req_parts = []
            for tp, bound in class_param_bounds.items():
                concept_name = self.protocols.get_concept_name(bound)
                if bound.type_args:
                    type_args_cpp = ", ".join(t.to_cpp() for t in bound.type_args)
                    req_parts.append(f"{concept_name}<{type_args_cpp}, {tp}>")
                else:
                    req_parts.append(f"{concept_name}<{tp}>")
            requires_clause = f"\n{INDENT}  requires {' && '.join(req_parts)}"

        out.write("\n")
        self.ctx.emit_preceding_comments(out, method.loc, indent=INDENT)
        self.ctx.emit_source_comment(out, method.loc, indent=INDENT)
        if proto_params or new_method_params:
            # Bounds for new method type params only (class param bounds go on the requires clause)
            bounds_for_header = dict(record_type_param_bounds) if record_type_param_bounds else {}
            bounds_for_header.update(
                {k: v for k, v in method.type_param_bounds.items()
                 if k in set(new_method_params)}
            )
            template_header = self.protocols.gen_combined_template_header(
                new_method_params, proto_params, bounds_for_header,
            )
            out.write(f"{INDENT}{template_header}")
        out.write(f"{INDENT}{static_prefix}{ret_type} {cpp_name}({params}){const_suffix}{rvalue_suffix}{override_suffix}{requires_clause} {{\n")

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        if not static:
            local_ns.bind_variable("self", NamedType(record_name))
        for pname, ptype in method.params:
            local_ns.bind_variable(pname, ptype)
        # Consuming methods on types with __del__: suppress destructor at method entry.
        # Walk the parent chain since __del__ may be inherited.
        if method.is_consuming and record_info is not None and self.ctx.record_or_ancestor_has_del(record_name):
            out.write(f"{INDENT}{INDENT}this->__tpy_owned_ = false;\n")

        prev_consuming = self.ctx.in_consuming_method
        self.ctx.in_consuming_method = method.is_consuming
        self.statements.gen_body(out, method.body, method.params, method.return_type,
                                 method, local_ns, indent_level=2, is_method=True,
                                 record_type_param_bounds=record_type_param_bounds,
                                 const_ref_params=self._build_const_ref_params(
                                     method.params, mp, rp, use_const_params))
        self.ctx.in_consuming_method = prev_consuming

        out.write(f"{INDENT}}}\n")

    def gen_body(self, *args, **kwargs) -> None:
        """Delegate to StatementGenerator.gen_body()."""
        self.statements.gen_body(*args, **kwargs)

    def _resolve_global_type(self, stmt: TpyVarDecl) -> TpyType:
        """Resolve the type of a global variable, unwrapping Own[T]/Optional[T] to T."""
        if stmt.type:
            var_type = stmt.type
        elif stmt.init:
            var_type = resolve_stmt_type_cascade(stmt, self.ctx.analyzer, self.types)
        else:
            raise RuntimeError(f"Global '{stmt.name}' has no type and no initializer")
        if isinstance(var_type, OwnType):
            var_type = var_type.wrapped
        # Optional non-value types use inner type (pointer-global adds T*)
        elif isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
            var_type = var_type.inner
        # Resolve IntLiteralType in all composite types (tuples, arrays, lists)
        var_type = resolve_int_literals(var_type, self.ctx.analyzer.ctx.default_int_for_literal)
        return var_type

    def _global_cpp_type(self, var_type: TpyType) -> str:
        """Map a global variable type to C++.

        @dynamic protocol types use the base class name instead of the concept
        template placeholder, since globals need a concrete pointer type.
        """
        if is_protocol_type(var_type) and isinstance(var_type, NamedType):
            pi = self.ctx.analyzer.registry.get_protocol(var_type.name)
            if pi and pi.is_dynamic:
                return self.protocols.get_dynamic_base_name(var_type.name)
        return var_type.to_cpp()

    def gen_global_decl(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a global variable definition in source file.

        Value-type globals are plain T, non-value-type globals are T* (nullptr).
        Initialization happens in __tpy_init() to ensure proper execution order.
        """
        if stmt.linkage != VarLinkage.DEFAULT:
            return
        self.ctx.emit_preceding_comments(out, stmt.loc)
        self.ctx.emit_source_comment(out, stmt.loc)
        var_type = self._resolve_global_type(stmt)
        cpp_type = self._global_cpp_type(var_type)
        if var_type.is_value_type():
            # C++ primitives need explicit zero-init; class types (BigInt, string_view) don't
            init = "{}" if isinstance(var_type, (Int32Type, BoolType, FloatType, Float32Type, CharType, PtrType)) else ""
            out.write(f"{cpp_type} {stmt.name}{init};\n")
        else:
            out.write(f"{cpp_type}* {stmt.name}{{}};\n")

    def gen_global_extern(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate an extern declaration for a global variable in header file."""
        var_type = self._resolve_global_type(stmt)
        cpp_type = self._global_cpp_type(var_type)
        if var_type.is_value_type():
            out.write(f"extern {cpp_type} {stmt.name};\n")
        else:
            out.write(f"extern {cpp_type}* {stmt.name};\n")

    def _gen_final_init_expr(self, stmt: TpyVarDecl, var_type: TpyType) -> str:
        """Generate the initializer expression for a Final global."""
        return self.statements.expressions.gen_expr(stmt.init, var_type)

    def gen_final_global_header(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a Final global declaration in header file.

        Constexpr-eligible types: inline constexpr T NAME = VALUE;
        BigInt: extern const ::tpy::BigInt NAME;

        Special case: Final[str] uses std::string_view (string literals have
        static lifetime, constexpr requires literal type).
        """
        var_type = self._resolve_global_type(stmt)
        cpp_type = var_type.to_cpp()
        # Final[str] -> constexpr std::string_view (string literals are static)
        if isinstance(var_type, StrType):
            cpp_type = "std::string_view"
        if is_constexpr_eligible(var_type):
            init_expr = self._gen_final_init_expr(stmt, var_type)
            out.write(f"inline constexpr {cpp_type} {stmt.name} = {init_expr};\n")
        else:
            # BigInt and other non-constexpr types: extern const in header
            out.write(f"extern const {cpp_type} {stmt.name};\n")

    def gen_final_global_source(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a Final global definition in source file.

        Only needed for non-constexpr types (BigInt). Constexpr types are
        fully defined in the header via inline constexpr.
        """
        var_type = self._resolve_global_type(stmt)
        if is_constexpr_eligible(var_type):
            return  # Defined in header via inline constexpr
        cpp_type = var_type.to_cpp()
        init_expr = self._gen_final_init_expr(stmt, var_type)
        out.write(f"const {cpp_type} {stmt.name} = {init_expr};\n")

    def gen_module_init_decl(self, out: TextIO) -> None:
        """Generate module init function declaration in header."""
        out.write("void __tpy_init();\n")

    def gen_module_init(self, out: TextIO, stmts: list, global_types: dict[str, TpyType | None] | None = None,
                        has_user_main: bool = False, module_name: str = "__main__") -> None:
        """Generate module init function containing top-level statements.

        Args:
            out: Output stream.
            stmts: Top-level statements (including TpyImport for user module imports).
            global_types: Dict of global variable names to types.
            has_user_main: If True, call main() at end.
            module_name: Value for __name__ ("__main__" for entry point, module name otherwise).
        """
        out.write("void __tpy_init() {\n")
        # Guard against double initialization (handles diamond dependencies)
        out.write(f"{INDENT}static bool initialized = false;\n")
        out.write(f"{INDENT}if (initialized) return;\n")
        out.write(f"{INDENT}initialized = true;\n\n")

        self.ctx.reset_scope()
        # Pre-seed with global names and types so re-declarations become assignments
        if global_types:
            self.ctx.declared_vars = set(global_types.keys())
            self.ctx.var_types = {name: typ for name, typ in global_types.items() if typ is not None}
            self.ctx.pointer_locals = {
                name for name, typ in global_types.items()
                if typ and not typ.is_value_type()
            }
        self.ctx.slots.reset(global_scope=True)
        scan = self.ctx.analyzer.top_level_scan_result
        if scan:
            self.ctx.reassigned_vars = scan.reassigned - self.ctx.global_declared_vars
            self.ctx.rvalue_reassigned_vars = scan.rvalue_reassigned - self.ctx.global_declared_vars
            self.ctx.lvalue_reassigned_vars = scan.lvalue_reassigned - self.ctx.global_declared_vars
        else:
            self.ctx.reassigned_vars = set()
            self.ctx.rvalue_reassigned_vars = set()
            self.ctx.lvalue_reassigned_vars = set()
        self.ctx.hoisted_vars = self.ctx.analyzer.top_level_hoisted_vars.copy()
        self.ctx.move_through_vars = self.ctx.analyzer.top_level_move_through_vars.copy()
        self.ctx.current_ns = self.ctx.analyzer.global_ns
        self.ctx.indent_level = 1

        self.statements._gen_buffered_body(out, stmts, track_stmt_line=True)

        self.ctx.current_ns = None
        if has_user_main:
            out.write(f"{INDENT}main();\n")
        out.write("}\n\n")

    def gen_namespace_close(self, out: TextIO) -> None:
        """Close the namespace in source file (for non-entry-point modules)."""
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n")

    def gen_main(self, out: TextIO) -> None:
        """Generate C++ main() that calls module init.

        The namespace is closed before main() so main is in global namespace.
        Accepts argc/argv and initializes ::tpy::sys_argv for sys.argv support.
        """
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n\n")
        out.write("int main(int argc, char* argv[]) {\n")
        out.write(f"{INDENT}::tpy::init_sys_argv(argc, argv);\n")
        out.write(f"{INDENT}{ns}::__tpy_init();\n")
        out.write(f"{INDENT}return 0;\n")
        out.write("}\n")

    def gen_extern_cpp_source_def(self, out: TextIO, func: TpyFunction) -> None:
        """Generate an extern_cpp export definition outside the tpyapp namespace (in source).

        Wraps the definition in the appropriate namespace and adds a using-directive
        to access the tpyapp module symbols.
        """
        if func.is_stub:
            return
        cpp_name = func.native_name or func.name
        ret_type = func.return_type.to_cpp_return()
        params = self.gen_params(func.params, func.type_params)
        ns, bare_name = self._split_extern_cpp_name(cpp_name)

        self.ctx.emit_preceding_comments(out, func.loc)
        self.ctx.emit_source_comment(out, func.loc)

        tpy_ns = module_to_cpp_namespace(self.ctx.module_name)
        if ns:
            out.write(f"namespace {ns} {{\n")
            out.write(f"{ret_type} {bare_name}({params}) {{\n")
            out.write(f"{INDENT}using namespace {tpy_ns};\n")
        else:
            out.write(f"{ret_type} {bare_name}({params}) {{\n")
            out.write(f"{INDENT}using namespace {tpy_ns};\n")

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.statements.gen_body(out, func.body, func.params, func.return_type,
                                 func, local_ns)

        out.write("}\n")
        if ns:
            out.write(f"}} // namespace {ns}\n")

    @staticmethod
    def _split_native_name(name: str) -> tuple[str, str]:
        """Split a qualified C++ name into (namespace, bare_name).

        'physics::calc' -> ('physics', 'calc')
        'a::b::func'    -> ('a::b', 'func')
        'func'           -> ('', 'func')
        """
        idx = name.rfind("::")
        if idx == -1:
            return ("", name)
        return (name[:idx], name[idx + 2:])

    _split_extern_cpp_name = _split_native_name
