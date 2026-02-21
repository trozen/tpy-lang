"""
TurboPython Function Code Generation

Generates C++ function declarations, definitions, and global variables.
"""

from __future__ import annotations
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, ReadonlyType, OptionalType, PendingListType, ListType, ArrayType, IntLiteralType,
    BIGINT, is_protocol_type, FunctionInfo, TypeParamRef, unwrap_readonly,
    Int32Type, BoolType, FloatType, CharType, PtrType, ConstPtrType,
)
from ..parse import TpyFunction, TpyVarDecl, VarLinkage
from ..namespace import Namespace
from .context import module_to_cpp_namespace
from .type_resolution import resolve_stmt_type_cascade

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .protocols import ProtocolGenerator
    from .statements import StatementGenerator



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

    def gen_params(self, params: list[tuple[str, TpyType]],
                   func_type_params: list[str] | None = None,
                   *, const_params: bool = False) -> str:
        """Generate function parameter list.

        Own[T] uses T&& (forwarding ref) only when T is a function-level type
        param (deduced at the call site). For class-level type params (methods,
        constructors), T is already bound at instantiation so T&& would be an
        rvalue ref -- fall through to T by value instead.

        const_params: use to_cpp_const_param (const T& for generics). Needed
        for constructors and const method overloads that must accept temporaries.
        """
        parts = []
        for pname, ptype in params:
            own = unwrap_readonly(ptype)
            if (isinstance(own, OwnType) and isinstance(own.wrapped, TypeParamRef)
                    and func_type_params and own.wrapped.name in func_type_params):
                parts.append(f"{own.wrapped.name}&& {pname}")
            elif const_params:
                parts.append(ptype.to_cpp_const_param(pname))
            else:
                parts.append(ptype.to_cpp_param(pname))
        return ", ".join(parts)

    def gen_c_params(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate parameter list for extern \"C\" declarations.

        Uses C-compatible types: str maps to const char* instead of
        std::string_view (which is not ABI-compatible with C).
        """
        from ..typesys import StrType
        parts = []
        for pname, ptype in params:
            if isinstance(ptype, StrType):
                parts.append(f"const char* {pname}")
            else:
                parts.append(ptype.to_cpp_param(pname))
        return ", ".join(parts)

    def gen_params_with_protocols(self, params: list[tuple[str, TpyType]],
                                   func_type_params: list[str] | None = None) -> str:
        """Generate function parameter list, using template types for protocol params.

        Static protocols use template types (T_paramname).
        @dynamic protocols use concrete __tpy_Base& reference params.
        """
        result = []
        for pname, ptype in params:
            # Resolve type in case it's a NamedType that's actually a protocol
            unwrapped = unwrap_readonly(ptype)
            if (isinstance(unwrapped, OwnType) and isinstance(unwrapped.wrapped, TypeParamRef)
                    and func_type_params and unwrapped.wrapped.name in func_type_params):
                result.append(f"{unwrapped.wrapped.name}&& {pname}")
            else:
                resolved = self.protocols.resolve_type_for_codegen(unwrapped)
                if is_protocol_type(resolved):
                    protocol_info = self.ctx.analyzer.registry.get_protocol(resolved.name)
                    if protocol_info and protocol_info.is_dynamic:
                        base_type = f"__tpy_{resolved.name}_Base"
                        if isinstance(ptype, ReadonlyType):
                            result.append(f"const {base_type}& {pname}")
                        else:
                            result.append(f"{base_type}& {pname}")
                    else:
                        if isinstance(ptype, ReadonlyType):
                            result.append(f"const T_{pname}& {pname}")
                        else:
                            result.append(f"T_{pname}& {pname}")
                else:
                    result.append(ptype.to_cpp_param(pname))
        return ", ".join(result)

    def _has_dynamic_protocol_params(self, params: list[tuple[str, TpyType]]) -> bool:
        """Check if any params are @dynamic protocol types (need Base& codegen)."""
        for _, ptype in params:
            unwrapped = unwrap_readonly(ptype)
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
        return bool(self.protocols.get_protocol_params(func.params))

    def gen_function_forward_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate a function forward declaration (signature only, no body).

        Emitted before record definitions so that inline constructor/method
        bodies can call free functions declared later in the header.
        Returns True if a declaration was emitted.
        """
        from ..parse.nodes import FunctionLinkage
        if func.linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C, FunctionLinkage.EXTERN_C):
            return False
        if func.is_stub:
            return False

        protocol_params = self.protocols.get_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)

        if is_generic or protocol_params:
            out.write(self.protocols.gen_combined_template_header(
                func.type_params, protocol_params, func.type_param_bounds
            ))
            ret_type = func.return_type.to_cpp_return()
            params = (self.gen_params_with_protocols(func.params, func.type_params)
                      if protocol_params or has_dynamic else self.gen_params(func.params, func.type_params))
            out.write(f"{ret_type} {func.name}({params});\n")
        else:
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_params_with_protocols(func.params) if has_dynamic else self.gen_params(func.params, func.type_params)
            out.write(f"{ret_type} {func.name}({params});\n")
        return True

    def gen_function_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate a function declaration (or full definition for template functions).

        Non-template non-stub functions are skipped (already forward-declared).
        Returns True if something was emitted.
        """
        from ..parse.nodes import FunctionLinkage
        if func.linkage == FunctionLinkage.NATIVE:
            return False

        # @native_c and @extern_c both use extern "C" linkage
        if func.linkage in (FunctionLinkage.NATIVE_C, FunctionLinkage.EXTERN_C):
            c_name = func.native_name or func.name
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_c_params(func.params)
            out.write(f'extern "C" {ret_type} {c_name}({params});\n')
            return True

        protocol_params = self.protocols.get_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)

        if is_generic or protocol_params:
            # Template functions: emit full definition in header so that
            # importing modules can instantiate them.
            if func.is_stub:
                out.write(self.protocols.gen_combined_template_header(
                    func.type_params, protocol_params, func.type_param_bounds
                ))
                ret_type = func.return_type.to_cpp_return()
                params = (self.gen_params_with_protocols(func.params, func.type_params)
                          if protocol_params or has_dynamic else self.gen_params(func.params, func.type_params))
                out.write(f"{ret_type} {func.name}({params});\n")
            else:
                self.gen_function_def(out, func)
            return True

        # Non-template non-stub: already forward-declared
        if not func.is_stub:
            return False
        ret_type = func.return_type.to_cpp_return()
        params = self.gen_params_with_protocols(func.params) if has_dynamic else self.gen_params(func.params, func.type_params)
        out.write(f"{ret_type} {func.name}({params});\n")
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
        # Stubs have no body -- declaration only
        if func.is_stub:
            return
        # @native (C++ import) exports are handled outside the namespace by generator.py
        if func.linkage == FunctionLinkage.NATIVE:
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

        protocol_params = self.protocols.get_protocol_params(func.params)
        has_dynamic = self._has_dynamic_protocol_params(func.params)
        is_generic = bool(func.type_params)

        if is_generic or protocol_params:
            # Generate combined template header for generic functions and/or protocol params
            out.write(self.protocols.gen_combined_template_header(
                func.type_params, protocol_params, func.type_param_bounds
            ))
            ret_type = func.return_type.to_cpp_return()
            params = (self.gen_params_with_protocols(func.params, func.type_params)
                      if protocol_params or has_dynamic else self.gen_params(func.params, func.type_params))
            out.write(f"{ret_type} {func.name}({params}) {{\n")
        else:
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_params_with_protocols(func.params) if has_dynamic else self.gen_params(func.params, func.type_params)
            out.write(f"{ret_type} {func.name}({params}) {{\n")

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.statements.gen_body(out, func.body, func.params, func.return_type,
                                 func, local_ns)

        out.write("}\n")

    def gen_method_def(self, out: TextIO, method: TpyFunction, record_name: str) -> None:
        """Generate a method definition inside a struct."""
        # __next__() -> T is emitted as __next_opt__() -> std::optional<T>
        is_dunder_next = method.name == "__next__"
        cpp_name = "__next_opt__" if is_dunder_next else method.name
        cpp_return_type = method.return_type
        if is_dunder_next:
            cpp_return_type = OptionalType(method.return_type)

        is_const = method.is_readonly
        is_static = method.is_staticmethod

        if is_const and not is_static:
            # Readonly method: const overload always.
            # Dual overload (+ non-const) only when the return could be a
            # reference -- value-type returns are copies so const alone suffices.
            self._gen_method_overload(out, method, record_name, cpp_name, cpp_return_type, const=True)
            needs_dual = not cpp_return_type.is_value_type() or isinstance(cpp_return_type, TypeParamRef)
            if needs_dual:
                self._gen_method_overload(out, method, record_name, cpp_name, cpp_return_type, const=False)
        else:
            self._gen_method_overload(out, method, record_name, cpp_name, cpp_return_type, const=False,
                                      static=is_static)

        # Also emit a __next__() panic stub so direct calls compile but fail at runtime
        if is_dunder_next:
            orig_ret = method.return_type.to_cpp_return()
            out.write(f"\n  {orig_ret} __next__() {{\n")
            out.write(f'    tpy::tpy_panic("__next__() is not directly callable; use a for-loop");\n')
            out.write("  }\n")

    def _gen_method_overload(
        self, out: TextIO, method: TpyFunction, record_name: str,
        cpp_name: str, cpp_return_type: TpyType, *, const: bool, static: bool = False,
    ) -> None:
        """Emit a single method overload (const or non-const)."""
        ret_type = cpp_return_type.to_cpp_return_const() if const else cpp_return_type.to_cpp_return()
        if const:
            params = self.gen_params(method.params, method.type_params, const_params=True)
        else:
            params = self.gen_params(method.params, method.type_params)
        const_suffix = " const" if const else ""
        static_prefix = "static " if static else ""
        out.write("\n")
        self.ctx.emit_preceding_comments(out, method.loc, indent="  ")
        self.ctx.emit_source_comment(out, method.loc, indent="  ")
        out.write(f"  {static_prefix}{ret_type} {cpp_name}({params}){const_suffix} {{\n")

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        if not static:
            local_ns.bind_variable("self", NamedType(record_name))
        for pname, ptype in method.params:
            local_ns.bind_variable(pname, ptype)
        self.statements.gen_body(out, method.body, method.params, method.return_type,
                                 method, local_ns, indent_level=2, is_method=True)

        out.write("  }\n")

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
        # Normalize unresolved int literals to configured default integer type.
        default_int = self.ctx.analyzer.ctx.default_int_type
        if isinstance(var_type, ListType) and isinstance(var_type.element_type, IntLiteralType):
            var_type = ListType(default_int)
        elif isinstance(var_type, ArrayType) and isinstance(var_type.element_type, IntLiteralType):
            var_type = ArrayType(default_int, var_type.size)
        return var_type

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
        cpp_type = var_type.to_cpp()
        if var_type.is_value_type():
            # C++ primitives need explicit zero-init; class types (BigInt, string_view) don't
            init = "{}" if isinstance(var_type, (Int32Type, BoolType, FloatType, CharType, PtrType, ConstPtrType)) else ""
            out.write(f"{cpp_type} {stmt.name}{init};\n")
        else:
            out.write(f"{cpp_type}* {stmt.name}{{}};\n")

    def gen_global_extern(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate an extern declaration for a global variable in header file."""
        var_type = self._resolve_global_type(stmt)
        cpp_type = var_type.to_cpp()
        if var_type.is_value_type():
            out.write(f"extern {cpp_type} {stmt.name};\n")
        else:
            out.write(f"extern {cpp_type}* {stmt.name};\n")

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
        out.write("  static bool initialized = false;\n")
        out.write("  if (initialized) return;\n")
        out.write("  initialized = true;\n\n")
        # Initialize synthetic __name__ if not user-defined
        if self.ctx._has_synthetic_name:
            out.write(f'  __name__ = "{module_name}";\n')

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
        self.ctx.current_ns = self.ctx.analyzer.global_ns
        self.ctx.indent_level = 1

        self.statements._gen_buffered_body(out, stmts, track_stmt_line=True)

        self.ctx.current_ns = None
        if has_user_main:
            out.write("  main();\n")
        out.write("}\n\n")

    def gen_namespace_close(self, out: TextIO) -> None:
        """Close the namespace in source file (for non-entry-point modules)."""
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n")

    def gen_main(self, out: TextIO) -> None:
        """Generate C++ main() that calls module init.

        The namespace is closed before main() so main is in global namespace.
        Accepts argc/argv and initializes tpy::sys_argv for sys.argv support.
        """
        ns = module_to_cpp_namespace(self.ctx.module_name)
        out.write(f"}} // namespace {ns}\n\n")
        out.write("int main(int argc, char* argv[]) {\n")
        out.write("  tpy::init_sys_argv(argc, argv);\n")
        out.write(f"  {ns}::__tpy_init();\n")
        out.write("  return 0;\n")
        out.write("}\n")

    def gen_native_header_decl(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a @native C++ declaration outside the tpy_user namespace (in header).

        Parses native_name on '::' to extract namespace and emits the declaration
        wrapped in the appropriate namespace block.
        """
        cpp_name = func.native_name or func.name
        ret_type = func.return_type.to_cpp_return()
        params = self.gen_params(func.params, func.type_params)
        ns, bare_name = self._split_native_name(cpp_name)
        if ns:
            out.write(f"namespace {ns} {{ {ret_type} {bare_name}({params}); }}\n")
        else:
            out.write(f"{ret_type} {bare_name}({params});\n")

    # Backward compat alias
    gen_extern_cpp_header_decl = gen_native_header_decl

    def gen_extern_cpp_source_def(self, out: TextIO, func: TpyFunction) -> None:
        """Generate an extern_cpp export definition outside the tpy_user namespace (in source).

        Wraps the definition in the appropriate namespace and adds a using-directive
        to access the tpy_user module symbols.
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
            out.write(f"  using namespace {tpy_ns};\n")
        else:
            out.write(f"{ret_type} {bare_name}({params}) {{\n")
            out.write(f"  using namespace {tpy_ns};\n")

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
