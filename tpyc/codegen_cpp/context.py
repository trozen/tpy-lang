"""
TurboPython Code Generation Context

Shared state and utilities for C++ code generation.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Callable, TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, PtrType, OwnType, ReadonlyType, OptionalType, NamedType, SelfType,
    BigIntType, BoolType, IntLiteralType, TypeParamRef, UnionType, FunctionInfo,
    is_protocol_type, unwrap_readonly, ensure_qualified,
)
from ..parse import (
    SourceLocation, TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyGeneratorExpression,
    TpyCoerce, TpyBinOp, TpyUnaryOp, TpyMethodCall, TpySubscript, TpyCall, TpyName, TpyFieldAccess,
    TpyIfExpr,
)
from ..namespace import Namespace, BindingKind

if TYPE_CHECKING:
    from ..sema import SemanticAnalyzer


INDENT = "    "

# C++ reserved keywords and common type names that conflict with Python identifiers.
# When a Python name matches one of these, codegen appends '_' to avoid C++ errors.
_CPP_RESERVED_WORDS: frozenset[str] = frozenset({
    # C++ keywords
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor",
    "bool", "break", "case", "catch", "char", "char8_t", "char16_t", "char32_t",
    "class", "compl", "concept", "const", "consteval", "constexpr", "constinit",
    "const_cast", "continue", "co_await", "co_return", "co_yield",
    "decltype", "default", "delete", "do", "double", "dynamic_cast",
    "else", "enum", "explicit", "export", "extern",
    "false", "float", "for", "friend", "goto",
    "if", "inline", "int", "long", "mutable",
    "namespace", "new", "noexcept", "not", "not_eq", "nullptr",
    "operator", "or", "or_eq",
    "private", "protected", "public",
    "register", "reinterpret_cast", "requires", "return",
    "short", "signed", "sizeof", "static", "static_assert", "static_cast",
    "struct", "switch",
    "template", "this", "thread_local", "throw", "true", "try", "typedef",
    "typeid", "typename",
    "union", "unsigned", "using",
    "virtual", "void", "volatile",
    "wchar_t", "while",
    "xor", "xor_eq",
})


def escape_cpp_name(name: str) -> str:
    """Escape a Python identifier that clashes with a C++ reserved word.

    Appends '_' to names that collide with C++ keywords or built-in type names.
    Leaves other names unchanged.  Dunder names (__x__) and internal names
    (starting with __tpy) are never escaped -- they are compiler-generated.
    """
    if name in _CPP_RESERVED_WORDS:
        return name + "_"
    return name


_TEMPLATE_PLACEHOLDER = re.compile(r"\{(self|cpp|\d+)\}")


def expand_cpp_template(template: str, self_val: str, *args: str,
                        self_type: 'TpyType | None' = None) -> str:
    """Expand a C++ template, substituting {self}, {cpp}, and positional {0}, {1}, etc.

    {cpp} is replaced with self_type.to_cpp() when available -- used for
    @builtin_type methods that reference their own C++ type name.
    """
    # Validate before substitution: check that all placeholder indices are in range.
    # Post-substitution scanning would false-positive on C++ braces in values
    # (e.g. std::vector<int>{30} looks like {30} placeholder).
    for m in _TEMPLATE_PLACEHOLDER.finditer(template):
        token = m.group(1)
        if token in ("self", "cpp"):
            continue
        if int(token) >= len(args):
            raise CodeGenError(
                f"Unreplaced placeholder {m.group()} in C++ template: {template}"
            )
    if self_type and "{cpp}" in template:
        template = template.replace("{cpp}", self_type.to_cpp())
    result = template.replace("{self}", self_val)
    for i, arg in enumerate(args):
        result = result.replace(f"{{{i}}}", arg)
    return result


def escape_cpp_string(value: str) -> str:
    """Escape a Python string for use in a C++ string literal (double-quoted)."""
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def escape_cpp_char(value: str) -> str:
    """Escape a Python char for use in a C++ char literal (single-quoted)."""
    return value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


# Namespace map: module_name -> C++ namespace (set by Compiler before codegen)
_namespace_map: dict[str, str] = {}
# Include path map: module_name -> header path (derived from namespace or explicit override)
_include_path_map: dict[str, str] = {}


def set_namespace_map(ns_map: dict[str, str]) -> None:
    """Set the module-to-namespace mapping for codegen."""
    global _namespace_map
    _namespace_map = ns_map


def set_include_path_map(ip_map: dict[str, str]) -> None:
    """Set the module-to-include-path mapping for codegen."""
    global _include_path_map
    _include_path_map = ip_map


def get_include_path(module_name: str) -> str | None:
    """Get the include path override for a module, or None for default."""
    return _include_path_map.get(module_name)


def clear_namespace_map() -> None:
    """Clear the namespace and include path maps (called between compilations)."""
    global _namespace_map, _include_path_map
    _namespace_map = {}
    _include_path_map = {}


# Mapping from Python dunder methods to C++ binary operators.
# Both __truediv__ and __floordiv__ map to / in C++: for integer types, C++ /
# is truncating division (like Python //); user types should implement the
# appropriate semantics in their __truediv__/__floordiv__ methods.
def module_to_cpp_namespace(module_name: str) -> str:
    """Convert a dotted module name to a C++ namespace.

    Checks the namespace map first (for # tpy: namespace overrides),
    falls back to "tpyapp::{module_name}".
    """
    if module_name in _namespace_map:
        return _namespace_map[module_name]
    return f"tpyapp::{module_name.replace('.', '::')}"


def qualified_cpp_name(module_name: str, name: str) -> str:
    """Build an absolute-qualified C++ name for cross-module references.

    Example: ("shapes", "Circle") -> "::tpyapp::shapes::Circle"
    """
    return f"::{module_to_cpp_namespace(module_name)}::{escape_cpp_name(name)}"


def qualify_native_name(name: str) -> str:
    """Ensure a C++ native name is fully qualified (prefixed with ::).

    Names already starting with :: or without :: are returned as-is.
    Example: "tpy::__len__" -> "::tpy::__len__", "abs" -> "abs"
    """
    return ensure_qualified(name)


def loop_var_binding(
    elem_type: TpyType, cpp_var: str, deref_expr: str,
    const_loop_var: bool, hoisted: bool = False,
    consuming: bool = False,
) -> str:
    """Return the C++ loop variable binding line (no trailing newline).

    Shared by for-loop and comprehension codegen to avoid duplicating the
    const_loop_var / value_type / auto&& decision tree.

    consuming=True uses auto&& to bind into OwnIter's move-iterator
    storage. Zero cost (no per-element move), but move-ready: codegen
    can later emit std::move(var) for per-element ownership transfer.
    """
    if hoisted:
        return f"{cpp_var} = {deref_expr};"
    if consuming:
        # Forwarding ref into OwnIter storage: zero-cost, move-ready.
        return f"auto&& {cpp_var} = {deref_expr};"
    if const_loop_var and elem_type.is_value_type():
        return f"const {elem_type.to_cpp()}& {cpp_var} = {deref_expr};"
    if const_loop_var:
        return f"const auto& {cpp_var} = {deref_expr};"
    if elem_type.is_value_type():
        return f"{elem_type.to_cpp()} {cpp_var} = {deref_expr};"
    return f"auto&& {cpp_var} = {deref_expr};"


def is_lvalue_iterable(
    expr: TpyExpr,
    get_record: Callable[[str], object | None],
    get_type: Callable[[TpyExpr], TpyType],
) -> bool:
    """Check if an iterable expression is a C++ lvalue.

    Lvalue expressions get ``auto&`` to preserve consumption semantics.
    Rvalue expressions (constructors, value-returning calls, literals)
    get ``auto`` to own the temporary safely.

    get_record: look up a record by name (e.g. registry.get_record).
    get_type:   resolve the C++ result type of an expression.
    """
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, TpyName):
        return True
    if isinstance(expr, TpyFieldAccess):
        return is_lvalue_iterable(expr.obj, get_record, get_type)
    if isinstance(expr, TpySubscript):
        return is_lvalue_iterable(expr.obj, get_record, get_type)
    if isinstance(expr, (TpyMethodCall, TpyCall)):
        if isinstance(expr, TpyCall) and expr.call_type is not None:
            return False
        if isinstance(expr, TpyCall) and get_record(expr.func):
            return False
        ret_type = get_type(expr)
        # Protocol return types (e.g. Iterator[T] from generators) are
        # value types in practice -- the C++ return is a concrete struct.
        if is_protocol_type(ret_type):
            return False
        return (not ret_type.is_value_type()
                and not isinstance(ret_type, (OptionalType, UnionType)))
    return False


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
        self._pending_named: list[tuple[str, str, str | None, bool]] = []
        self._counter: int = 0

    def create(self, param_type: TpyType, init_expr: str) -> str:
        """Create a temp variable and return its name for use in the call."""
        self._counter += 1
        temp_name = f"__tmp_{self._counter}"
        is_protocol = is_protocol_type(param_type)
        type_cpp = "auto" if is_protocol or isinstance(param_type, TypeParamRef) else param_type.to_cpp()
        self._pending.append((temp_name, type_cpp, init_expr, False))
        return temp_name

    def create_typed(self, cpp_type: str, init_expr: str, *, brace_init: bool = False) -> str:
        """Create a temp variable with an explicit C++ type."""
        self._counter += 1
        temp_name = f"__tmp_{self._counter}"
        self._pending.append((temp_name, cpp_type, init_expr, brace_init))
        return temp_name

    def declare_named(self, name: str, cpp_type: str, *,
                      init: str | None = None, brace_init: bool = False) -> None:
        """Register a named pre-declaration (for walrus operator variables)."""
        self._pending_named.append((name, cpp_type, init, brace_init))

    def flush(self, out: TextIO, indent: str) -> None:
        """Emit any pending temp variable declarations."""
        for name, cpp_type, init_val, brace_init in self._pending_named:
            if init_val is not None:
                out.write(f"{indent}{cpp_type} {name} = {init_val};\n")
            elif brace_init:
                out.write(f"{indent}{cpp_type} {name}{{}};\n")
            else:
                out.write(f"{indent}{cpp_type} {name};\n")
        self._pending_named.clear()
        for temp_name, type_cpp, init_expr, brace_init in self._pending:
            if brace_init:
                out.write(f"{indent}{type_cpp} {temp_name}{{{init_expr}}};\n")
            else:
                out.write(f"{indent}{type_cpp} {temp_name} = {init_expr};\n")
        self._pending.clear()


class CodeGenError(Exception):
    """Error during C++ code generation."""
    def __init__(self, message: str, loc: SourceLocation | None = None,
                 filename: str | None = None):
        self.message = message
        self.loc = loc
        self.filename = filename
        super().__init__(message)

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        name = self.filename or filename
        if self.loc:
            return f"{name}:{self.loc.line}: error: {self.message}"
        return f"{name}: error: {self.message}"


@dataclass
class CodeGenOptions:
    """Options for C++ code generation."""
    emit_source_comments: bool = False  # Embed Python source as comments in generated C++
    comment_line_numbers: bool = True   # Include .py line numbers in source comments


@dataclass
class LocalScopeSnap:
    """Snapshot of the C++ local-variable declaration state inside a function body.

    Covers every field that tracks which locals exist and what C++ representation
    they use (pointer-local, const-indirect, movable, rebind slot). Used to restore
    scope between if/else branches so that declarations inside one branch don't
    bleed into sibling branches.

    If a new "what locals exist" field is added to CodeGenContext, add it here too.
    """
    declared_vars: set[str]
    var_types: dict[str, TpyType]
    local_scope_names: set[str]
    pointer_locals: set[str]
    ptr_variant_locals: set[str]
    const_indirect_locals: set[str]
    movable_locals: set[str]
    rebind_slots: dict[str, str]
    plain_rebind_slots: set[str]
    assign_narrowed_types: dict[str, 'TpyType']


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
    nested_def_locals: set[str] = field(default_factory=set)
    # Walrus pre-declarations already emitted (not snapshot/restored across branches)
    walrus_pre_declared: set[str] = field(default_factory=set)
    global_names: set[str] = field(default_factory=set)
    current_ns: Namespace | None = None
    in_method: bool = False
    in_consuming_method: bool = False
    current_return_type: TpyType | None = None
    current_error_return: str | None = None
    current_func_params: dict[str, TpyType] = field(default_factory=dict)
    current_type_param_bounds: dict[str, TpyType] = field(default_factory=dict)
    const_ref_params: set[str] = field(default_factory=set)

    # --- Temporary variable management ---
    temps: TempState = field(default_factory=TempState)

    # --- Global declaration tracking (from `global x` statements) ---
    global_declared_vars: set[str] = field(default_factory=set)

    # --- Pointer-local tracking ---
    pointer_locals: set[str] = field(default_factory=set)
    const_indirect_locals: set[str] = field(default_factory=set)

    # --- Pointer-variant locals (non-value union variables) ---
    # Variables that are std::variant<T*...> instead of std::variant<T...>.
    ptr_variant_locals: set[str] = field(default_factory=set)
    pointer_globals: set[str] = field(default_factory=set)
    final_globals: set[str] = field(default_factory=set)
    slots: SlotState = field(default_factory=SlotState)
    rebind_slots: dict[str, str] = field(default_factory=dict)
    plain_rebind_slots: set[str] = field(default_factory=set)
    reassigned_vars: set[str] = field(default_factory=set)
    rvalue_reassigned_vars: set[str] = field(default_factory=set)
    lvalue_reassigned_vars: set[str] = field(default_factory=set)

    # --- Auto-move tracking (last-use -> std::move) ---
    movable_locals: set[str] = field(default_factory=set)

    # --- Move-through vars (lvalue alias promoted to owned via std::move) ---
    move_through_vars: set[str] = field(default_factory=set)

    # --- Hoisted variable tracking (scope escape phase 2) ---
    hoisted_vars: set[str] = field(default_factory=set)

    # --- Comprehension-local variable names ---
    # Loop variables inside comprehensions shadow globals during element
    # expression codegen.  Checked early in is_global_name().
    comp_local_names: set[str] = field(default_factory=set)
    pending_hoist_decls: list[str] = field(default_factory=list)

    # --- Union type narrowing (isinstance -> std::get) ---
    narrowed_vars: dict[str, str] = field(default_factory=dict)
    # Assignment narrowing: var -> narrowed concrete type (for inline std::get at access points)
    assign_narrowed_types: dict[str, 'TpyType'] = field(default_factory=dict)

    # --- @overload specialization ---
    # When generating code for a specific @overload stub, maps parameter names
    # to their concrete (non-union) types. Used for dead branch elimination:
    # isinstance checks on specialized params resolve statically.
    overload_param_types: dict[str, 'TpyType'] = field(default_factory=dict)

    # --- Iterator loop counter ---
    iter_counter: int = 0

    # --- Tuple unpacking counter ---
    unpack_counter: int = 0

    # --- try/except ---
    try_except_counter: int = 0
    # When set, we're inside a try body -- error_return calls should goto this label
    try_except_label: str | None = None

    # --- with statement ---
    with_counter: int = 0
    # Variables pre-declared by _emit_branch_decls for for-loop hoisting
    loop_hoisted_vars: set[str] = field(default_factory=set)

    # --- Match/case label counter (for goto-based guard fallthrough) ---
    match_counter: int = 0

    # --- Generator function codegen ---
    in_generator_body: bool = False
    generator_field_names: set[str] = field(default_factory=set)
    # Fields stored as std::optional (non-value locals + synthetic for-loop fields)
    generator_optional_fields: set[str] = field(default_factory=set)
    # For-loops with yields in state machine generators: keyed by id(TpyForEach)
    # Values are GeneratorForInfo (not imported here to avoid circular dep)
    generator_for_loop_info: dict[int, object] = field(default_factory=dict)

    # --- for/else, while/else label stack ---
    # When generating a loop with an else clause, the goto label name is
    # pushed here so TpyBreak codegen can emit `goto label` instead of `break`.
    loop_else_labels: list[str] = field(default_factory=list)

    # --- Cross-module import tracking ---
    user_module_imports: set[str] = field(default_factory=set)
    all_user_modules: set[str] = field(default_factory=set)
    implicit_stdlib_modules: set[str] = field(default_factory=set)
    # Maps local_name -> (source_module, original_name) to support import aliases
    user_imported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_protocols: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_type_aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_enums: dict[str, tuple[str, str]] = field(default_factory=dict)
    top_level_decls: dict[str, int] = field(default_factory=dict)
    current_stmt_line: int = 0

    # --- Native global variable imports ---
    # TODO: replace with dict[str, NativeGlobalInfo] holding c_name, linkage, var_type
    # instead of a flat name->name mapping (the TpyVarDecl nodes already carry this)
    native_global_names: dict[str, str] = field(default_factory=dict)

    # --- Re-exports (from __init__.py) ---
    reexported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
    reexported_enums: dict[str, tuple[str, str]] = field(default_factory=dict)

    def reset_scope(self) -> None:
        """Reset all per-scope state for a new function/method/module-init body."""
        self.declared_vars = set()
        self.var_types = {}
        self.local_scope_names = set()
        self.nested_def_locals = set()
        self.global_declared_vars = set()
        self.pointer_locals = set()
        self.ptr_variant_locals = set()
        self.const_indirect_locals = set()
        self.slots.reset()
        self.rebind_slots = {}
        self.plain_rebind_slots = set()
        self.reassigned_vars = set()
        self.rvalue_reassigned_vars = set()
        self.lvalue_reassigned_vars = set()
        self.movable_locals = set()
        self.move_through_vars = set()
        self.hoisted_vars = set()
        self.comp_local_names = set()
        self.pending_hoist_decls = []
        self.current_ns = None
        self.indent_level = 0
        self.current_return_type = None
        self.current_error_return = None
        self.current_func_params = {}
        self.current_type_param_bounds = {}
        self.in_method = False
        self.narrowed_vars = {}
        self.assign_narrowed_types = {}
        self.walrus_pre_declared = set()
        # Note: overload_param_types is NOT reset here -- it's managed by
        # _gen_overload_specialized_function/method which set it before gen_body
        # and clear it in a finally block.
        self.iter_counter = 0
        self.unpack_counter = 0
        self.loop_else_labels = []
        self.loop_hoisted_vars = set()

    def indent(self) -> str:
        """Get current indentation string."""
        return INDENT * self.indent_level

    def snapshot_local_scope(self) -> LocalScopeSnap:
        """Snapshot the local-variable declaration state (see LocalScopeSnap)."""
        return LocalScopeSnap(
            declared_vars=self.declared_vars.copy(),
            var_types=dict(self.var_types),
            local_scope_names=self.local_scope_names.copy(),
            pointer_locals=self.pointer_locals.copy(),
            ptr_variant_locals=self.ptr_variant_locals.copy(),
            const_indirect_locals=self.const_indirect_locals.copy(),
            movable_locals=self.movable_locals.copy(),
            rebind_slots=dict(self.rebind_slots),
            plain_rebind_slots=self.plain_rebind_slots.copy(),
            assign_narrowed_types=dict(self.assign_narrowed_types),
        )

    def restore_local_scope(self, snap: LocalScopeSnap) -> None:
        """Restore local-variable declaration state from a snapshot."""
        self.declared_vars = snap.declared_vars.copy()
        self.var_types = dict(snap.var_types)
        self.local_scope_names = snap.local_scope_names.copy()
        self.pointer_locals = snap.pointer_locals.copy()
        self.ptr_variant_locals = snap.ptr_variant_locals.copy()
        self.const_indirect_locals = snap.const_indirect_locals.copy()
        self.movable_locals = snap.movable_locals.copy()
        self.rebind_slots = dict(snap.rebind_slots)
        self.plain_rebind_slots = snap.plain_rebind_slots.copy()
        self.assign_narrowed_types = dict(snap.assign_narrowed_types)

    def restore_narrowed_vars(self, saved: dict[str, str | None]) -> None:
        """Restore narrowed_vars after a branch block."""
        for var_name, prev in saved.items():
            if prev is not None:
                self.narrowed_vars[var_name] = prev
            else:
                self.narrowed_vars.pop(var_name, None)

    def any_ancestor_has_del(self, record_name: str) -> bool:
        """Check if any ancestor of the named record has __del__."""
        record_info = self.analyzer.registry.get_record(record_name)
        if record_info is None:
            return False
        parent = record_info.parent
        while parent is not None:
            parent_rec = self.analyzer.registry.get_record_for_type(parent)
            if parent_rec is None:
                break
            if parent_rec.has_del:
                return True
            parent = parent_rec.parent
        return False

    def record_or_ancestor_has_del(self, record_name: str) -> bool:
        """Check if the named record or any ancestor has __del__."""
        record_info = self.analyzer.registry.get_record(record_name)
        if record_info is None:
            return False
        if record_info.has_del:
            return True
        return self.any_ancestor_has_del(record_name)

    def _write_source_comment(self, out: TextIO, line_no: int, source: str, indent: str = "") -> None:
        """Write a source comment line, optionally including the .py line number."""
        source = source.lstrip()
        if self.options.comment_line_numbers:
            out.write(f"{indent}// {line_no}: {source}\n")
        else:
            out.write(f"{indent}// {source}\n")

    def emit_source_comment(self, out: TextIO, loc: SourceLocation | None, indent: str = "") -> None:
        """Emit the original Python source line as a comment if enabled."""
        if not self.options.emit_source_comments:
            return
        if loc is None:
            return
        line_idx = loc.line - 1  # Convert 1-indexed to 0-indexed
        if 0 <= line_idx < len(self.source_lines):
            source_line = self.source_lines[line_idx].rstrip()
            self._write_source_comment(out, loc.line, source_line, indent)

    def emit_else_comment(self, out: TextIO, orelse: list, indent: str = "") -> None:
        """Emit the 'else:' source line as a comment.

        Scans backward from the first orelse statement, skipping blank lines
        and comment lines. Stops on any other non-empty line that is not 'else:'.
        """
        if not self.options.emit_source_comments:
            return
        if not orelse or not hasattr(orelse[0], 'loc') or orelse[0].loc is None:
            return
        first_line = orelse[0].loc.line
        for line_num in range(first_line - 1, 0, -1):
            line_idx = line_num - 1
            if 0 <= line_idx < len(self.source_lines):
                stripped = self.source_lines[line_idx].strip()
                if stripped.startswith('else:'):
                    self._write_source_comment(out, line_num, self.source_lines[line_idx].rstrip(), indent)
                    return
                if stripped and not stripped.startswith('#'):
                    return

    def emit_inline_comments(self, out: TextIO, loc: SourceLocation | None, indent: str = "") -> None:
        """Emit Python comment lines immediately preceding a statement.

        Walks backwards from the line before loc, skipping blank lines,
        then collecting consecutive comment lines (#...) at the same or
        shallower indentation as the current statement (deeper-indented
        comments belong to inner blocks and are handled by
        emit_block_trailing_comments).
        Emits them in source order.
        """
        if not self.options.emit_source_comments:
            return
        if loc is None:
            return
        stmt_line = self.source_lines[loc.line - 1]
        stmt_indent = len(stmt_line) - len(stmt_line.lstrip())
        collected: list[tuple[int, str]] = []
        idx = loc.line - 2  # 0-indexed line before current statement
        # Skip blank lines
        while idx >= 0 and not self.source_lines[idx].strip():
            idx -= 1
        # Collect consecutive comment lines at same or shallower indentation
        while idx >= 0:
            stripped = self.source_lines[idx].strip()
            if stripped.startswith("#"):
                line_indent = len(self.source_lines[idx]) - len(self.source_lines[idx].lstrip())
                if line_indent > stmt_indent:
                    break
                collected.append((idx + 1, self.source_lines[idx].rstrip()))
                idx -= 1
            else:
                break
        # Emit in source order
        for line_no, source_line in reversed(collected):
            self._write_source_comment(out, line_no, source_line, indent)

    def emit_block_trailing_comments(self, out: TextIO, body: list, indent: str = "") -> None:
        """Emit trailing comment lines after the last statement in a block.

        Scans forward from the last statement's line, emitting Python comment
        lines that maintain the same or deeper indentation. Stops at
        non-comment code or dedented lines.
        """
        if not self.options.emit_source_comments:
            return
        if not body:
            return
        last_stmt = body[-1]
        if not hasattr(last_stmt, 'loc') or last_stmt.loc is None:
            return
        last_line = last_stmt.loc.line
        # Determine expected indentation from the last statement's source
        ref_idx = last_line - 1
        if ref_idx < 0 or ref_idx >= len(self.source_lines):
            return
        ref_line = self.source_lines[ref_idx]
        block_indent = len(ref_line) - len(ref_line.lstrip())

        line_no = last_line + 1
        while line_no <= len(self.source_lines):
            line_idx = line_no - 1
            source_line = self.source_lines[line_idx]
            stripped = source_line.strip()
            if not stripped:
                line_no += 1
                continue
            line_indent = len(source_line) - len(source_line.lstrip())
            if line_indent < block_indent:
                break
            if stripped.startswith("#"):
                self._write_source_comment(out, line_no, source_line.rstrip(), indent)
                line_no += 1
            else:
                break

    def emit_preceding_comments(self, out: TextIO, loc: SourceLocation | None, indent: str = "") -> None:
        """Emit comments and decorators preceding a definition as C++ comments.

        Walks backwards from the line before loc, skipping blank lines,
        collecting decorator lines (@...) and comment lines (#...) at the
        same indentation as the definition. Deeper-indented comments belong
        to the body of a preceding definition and are skipped.
        Emits them in source order.
        """
        if not self.options.emit_source_comments:
            return
        if loc is None:
            return
        # Determine the definition's indentation
        def_line = self.source_lines[loc.line - 1]
        def_indent = len(def_line) - len(def_line.lstrip())
        collected: list[tuple[int, str]] = []
        idx = loc.line - 2  # 0-indexed line before definition
        # Skip blank lines between definition and block above
        while idx >= 0 and not self.source_lines[idx].strip():
            idx -= 1
        # Collect decorator lines
        while idx >= 0:
            stripped = self.source_lines[idx].strip()
            if stripped.startswith("@"):
                collected.append((idx + 1, self.source_lines[idx].rstrip()))
                idx -= 1
            else:
                break
        # Skip blank lines between decorators and comments
        while idx >= 0 and not self.source_lines[idx].strip():
            idx -= 1
        # Collect comment lines at same indentation as the definition
        while idx >= 0:
            stripped = self.source_lines[idx].strip()
            if stripped.startswith("#"):
                line_indent = len(self.source_lines[idx]) - len(self.source_lines[idx].lstrip())
                if line_indent > def_indent:
                    break
                collected.append((idx + 1, self.source_lines[idx].rstrip()))
                idx -= 1
            else:
                break
        # Emit in source order (collected is reversed)
        for line_no, source_line in reversed(collected):
            self._write_source_comment(out, line_no, source_line, indent)

    def is_global_name(self, expr: TpyExpr) -> bool:
        """Check if expression is a reference to a global variable.

        Returns False if the name is shadowed by a local variable or parameter.
        """
        if not isinstance(expr, TpyName):
            return False

        if expr.name in self.comp_local_names:
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

        Unifies pointer-globals (T*), pointer-locals (T*), and cross-module
        imported pointer variables -- all use -> for field/method access
        and (*x) for value dereference.
        """
        if isinstance(expr, TpyName) and expr.name in self.comp_local_names:
            return False
        if self._is_pointer_global(expr) or self.is_pointer_local(expr):
            return True
        if isinstance(expr, TpyName) and expr.name in self.user_imported_variables:
            source_module, original_name = self.user_imported_variables[expr.name]
            module_info = self.analyzer.registry.get_module(source_module)
            if module_info and original_name in module_info.variables:
                return module_info.variables[original_name].is_pointer
        return False

    def get_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the sema-analyzed type of an expression, unwrapping ReadonlyType.

        Codegen doesn't need ReadonlyType (C++ handles const via signatures),
        so this always strips it.
        """
        typ = self.analyzer.get_expr_type(expr)
        return unwrap_readonly(typ) if typ is not None else None

    def pointer_value_expr(self, expr: TpyExpr, rendered: str) -> str:
        """Return expression yielding raw pointer value for pointer names.

        Most pointer-globals are wrapped and need one dereference (`*name`) to get
        `T*`, but globals whose declared type is already pointer-like (Ptr, Ptr[readonly[T]],
        or Optional non-value) are already `T*` and must be returned as-is.
        """
        if not self._is_pointer_global(expr):
            return rendered
        expr_type = self.get_expr_type(expr)
        if isinstance(expr_type, PtrType):
            return rendered
        if isinstance(expr_type, OptionalType) and expr_type.uses_pointer_repr():
            return rendered
        return f"(*{rendered})"

    def _call_returns_cpp_ref(self, fi: FunctionInfo | None, obj: TpyExpr | None = None) -> bool:
        """True if this function/method call returns a C++ lvalue reference (T&).

        User-defined functions/methods with non-value, non-generic, non-owning
        return types emit T& in C++ (via to_cpp_return()). Everything else --
        native imports, @native record methods, TypeParamRef (val_or_ref_t<T>),
        Own[T], Optional[T] -- uses value semantics.
        """
        if fi is None or fi.is_native_import:
            return False
        if obj is not None:
            # Methods on @native records have unknown C++ return convention.
            # Instance calls: obj type is the record. Static calls: obj is the
            # class name with no instance type set, so fall back to name lookup.
            obj_type = unwrap_readonly(self.analyzer.get_expr_type(obj))
            obj_rec_name = (obj_type.name if isinstance(obj_type, NamedType)
                            else (obj.name if isinstance(obj, TpyName) else None))
            if obj_rec_name is not None:
                rec = self.analyzer.registry.get_record(obj_rec_name)
                if rec is not None and rec.is_native:
                    return False
        rt = fi.return_type
        return (rt is not None
                and not rt.is_value_type()
                and not isinstance(rt, (TypeParamRef, OwnType, OptionalType, UnionType))
                and not is_protocol_type(rt))

    def is_rvalue_source(self, expr: TpyExpr) -> bool:
        """Check if an expression produces an rvalue (needs a stack slot).

        Rvalues: constructor calls, Own[T] returns, literals, binop/unop results,
        field access on rvalue objects (member of temporary).
        Lvalues: variable names, field access on lvalues, subscript, function returning T&.

        For pointer-local init: rvalue -> new slot, lvalue -> take address.
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
        # Ternary: lvalue iff both arms are lvalues (C++ ternary with two lvalue
        # arms is itself an lvalue). Uses OR semantics: rvalue if either arm is
        # rvalue, since _gen_if_expr emits arms inline with no temp materialization
        # (unlike _gen_logical_value which materializes rvalue arms into auto&& temps).
        if isinstance(expr, TpyIfExpr):
            result_type = self.analyzer.get_expr_type(expr)
            if result_type and not result_type.is_value_type():
                return (self.is_rvalue_source(expr.then_expr)
                        or self.is_rvalue_source(expr.else_expr))
        # Logical and/or with operand-return semantics: rvalue temps are
        # materialized into named variables by _gen_logical_value, so the
        # result is only an rvalue when both operands are rvalues.
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            result_type = self.analyzer.get_expr_type(expr)
            if not isinstance(result_type, BoolType):
                return self.is_rvalue_source(expr.left) and self.is_rvalue_source(expr.right)
        # Constructor calls, literals, ops are rvalues
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                             TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat,
                             TpyListComprehension, TpyBinOp, TpyUnaryOp)):
            return True
        if isinstance(expr, TpyMethodCall):
            return not self._call_returns_cpp_ref(expr.resolved_function_info, expr.obj)
        # Coercions: depends on inner expr
        if isinstance(expr, TpyCoerce):
            return self.is_rvalue_source(expr.expr)
        # Function calls
        if isinstance(expr, TpyCall):
            # Record constructors -> rvalue
            if self.analyzer.registry.get_record(expr.func):
                return True
            # Generic type constructors -> rvalue
            if expr.call_type is not None:
                return True
            if self.analyzer.registry.get_function(expr.func) is not None:
                return not self._call_returns_cpp_ref(expr.resolved_function_info)
            # copy() -> rvalue
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
        # Scalar literals: 1, 3.14, "x", True, None
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral, TpyNoneLiteral)):
            return True
        # Container literals: [], [1,2,3], [0]*10, [x for x in items]
        if isinstance(expr, (TpyArrayLiteral, TpyListRepeat, TpyListComprehension)):
            return True
        # Generator expressions produce rvalue temporaries
        if isinstance(expr, TpyGeneratorExpression):
            return True
        # Explicit coercions: Ptr[T]->T is dereference (lvalue), others produce temporaries
        if isinstance(expr, TpyCoerce):
            # Pointer dereference is an lvalue, not a temporary
            if isinstance(expr.actual_type, PtrType):
                return False
            return True
        # Binary and unary ops always produce temporaries
        if isinstance(expr, (TpyBinOp, TpyUnaryOp)):
            return True
        if isinstance(expr, TpyMethodCall):
            return self.is_rvalue_source(expr)
        # Subscript on user records returns by value (rvalue)
        # std::vector/array operator[] returns lvalue ref, but user __getitem__ returns by value
        if isinstance(expr, TpySubscript):
            container_type = unwrap_readonly(self.analyzer.get_expr_type(expr.obj)) if self.analyzer.get_expr_type(expr.obj) is not None else None
            if isinstance(container_type, NamedType) and container_type.is_user_record:
                return True
        if isinstance(expr, TpyCall):
            return self.is_rvalue_source(expr)
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
        elif isinstance(typ, ReadonlyType):
            return self.contains_protocol_type(typ.wrapped)
        elif isinstance(typ, PtrType):
            return self.contains_protocol_type(typ.pointee)
        elif (elem_type := typ.get_element_type()) is not None:
            return self.contains_protocol_type(elem_type)
        return False
