"""
TurboPython Function Code Generation

Generates C++ function declarations, definitions, and global variables.
"""

from __future__ import annotations
import io
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, is_protocol_type,
    Int32Type, BoolType, FloatType, CharType, PtrType, ConstPtrType,
)
from ..parse import TpyFunction, TpyVarDecl
from ..namespace import Namespace
from .context import module_to_cpp_namespace

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .protocols import ProtocolGenerator
    from .statements import StatementGenerator


class FunctionGenerator:
    """Generates C++ functions and globals."""

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

    def gen_params(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate function parameter list."""
        return ", ".join(ptype.to_cpp_param(pname) for pname, ptype in params)

    def gen_params_with_protocols(self, params: list[tuple[str, TpyType]]) -> str:
        """Generate function parameter list, using template types for protocol params."""
        result = []
        for pname, ptype in params:
            # Resolve type in case it's a NamedType that's actually a protocol
            resolved = self.protocols.resolve_type_for_codegen(ptype)
            if is_protocol_type(resolved):
                # Protocol param: T_name& name (mutable ref, no const methods required)
                result.append(f"T_{pname}& {pname}")
            else:
                result.append(ptype.to_cpp_param(pname))
        return ", ".join(result)

    def gen_function_decl(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function declaration."""
        protocol_params = self.protocols.get_protocol_params(func.params)
        is_generic = bool(func.type_params)

        if is_generic or protocol_params:
            # Generate combined template header for generic functions and/or protocol params
            out.write(self.protocols.gen_combined_template_header(
                func.type_params, protocol_params, func.type_param_bounds
            ))
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_params_with_protocols(func.params) if protocol_params else self.gen_params(func.params)
            out.write(f"{ret_type} {func.name}({params});\n")
        else:
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_params(func.params)
            out.write(f"{ret_type} {func.name}({params});\n")

    def gen_function_def(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a function definition."""
        self.ctx.emit_source_comment(out, func.loc)

        protocol_params = self.protocols.get_protocol_params(func.params)
        is_generic = bool(func.type_params)

        if is_generic or protocol_params:
            # Generate combined template header for generic functions and/or protocol params
            out.write(self.protocols.gen_combined_template_header(
                func.type_params, protocol_params, func.type_param_bounds
            ))
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_params_with_protocols(func.params) if protocol_params else self.gen_params(func.params)
            out.write(f"{ret_type} {func.name}({params}) {{\n")
        else:
            ret_type = func.return_type.to_cpp_return()
            params = self.gen_params(func.params)
            out.write(f"{ret_type} {func.name}({params}) {{\n")

        # Reset declared vars and add parameters
        self.ctx.declared_vars = {pname for pname, _ in func.params}
        self.ctx.var_types = {pname: ptype for pname, ptype in func.params}
        # Track local scope names (params + local vars) that shadow globals
        self.ctx.local_scope_names = {pname for pname, _ in func.params}
        self.ctx.pointer_locals = set()
        self.ctx.slots.reset()
        self.ctx.reassigned_vars, self.ctx.rvalue_reassigned_vars = self.statements.scan_reassigned_vars(func.body)
        self.ctx.hoisted_vars = self.ctx.analyzer.function_hoisted_vars.get(id(func), set())
        self.ctx.pending_hoist_decls = []

        # Set up local namespace for this function
        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.ctx.current_ns = local_ns

        self.ctx.indent_level = 1
        self.ctx.current_return_type = func.return_type
        self.ctx.current_func_params = {pname: ptype for pname, ptype in func.params}

        # Buffer body to collect hoist declarations
        body_buf = io.StringIO()
        for stmt in func.body:
            self.statements.gen_stmt(body_buf, stmt)

        # Write hoist declarations first, then body
        for decl in self.ctx.pending_hoist_decls:
            out.write(decl)
        out.write(body_buf.getvalue())

        self.ctx.indent_level = 0
        self.ctx.current_ns = None

        out.write("}\n")

    def _resolve_global_type(self, stmt: TpyVarDecl) -> TpyType:
        """Resolve the type of a global variable, unwrapping Own[T] to T."""
        if stmt.type:
            var_type = stmt.type
        elif stmt.init:
            var_type = self.types.get_resolved_type(stmt.init)
        else:
            raise RuntimeError(f"Global '{stmt.name}' has no type and no initializer")
        if isinstance(var_type, OwnType):
            var_type = var_type.wrapped
        return var_type

    def gen_global_decl(self, out: TextIO, stmt: TpyVarDecl) -> None:
        """Generate a global variable definition in source file.

        Value-type globals are plain T, non-value-type globals are T* (nullptr).
        Initialization happens in __tpy_init() to ensure proper execution order.
        """
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
        # Pre-seed with global names and types so re-declarations become assignments
        if global_types:
            self.ctx.declared_vars = set(global_types.keys())
            self.ctx.var_types = {name: typ for name, typ in global_types.items() if typ is not None}
        else:
            self.ctx.declared_vars = set()
            self.ctx.var_types = {}
        # In module init, there are no local shadowing variables
        self.ctx.local_scope_names = set()
        # Non-value-type globals use pointer model (T*) inside __tpy_init
        if global_types:
            self.ctx.pointer_locals = {
                name for name, typ in global_types.items()
                if typ and not typ.is_value_type()
            }
        else:
            self.ctx.pointer_locals = set()
        self.ctx.slots.reset(global_scope=True)
        self.ctx.reassigned_vars, self.ctx.rvalue_reassigned_vars = self.statements.scan_reassigned_vars(stmts)
        self.ctx.hoisted_vars = self.ctx.analyzer.top_level_hoisted_vars.copy()
        self.ctx.pending_hoist_decls = []
        # Use global namespace for module init (globals are directly accessible)
        self.ctx.current_ns = self.ctx.analyzer.global_ns
        self.ctx.indent_level = 1

        # Buffer body to collect hoist declarations
        body_buf = io.StringIO()
        for stmt in stmts:
            # Track current statement line for order-aware import qualification
            self.ctx.current_stmt_line = stmt.loc.line if hasattr(stmt, 'loc') and stmt.loc else 0
            self.statements.gen_stmt(body_buf, stmt)
        self.ctx.current_stmt_line = 0  # Reset after top-level processing

        # Write hoist declarations first, then body
        for decl in self.ctx.pending_hoist_decls:
            out.write(decl)
        out.write(body_buf.getvalue())

        self.ctx.current_ns = None
        # Call user's main() if defined
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
