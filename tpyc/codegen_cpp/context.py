"""
TurboPython Code Generation Context

Shared state and utilities for C++ code generation.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, PtrType, ConstPtrType, OwnType, OptionalType, NamedType, SelfType,
    BigIntType, IntLiteralType, TypeParamRef, is_protocol_type,
)
from ..parse import (
    SourceLocation, TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyCoerce, TpyBinOp,
    TpyUnaryOp, TpyMethodCall, TpySubscript, TpyCall, TpyName, TpyFieldAccess
)
from ..namespace import Namespace, BindingKind

if TYPE_CHECKING:
    from ..sema import SemanticAnalyzer


_TEMPLATE_PLACEHOLDER = re.compile(r"\{(self|\d+)\}")


def expand_cpp_template(template: str, self_val: str, *args: str) -> str:
    """Expand a C++ template, substituting {self} and positional {0}, {1}, etc."""
    result = template.replace("{self}", self_val)
    for i, arg in enumerate(args):
        result = result.replace(f"{{{i}}}", arg)
    remaining = _TEMPLATE_PLACEHOLDER.search(result)
    if remaining:
        raise CodeGenError(
            f"Unreplaced placeholder {remaining.group()} in C++ template: {template}"
        )
    return result


def escape_cpp_string(value: str) -> str:
    """Escape a Python string for use in a C++ string literal (double-quoted)."""
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def escape_cpp_char(value: str) -> str:
    """Escape a Python char for use in a C++ char literal (single-quoted)."""
    return value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


# Mapping from Python dunder methods to C++ binary operators.
# Both __truediv__ and __floordiv__ map to / in C++: for integer types, C++ /
# is truncating division (like Python //); user types should implement the
# appropriate semantics in their __truediv__/__floordiv__ methods.
def module_to_cpp_namespace(module_name: str) -> str:
    """Convert a dotted module name to a fully-qualified C++ namespace.

    Example: "mypackage.submod" -> "tpy_user::mypackage::submod"
    """
    return f"tpy_user::{module_name.replace('.', '::')}"


DUNDER_TO_BINARY_OP: dict[str, str] = {
    "__add__": "+", "__sub__": "-", "__mul__": "*",
    "__truediv__": "/", "__floordiv__": "/", "__mod__": "%",
    "__eq__": "==", "__ne__": "!=",
    "__lt__": "<", "__le__": "<=", "__gt__": ">", "__ge__": ">=",
    "__and__": "&", "__or__": "|", "__xor__": "^",
    "__lshift__": "<<", "__rshift__": ">>",
}


class SlotState:
    """Manages unique slot names for pointer-local backing storage."""

    def __init__(self):
        self._counter: int = 0
        self._prefix: str = "__slot"
        self._global_scope: bool = False

    @property
    def global_scope(self) -> bool:
        return self._global_scope

    def next_slot(self) -> str:
        """Return a fresh slot name (__slot_N or __global_slot_N)."""
        self._counter += 1
        return f"{self._prefix}_{self._counter}"

    def reset(self, *, global_scope: bool = False) -> None:
        self._counter = 0
        self._global_scope = global_scope
        self._prefix = "__global_slot" if global_scope else "__slot"


class TempState:
    """Manages temporary variables for array literals passed to mutable reference params."""

    def __init__(self):
        self._pending: list[tuple[str, str, str]] = []
        self._counter: int = 0

    def create(self, param_type: TpyType, init_expr: str) -> str:
        """Create a temp variable and return its name for use in the call."""
        self._counter += 1
        temp_name = f"__tmp_{self._counter}"
        is_protocol = is_protocol_type(param_type)
        type_cpp = "auto" if is_protocol or isinstance(param_type, TypeParamRef) else param_type.to_cpp()
        self._pending.append((temp_name, type_cpp, init_expr))
        return temp_name

    def flush(self, out: TextIO, indent: str) -> None:
        """Emit any pending temp variable declarations."""
        for temp_name, type_cpp, init_expr in self._pending:
            out.write(f"{indent}{type_cpp} {temp_name} = {init_expr};\n")
        self._pending.clear()


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

    # --- Core ---
    analyzer: SemanticAnalyzer
    options: CodeGenOptions
    module_name: str = "generated"
    source_lines: list[str] = field(default_factory=list)

    # --- Scope tracking ---
    indent_level: int = 0
    declared_vars: set[str] = field(default_factory=set)
    var_types: dict[str, TpyType] = field(default_factory=dict)
    local_scope_names: set[str] = field(default_factory=set)
    global_names: set[str] = field(default_factory=set)
    current_ns: Namespace | None = None
    in_method: bool = False
    current_return_type: TpyType | None = None
    current_func_params: dict[str, TpyType] = field(default_factory=dict)

    # --- Temporary variable management ---
    temps: TempState = field(default_factory=TempState)

    # --- Pointer-local tracking ---
    pointer_locals: set[str] = field(default_factory=set)
    pointer_globals: set[str] = field(default_factory=set)
    slots: SlotState = field(default_factory=SlotState)
    rebind_slots: dict[str, str] = field(default_factory=dict)
    reassigned_vars: set[str] = field(default_factory=set)
    rvalue_reassigned_vars: set[str] = field(default_factory=set)

    # --- Hoisted variable tracking (scope escape phase 2) ---
    hoisted_vars: set[str] = field(default_factory=set)
    pending_hoist_decls: list[str] = field(default_factory=list)

    # --- Module-level flags ---
    _has_synthetic_name: bool = False

    # --- Cross-module import tracking ---
    user_module_imports: set[str] = field(default_factory=set)
    all_user_modules: set[str] = field(default_factory=set)
    # Maps local_name -> (source_module, original_name) to support import aliases
    user_imported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_protocols: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
    top_level_decls: dict[str, int] = field(default_factory=dict)
    current_stmt_line: int = 0

    # --- Re-exports (from __init__.py) ---
    reexported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)

    def reset_scope(self) -> None:
        """Reset all per-scope state for a new function/method/module-init body."""
        self.declared_vars = set()
        self.var_types = {}
        self.local_scope_names = set()
        self.pointer_locals = set()
        self.slots.reset()
        self.rebind_slots = {}
        self.reassigned_vars = set()
        self.rvalue_reassigned_vars = set()
        self.hoisted_vars = set()
        self.pending_hoist_decls = []
        self.current_ns = None
        self.indent_level = 0
        self.current_return_type = None
        self.current_func_params = {}
        self.in_method = False

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

    def emit_preceding_comments(self, out: TextIO, loc: SourceLocation | None, indent: str = "") -> None:
        """Emit comments and decorators preceding a definition as C++ comments.

        Walks backwards from the line before loc, skipping blank lines,
        collecting decorator lines (@...) and comment lines (#...).
        Emits them in source order.
        """
        if not self.options.emit_source_comments:
            return
        if loc is None:
            return
        collected: list[str] = []
        idx = loc.line - 2  # 0-indexed line before definition
        # Skip blank lines between definition and block above
        while idx >= 0 and not self.source_lines[idx].strip():
            idx -= 1
        # Collect decorator lines
        while idx >= 0:
            stripped = self.source_lines[idx].strip()
            if stripped.startswith("@"):
                collected.append(self.source_lines[idx].rstrip())
                idx -= 1
            else:
                break
        # Skip blank lines between decorators and comments
        while idx >= 0 and not self.source_lines[idx].strip():
            idx -= 1
        # Collect comment lines
        while idx >= 0:
            stripped = self.source_lines[idx].strip()
            if stripped.startswith("#"):
                collected.append(self.source_lines[idx].rstrip())
                idx -= 1
            else:
                break
        # Emit in source order (collected is reversed)
        for line in reversed(collected):
            out.write(f"{indent}// {line}\n")

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

    def is_pointer_local(self, expr: TpyExpr) -> bool:
        """Check if expression is a reference to a pointer-local variable."""
        if not isinstance(expr, TpyName):
            return False
        return expr.name in self.pointer_locals

    def _is_pointer_global(self, expr: TpyExpr) -> bool:
        """Check if expression is a reference to a non-value-type global (T*)."""
        if not isinstance(expr, TpyName):
            return False
        return expr.name in self.pointer_globals and self.is_global_name(expr)

    def is_indirect_name(self, expr: TpyExpr) -> bool:
        """Check if expression needs indirect access (-> / deref).

        Unifies pointer-globals (T*) and pointer-locals (T*) — both use
        -> for field/method access and (*x) for value dereference.
        """
        return self._is_pointer_global(expr) or self.is_pointer_local(expr)

    def is_rvalue_source(self, expr: TpyExpr) -> bool:
        """Check if an expression produces an rvalue (needs a stack slot).

        Rvalues: constructor calls, Own[T] returns, literals, binop/unop results,
        field access on rvalue objects (member of temporary).
        Lvalues: variable names, field access on lvalues, subscript, function returning T&.

        For pointer-local init: rvalue → new slot, lvalue → take address.
        """
        # Names are lvalues (either pointer-locals, params, or globals)
        if isinstance(expr, TpyName):
            return False
        # Field access: rvalue iff the object is rvalue (member of temporary)
        if isinstance(expr, TpyFieldAccess):
            return self.is_rvalue_source(expr.obj)
        # Subscript into containers is an lvalue (returns T&).
        # NOTE: user-record __getitem__ currently returns const T&, so
        # &(obj[i]) would give const T* (won't assign to T*). This will
        # be fixed when non-const __getitem__ overloads are added.
        if isinstance(expr, TpySubscript):
            return False
        # Constructor calls, literals, ops are rvalues
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                             TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat,
                             TpyBinOp, TpyUnaryOp, TpyMethodCall)):
            return True
        # Coercions: depends on inner expr
        if isinstance(expr, TpyCoerce):
            return self.is_rvalue_source(expr.expr)
        # Function calls
        if isinstance(expr, TpyCall):
            # Record constructors → rvalue
            if self.analyzer.registry.get_record(expr.func):
                return True
            # Generic type constructors → rvalue
            if expr.call_type is not None:
                return True
            # Functions returning Own[T] or Optional[T] → rvalue (pointer value)
            if func_info := self.analyzer.registry.get_function(expr.func):
                if isinstance(func_info.return_type, (OwnType, OptionalType)):
                    return True
                return False
            # Builtin functions → rvalue
            if self.analyzer.registry.get_builtin_function_overloads(expr.func):
                return True
            # copy() → rvalue
            if expr.func in self.analyzer.imported_names:
                module_name, func_name = self.analyzer.imported_names[expr.func]
                if module_name == "tpy" and func_name == "copy":
                    return True
            return True  # Default: treat unknown calls as rvalue
        return True  # Default: rvalue

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
            if isinstance(container_type, NamedType) and container_type.is_record:
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

    def unwrap_copy(self, expr: TpyExpr) -> TpyExpr:
        """If expr is tpy.copy(x), return x; otherwise return expr as-is."""
        if isinstance(expr, TpyCoerce):
            inner = self.unwrap_copy(expr.expr)
            return inner if inner is not expr.expr else expr
        if isinstance(expr, TpyCall) and len(expr.args) == 1:
            if expr.func in self.analyzer.imported_names:
                mod, fn = self.analyzer.imported_names[expr.func]
                if mod == "tpy" and fn == "copy":
                    return expr.args[0]
        return expr

    def contains_protocol_type(self, typ: TpyType) -> bool:
        """Check if a type contains a protocol or SelfType anywhere in its structure.

        Used to determine if a variable should use 'auto' in C++ codegen because
        the actual type depends on template parameters.
        """
        if isinstance(typ, SelfType):
            return True
        if is_protocol_type(typ):
            return True
        elif isinstance(typ, OwnType):
            return self.contains_protocol_type(typ.wrapped)
        elif isinstance(typ, (PtrType, ConstPtrType)):
            return self.contains_protocol_type(typ.pointee)
        elif (elem_type := typ.get_element_type()) is not None:
            return self.contains_protocol_type(elem_type)
        return False

