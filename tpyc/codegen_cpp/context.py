"""
TurboPython Code Generation Context

Shared state and utilities for C++ code generation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, PtrType, ConstPtrType, OwnType, ProtocolType, SelfType,
    RecordType, BigIntType, IntLiteralType, TypeParamRef,
)
from ..parse import (
    SourceLocation, TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyArrayLiteral, TpyListRepeat, TpyCoerce, TpyBinOp,
    TpyUnaryOp, TpyMethodCall, TpySubscript, TpyCall, TpyName
)
from ..namespace import Namespace, BindingKind

if TYPE_CHECKING:
    from ..sema import SemanticAnalyzer


class CodeGenError(Exception):
    """Error during C++ code generation."""
    def __init__(self, message: str, loc: SourceLocation | None = None):
        self.message = message
        self.loc = loc
        super().__init__(message)

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        if self.loc:
            return f"{filename}:{self.loc.line}: error: {self.message}"
        return f"{filename}: error: {self.message}"


@dataclass
class CodeGenOptions:
    """Options for C++ code generation."""
    emit_source_comments: bool = False  # Embed Python source as comments in generated C++


@dataclass
class CodeGenContext:
    """Shared state for C++ code generation."""
    analyzer: SemanticAnalyzer
    options: CodeGenOptions
    module_name: str = "generated"
    source_lines: list[str] = field(default_factory=list)
    indent_level: int = 0
    declared_vars: set[str] = field(default_factory=set)
    var_types: dict[str, TpyType] = field(default_factory=dict)
    local_scope_names: set[str] = field(default_factory=set)
    global_names: set[str] = field(default_factory=set)
    current_ns: Namespace | None = None
    in_method: bool = False
    current_return_type: TpyType | None = None
    current_func_params: dict[str, TpyType] = field(default_factory=dict)

    # Pending temporaries for array literals passed to mutable Array params
    _pending_temps: list[tuple[str, str, str]] = field(default_factory=list)
    _temp_counter: int = 0

    # Synthetic __name__ tracking
    _has_synthetic_name: bool = False

    def indent(self) -> str:
        """Get current indentation string."""
        return "  " * self.indent_level

    def emit_source_comment(self, out: TextIO, loc: SourceLocation | None, indent: str = "") -> None:
        """Emit the original Python source line as a comment if enabled."""
        if not self.options.emit_source_comments:
            return
        if loc is None:
            return
        line_idx = loc.line - 1  # Convert 1-indexed to 0-indexed
        if 0 <= line_idx < len(self.source_lines):
            source_line = self.source_lines[line_idx].rstrip()
            out.write(f"{indent}// {loc.line}: {source_line}\n")

    def is_global_name(self, expr: TpyExpr) -> bool:
        """Check if expression is a reference to a global variable.

        Returns False if the name is shadowed by a local variable or parameter.
        """
        if not isinstance(expr, TpyName):
            return False

        # Use namespace if available
        if self.current_ns:
            # Check if it's a global variable
            global_binding = self.analyzer.global_ns.lookup_local(expr.name)
            if global_binding is None or global_binding.kind != BindingKind.VARIABLE:
                return False  # Not a global variable

            # Check if locally shadowed (only if we have a local namespace, not global_ns itself)
            if self.current_ns is not self.analyzer.global_ns:
                # Traverse local namespace chain (up to but not including global_ns)
                ns = self.current_ns
                while ns is not None and ns is not self.analyzer.global_ns:
                    if ns.lookup_local(expr.name):
                        return False  # Locally shadowed
                    ns = ns.parent

            return True

        # Fallback: use old tracking
        # local_scope_names contains function params and locally-declared variables
        return expr.name in self.global_names and expr.name not in self.local_scope_names

    def is_temporary_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression produces a temporary (rvalue).

        Temporaries can't bind to non-const lvalue references, so they need
        to be stored in a temp variable when passed to mutable ref params.

        Lvalues (don't need temps): variable names, field access
        Rvalues (need temps): literals, binary/unary ops, calls, coercions, subscripts on records
        """
        from ..typesys import VOID

        # Scalar literals: 1, 3.14, "x", True
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral)):
            return True
        # Container literals: [], [1,2,3], [0]*10
        if isinstance(expr, (TpyArrayLiteral, TpyListRepeat)):
            return True
        # Explicit coercions: Ptr[T]->T is dereference (lvalue), others produce temporaries
        if isinstance(expr, TpyCoerce):
            # Pointer dereference is an lvalue, not a temporary
            if isinstance(expr.actual_type, (PtrType, ConstPtrType)):
                return False
            return True
        # Binary and unary ops always produce temporaries
        if isinstance(expr, (TpyBinOp, TpyUnaryOp)):
            return True
        # Method calls produce temporaries (unless void, but void can't be passed anyway)
        if isinstance(expr, TpyMethodCall):
            return True
        # Subscript on user records returns by value (rvalue)
        # std::vector/array operator[] returns lvalue ref, but user __getitem__ returns by value
        if isinstance(expr, TpySubscript):
            from .types import TypeResolver
            container_type = self.analyzer.get_expr_type(expr.obj)
            if isinstance(container_type, RecordType):
                return True
        # Function calls
        if isinstance(expr, TpyCall):
            # Generic type constructors (list(), Container[T,N](), etc.)
            if expr.call_type is not None:
                return True
            # Record constructor calls (e.g., Point(1, 2))
            if self.analyzer.registry.get_record(expr.func):
                return True
            # User-defined functions returning non-void
            if func_info := self.analyzer.registry.get_function(expr.func):
                return func_info.return_type != VOID
            # Builtin functions (len, chr, ord, etc.) - always return values
            if self.analyzer.registry.get_builtin_function_overloads(expr.func):
                return True
            # Imported module functions (math.sqrt, etc.)
            if expr.func in self.analyzer.imported_names:
                return True
        return False

    def contains_protocol_type(self, typ: TpyType) -> bool:
        """Check if a type contains ProtocolType or SelfType anywhere in its structure.

        Used to determine if a variable should use 'auto' in C++ codegen because
        the actual type depends on template parameters.
        """
        if isinstance(typ, (ProtocolType, SelfType)):
            return True
        elif isinstance(typ, OwnType):
            return self.contains_protocol_type(typ.wrapped)
        elif isinstance(typ, (PtrType, ConstPtrType)):
            return self.contains_protocol_type(typ.pointee)
        elif (elem_type := typ.get_element_type()) is not None:
            return self.contains_protocol_type(elem_type)
        return False

    def create_temp_for_literal(self, param_type: TpyType, init_expr: str) -> str:
        """Create a temp variable for a literal passed to a mutable reference param.

        Returns the temp variable name to use in the call.
        """
        self._temp_counter += 1
        temp_name = f"__tmp_{self._temp_counter}"
        # Protocol and TypeParamRef use 'auto' since actual type is determined by expression
        type_cpp = "auto" if isinstance(param_type, (ProtocolType, TypeParamRef)) else param_type.to_cpp()
        self._pending_temps.append((temp_name, type_cpp, init_expr))
        return temp_name

    def flush_pending_temps(self, out: TextIO, indent: str) -> None:
        """Emit any pending temp variable declarations."""
        for temp_name, type_cpp, init_expr in self._pending_temps:
            out.write(f"{indent}{type_cpp} {temp_name} = {init_expr};\n")
        self._pending_temps.clear()

    def reset_for_scope(self, *, params: list[tuple[str, TpyType]] | None = None,
                        global_types: dict[str, TpyType | None] | None = None) -> None:
        """Reset scope tracking for a new function/method/module init."""
        if global_types:
            self.declared_vars = set(global_types.keys())
            self.var_types = {name: typ for name, typ in global_types.items() if typ is not None}
        else:
            self.declared_vars = set()
            self.var_types = {}

        if params:
            self.declared_vars.update(pname for pname, _ in params)
            self.var_types.update({pname: ptype for pname, ptype in params})
            self.local_scope_names = {pname for pname, _ in params}
        else:
            self.local_scope_names = set()
