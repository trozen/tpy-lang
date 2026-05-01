"""
TurboPython Code Generation Context

Shared state and utilities for C++ code generation.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator, Literal, TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, PtrType, OwnType, ReadonlyType, OptionalType, NominalType, SelfType,
    IntLiteralType, TypeParamRef, UnionType, TupleType, FunctionInfo, ModuleInfo,
    is_protocol_type, unwrap_readonly, ensure_qualified, unwrap_ref_type, is_union_or_optional_type,

)
from ..parse import (
    SourceLocation, TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyGeneratorExpression,
    TpyCoerce, TpyBinOp, TpyUnaryOp, TpyMethodCall, TpySubscript, TpySlice, TpyCall, TpyName, TpyFieldAccess,
    TpyIfExpr,
)
from ..namespace import Namespace, BindingKind
from ..type_def_registry import (
    is_bool_type, is_dict, is_set, is_bytes_view_type, is_str_view_type,
)

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
    return (value.replace('\\', '\\\\')
                 .replace('"', '\\"')
                 .replace('\n', '\\n')
                 .replace('\r', '\\r')
                 .replace('\t', '\\t')
                 .replace('\x00', '\\000'))


def cpp_bytes_literal_span(value: bytes) -> str:
    """Render a bytes literal as a `::tpy::bytes_literal(...)` call --
    a `std::span<const uint8_t>` over a C++ string literal (static
    storage), avoiding the heap allocation of a temporary vector."""
    escaped = "".join(f"\\x{b:02x}" for b in value)
    return f'::tpy::bytes_literal("{escaped}", {len(value)})'


def cpp_string_literal_expr(value: str) -> str:
    """Render a Python string as a C++ string_view expression.

    NUL-free literals emit as bare `"..."` (decay; string_view ctor uses
    strlen). Embedded NUL needs `std::string_view{"...", N}` with explicit
    UTF-8 byte length, otherwise strlen truncates at the first NUL.
    """
    escaped = escape_cpp_string(value)
    if '\x00' in value:
        nbytes = len(value.encode('utf-8'))
        return f'std::string_view{{"{escaped}", {nbytes}}}'
    return f'"{escaped}"'


def view_key_target(container_type) -> "TpyType | None":
    """For dict/set with a view-typed key (BytesView/StrView), return that
    key type so callers can thread it as a target_type into key-position
    codegen. Lets bytes/str literals pin to static storage instead of
    being stored as dangling spans/string_views."""
    if not (is_dict(container_type) or is_set(container_type)):
        return None
    type_args = getattr(container_type, "type_args", None)
    if not type_args:
        return None
    k = type_args[0]
    if is_bytes_view_type(k) or is_str_view_type(k):
        return k
    return None


def escape_cpp_char(value: str) -> str:
    """Escape a Python char for use in a C++ char literal (single-quoted)."""
    return (value.replace('\\', '\\\\').replace("'", "\\'")
                 .replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                 .replace('\x00', '\\000'))


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


def module_to_include_path(module_name: str) -> str:
    """Resolve include path for a module: override if set, else default from name."""
    override = get_include_path(module_name)
    if override is not None:
        return override
    parts = module_name.split('.')
    if len(parts) == 1:
        return f"{parts[0]}.hpp"
    return '/'.join(parts[:-1]) + f"/{parts[-1]}.hpp"


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
    Example: ("shapes", "Container.Inner") -> "::tpyapp::shapes::Container::Inner"
    """
    cpp_name = "::".join(escape_cpp_name(part) for part in name.split("."))
    return f"::{module_to_cpp_namespace(module_name)}::{cpp_name}"


def qualify_native_name(name: str) -> str:
    """Force @native call-site emission to absolute global scope.

    Any non-empty name gets a leading `::` so C++ unqualified lookup can't
    bind it to a member function, enclosing-namespace symbol, or ADL hit
    before finding the intended native symbol. Idempotent: names that
    already start with `::` are left alone.

    Example: "socket" -> "::socket", "tpy::__len__" -> "::tpy::__len__",
    "::already_global" -> "::already_global".
    """
    if not name or name.startswith("::"):
        return name
    return f"::{name}"


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
    # Own[T] from consuming iterators uses the same binding as T.
    if isinstance(elem_type, OwnType):
        elem_type = elem_type.wrapped
    if hoisted:
        return f"{cpp_var} = {deref_expr};"
    if consuming:
        # Forwarding ref into OwnIter storage: zero-cost, move-ready.
        return f"auto&& {cpp_var} = {deref_expr};"
    # Composite types (variants, tuples) use reference binding -- they may
    # contain heap-allocated members, making copies expensive.
    if isinstance(elem_type, (UnionType, TupleType)):
        if const_loop_var:
            return f"const auto& {cpp_var} = {deref_expr};"
        return f"auto&& {cpp_var} = {deref_expr};"
    if const_loop_var and elem_type.is_value_type():
        if elem_type.is_expensive_copy():
            return f"const {elem_type.to_cpp()}& {cpp_var} = {deref_expr};"
        return f"{elem_type.to_cpp()} {cpp_var} = {deref_expr};"
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
        # Slicing (a[1:]) returns an rvalue span/view, not a reference into obj.
        # Only single-index subscripting (a[0]) returns T& and inherits lvalueness from obj.
        if isinstance(expr.index, TpySlice):
            return False
        return is_lvalue_iterable(expr.obj, get_record, get_type)
    if isinstance(expr, (TpyMethodCall, TpyCall)):
        if isinstance(expr, TpyCall) and expr.call_type is not None:
            return False
        if isinstance(expr, TpyCall) and isinstance(expr.func, TpyName) and get_record(expr.func_name):
            return False
        # Own[T] returns are by-value rvalues even when T is a reference type;
        # get_type strips OwnType, so consult resolved_function_info to see it.
        rfi = expr.resolved_function_info
        if rfi is not None and isinstance(rfi.return_type, OwnType):
            return False
        ret_type = get_type(expr)
        # Protocol return types (e.g. Iterator[T] from generators) are
        # value types in practice -- the C++ return is a concrete struct.
        if is_protocol_type(ret_type):
            return False
        return (not ret_type.is_value_type()
                and not is_union_or_optional_type(ret_type))
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
        self._pending: list[tuple[str, str, str, bool]] = []
        self._pending_named: list[tuple[str, str, str | None, bool]] = []
        self._counter: int = 0

    def create(self, param_type: TpyType, init_expr: str) -> str:
        """Create a temp variable and return its name for use in the call."""
        param_type = unwrap_ref_type(param_type)
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

    def checkpoint(self) -> tuple[int, int]:
        """Snapshot the current pending-temp queue lengths.

        Pair with `rollback_to` to discard temps registered between the two
        calls -- needed when emitting into a context that has no place to
        flush declarations (e.g. a C++ member-initializer-list expression).
        """
        return (len(self._pending), len(self._pending_named))

    def rollback_to(self, checkpoint: tuple[int, int]) -> bool:
        """Discard temps registered after `checkpoint`. Returns True if any were dropped.

        `_counter` is intentionally not rolled back: the caller may emit the same
        expression again later (in a different context that can flush temps), and
        reusing counter values would produce duplicate `__tmp_N` names. Skipping a
        number is harmless -- temp names only need to be unique within a pass.
        """
        pending, pending_named = checkpoint
        if len(self._pending) == pending and len(self._pending_named) == pending_named:
            return False
        del self._pending[pending:]
        del self._pending_named[pending_named:]
        return True

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
    no_main: bool = False               # Skip main() generation, emit __tpy_main() only


@dataclass(frozen=True)
class RecursiveUnionInfo:
    """Codegen-side identity of a recursive union alias."""
    name: str  # alias short name (e.g. "JsonValue")
    full_members: tuple[TpyType, ...]  # canonical wrapper-struct variant ordering
    origin: str | None  # defining module name; None for the current module


@dataclass
class LocalScopeSnap:
    """Snapshot of the C++ local-variable declaration state inside a function body.

    Covers every field that tracks which locals exist and what C++ representation
    they use (pointer-local, const-indirect, movable, rebind slot). Used to restore
    scope between if/else branches so that declarations inside one branch don't
    bleed into sibling branches.

    If a new "what locals exist" field is added to CodeGenContext, add it here too.

    Note: hoisted_vars and branch_hoisted_vars are NOT snapshotted -- they are
    function-scoped accumulators. A stale entry from a prior branch's nested if
    causes unnecessary hoisting but not incorrect code.
    """
    declared_vars: set[str]
    var_types: dict[str, TpyType]
    local_scope_names: set[str]
    pointer_locals: set[str]
    optional_locals: set[str]
    ptr_variant_locals: set[str]
    const_indirect_locals: set[str]
    movable_locals: set[str]
    ref_bound_locals: set[str]
    rebind_slots: dict[str, str]
    plain_rebind_slots: set[str]
    assign_narrowed_types: dict[str, 'TpyType']


@dataclass
class FinallyContext:
    """Tracks an active try/finally block for goto-based finally codegen.

    Non-void return uses the shared __retval variable + goto finally_label.
    Void return, break, and continue use dedicated labels with their own
    finally body copies (needs_*_copy set during try body codegen).
    """
    finally_label: str                    # normal-path label (__finally_N)
    return_label: str = ""                # void-return path (__finally_return_N)
    break_label: str = ""                 # break path (__finally_break_N)
    continue_label: str = ""              # continue path (__finally_continue_N)
    needs_return_copy: bool = False       # set when void return generated in try body
    needs_break_copy: bool = False        # set when break generated in try body
    needs_continue_copy: bool = False     # set when continue generated in try body


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
    in_property_getter: bool = False
    current_return_type: TpyType | None = None
    current_error_return: str | None = None
    error_return_stmt_handled: bool = False
    current_func_params: dict[str, TpyType] = field(default_factory=dict)
    current_type_param_bounds: dict[str, TpyType] = field(default_factory=dict)
    const_ref_params: set[str] = field(default_factory=set)

    # --- Temporary variable management ---
    temps: TempState = field(default_factory=TempState)

    # --- Global declaration tracking (from `global x` statements) ---
    global_declared_vars: set[str] = field(default_factory=set)

    # --- Pointer-local tracking ---
    pointer_locals: set[str] = field(default_factory=set)
    # Non-value hoisted vars that are not reassigned: std::optional<T> instead of T*+slot.
    # Also in pointer_locals for dereference ((*name) works for both T* and optional<T>).
    optional_locals: set[str] = field(default_factory=set)
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
    sema_movable_locals: set[str] = field(default_factory=set)

    # --- Reference-bound locals (T& aliases -- del must not move-sink) ---
    # ref_bound_locals grows during codegen as T& decls are emitted -> in LocalScopeSnap.
    # aliased_vars/alias_names are set once per function from prescan -> NOT in LocalScopeSnap.
    ref_bound_locals: set[str] = field(default_factory=set)
    aliased_vars: set[str] = field(default_factory=set)
    alias_names: set[str] = field(default_factory=set)

    # --- Move-through vars (lvalue alias promoted to owned via std::move) ---
    move_through_vars: set[str] = field(default_factory=set)

    # --- Hoisted variable tracking (scope escape phase 2) ---
    hoisted_vars: set[str] = field(default_factory=set)
    # Branch-hoisted pointer-locals: declared by _emit_branch_decls for if/match/try.
    # Rvalue slots for these vars must go to pending_hoist_decls, not block scope.
    branch_hoisted_vars: set[str] = field(default_factory=set)

    # --- Comprehension-local variable names ---
    # Loop variables inside comprehensions shadow globals during element
    # expression codegen.  Checked early in is_global_name().
    comp_local_names: set[str] = field(default_factory=set)
    pending_hoist_decls: list[str] = field(default_factory=list)

    # True while generating container element expressions (dict/list/set/tuple
    # literals, comprehension elements). Ternary codegen checks this to produce
    # std::optional<T> instead of T*/nullptr -- containers always store
    # std::optional<T>, while locals/returns use T*.
    in_container_element: bool = False

    # --- Union type narrowing (isinstance -> std::get) ---
    narrowed_vars: dict[str, str] = field(default_factory=dict)
    # Protocol isinstance narrowing: var -> narrowed protocol type.
    # For `if isinstance(x, SomeProtocol):` on a union parameter, sema narrows x
    # to the protocol type within the branch. No std::get extraction is emitted
    # (the C++ template param is unchanged; the narrower concept constraint
    # holds via `if constexpr`). This map lets get_resolved_type surface the
    # narrower type to for-loop dispatch, `in` operator, etc.
    protocol_narrowings: dict[str, 'TpyType'] = field(default_factory=dict)
    # Assignment narrowing: var -> narrowed concrete type (for inline std::get at access points)
    assign_narrowed_types: dict[str, 'TpyType'] = field(default_factory=dict)
    # Literal type narrowing: var -> single-value LiteralType for dead branch elimination
    literal_facts: dict[str, 'TpyType'] = field(default_factory=dict)
    # Literal overload specialization: injected facts that survive reset_scope
    # (managed by _gen_literal_specialized_function, same pattern as overload_param_types)
    literal_overload_facts: dict[str, 'TpyType'] = field(default_factory=dict)

    # --- @overload specialization ---
    # When generating code for a specific @overload stub, maps parameter names
    # to their concrete (non-union) types. Used for dead branch elimination:
    # isinstance checks on specialized params resolve statically.
    overload_param_types: dict[str, 'TpyType'] = field(default_factory=dict)
    # For short-arity stubs, the missing impl params that must be emitted as
    # locals (with their default expressions) at the start of the body.
    # Each entry: (param_name, param_type, default_expr). Consumed by
    # StatementGenerator.gen_body right after the opening brace and then
    # cleared so inner bodies don't re-emit them.
    overload_missing_param_locals: list[tuple[str, 'TpyType', object]] = field(default_factory=list)

    # --- Iterator loop counter ---
    iter_counter: int = 0

    # --- Tuple unpacking counter ---
    unpack_counter: int = 0

    # --- try/except ---
    try_except_counter: int = 0
    # When set, we're inside a try body -- error_return calls should goto this label
    try_except_label: str | None = None
    # When set (except E as e), error_return calls should move error into this var before goto
    try_except_err_opt: str | None = None
    in_except_tier: Literal["return", "throw"] | None = None

    # --- finally (catch-all + goto pattern) ---
    # Stack of active try/finally blocks for goto-based control flow transformation.
    # When non-empty, return/break/continue must goto the innermost finally label
    # instead of emitting direct C++ return/break/continue.
    finally_stack: list[FinallyContext] = field(default_factory=list)
    # Shared return value variable for the outermost try/finally level.
    # Set once when the first finally context is pushed in a function.
    finally_retval_var: str | None = None
    finally_retval_declared: bool = False

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
    # When generating a generator method's __next__() body, self -> __self
    generator_self_ref: str | None = None

    # --- for/else, while/else label stack ---
    # When generating a loop with an else clause, the goto label name is
    # pushed here so TpyBreak codegen can emit `goto label` instead of `break`.
    loop_else_labels: list[str] = field(default_factory=list)

    # --- Cross-module import tracking ---
    user_module_imports: set[str] = field(default_factory=set)
    all_user_modules: set[str] = field(default_factory=set)
    implicit_stdlib_modules: set[str] = field(default_factory=set)
    macro_dep_modules: set[str] = field(default_factory=set)
    emitted_tpy_inits: set[str] = field(default_factory=set)
    # Maps local_name -> (source_module, original_name) to support import aliases
    user_imported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_protocols: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
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

    # --- Recursive union metadata (populated from module.recursive_union_names) ---
    # Names of recursive aliases declared in the *current* module. Used by
    # generator.py to decide which aliases to emit a wrapper struct for.
    recursive_union_names: set[str] = field(default_factory=set)
    # frozenset(members) -> RecursiveUnionInfo. Indexed by both the alias's
    # full member set and (when the alias has None as a direct member) the
    # narrowed-by-None subset, so types narrowed via `is None` stay
    # identifiable as the alias. Includes recursive aliases imported
    # transitively from other modules so cross-module consumers also
    # recognize the type as recursive. Keying by frozenset (rather than alias
    # name) avoids collisions when two modules declare aliases with the same
    # short name.
    _recursive_union_index: dict[frozenset, 'RecursiveUnionInfo'] = field(default_factory=dict)

    def init_recursive_unions(self, names: set[str],
                              type_aliases: 'dict[str, tuple[TpyType, object]]',
                              imported_modules: 'dict[str, ModuleInfo] | None' = None) -> None:
        """Build codegen metadata for recursive union aliases.

        Indexes both the current module's recursive aliases and the recursive
        aliases of all imported modules, so cross-module references resolve
        correctly through is_recursive_union / variant_index lookups.
        """
        from ..typesys import UnionType, is_void_like_type
        self.recursive_union_names = names
        self._recursive_union_index = {}

        def _register(alias_name: str, members: tuple, origin: 'str | None') -> None:
            info = RecursiveUnionInfo(name=alias_name, full_members=members, origin=origin)
            self._recursive_union_index[frozenset(members)] = info
            non_none = frozenset(m for m in members if not is_void_like_type(m))
            if len(non_none) < len(members):
                self._recursive_union_index[non_none] = info

        for name in names:
            entry = type_aliases.get(name)
            if entry is None:
                continue
            typ = entry[0]
            if isinstance(typ, UnionType):
                _register(name, typ.members, None)

        if imported_modules:
            for module_name, module_info in imported_modules.items():
                for alias_name in module_info.recursive_union_names:
                    typ = module_info.type_aliases.get(alias_name)
                    if isinstance(typ, UnionType):
                        _register(alias_name, typ.members, module_name)

    def _recursive_union_lookup(self, typ: 'TpyType') -> 'RecursiveUnionInfo | None':
        if not self._recursive_union_index:
            return None
        from ..typesys import UnionType
        if not isinstance(typ, UnionType):
            return None
        return self._recursive_union_index.get(frozenset(typ.members))

    def is_recursive_union(self, typ: 'TpyType') -> bool:
        """Check if a type is a recursive union (needs wrapper struct in C++)."""
        return self._recursive_union_lookup(typ) is not None

    def recursive_union_name(self, typ: 'TpyType') -> str | None:
        """Get the wrapper struct name for a recursive union, or None."""
        info = self._recursive_union_lookup(typ)
        return info.name if info is not None else None

    def recursive_union_full_members(self, typ: 'TpyType') -> 'tuple[TpyType, ...] | None':
        """Return the alias's full member tuple if typ is a recursive union
        (exact or narrowed-by-None), or None."""
        info = self._recursive_union_lookup(typ)
        return info.full_members if info is not None else None

    def recursive_union_info(self, typ: 'TpyType') -> 'tuple[bool, tuple[TpyType, ...] | None]':
        """Combined lookup: (is_recursive, full_members_or_None) in one probe.

        Equivalent to (is_recursive_union(typ), recursive_union_full_members(typ))
        but allocates only one frozenset and does one dict lookup.
        """
        info = self._recursive_union_lookup(typ)
        if info is None:
            return (False, None)
        return (True, info.full_members)

    def iter_imported_recursive_unions(self) -> 'Iterator[RecursiveUnionInfo]':
        """Yield each cross-module recursive alias once.

        Skips aliases declared in the current module. The index contains
        each alias under both its full and (when None is a member) narrowed
        frozenset, so id-based de-dup is needed -- both keys point at the
        same RecursiveUnionInfo instance produced by `_register`.
        """
        seen: set[int] = set()
        for info in self._recursive_union_index.values():
            if info.origin is None or info.origin == self.module_name:
                continue
            if id(info) in seen:
                continue
            seen.add(id(info))
            yield info

    def variant_data_expr(self, var_expr: str, typ: 'TpyType | None') -> str:
        """Add .value suffix for recursive union wrapper structs.

        Also handles OptionalType wrapping a recursive union: after
        narrowing, the deref'd value is a wrapper struct.
        """
        if typ is not None:
            if self.is_recursive_union(typ):
                return f"{var_expr}.value"
            if (isinstance(typ, OptionalType)
                    and isinstance(typ.inner, NominalType)
                    and typ.inner.name in self.recursive_union_names):
                return f"{var_expr}.value"
        return var_expr

    def is_ptr_variant_union(self, typ: 'TpyType') -> bool:
        """Check if a union type uses pointer-variant representation.

        Returns True for non-value unions (e.g. Dog | Cat with records)
        that are NOT recursive union aliases. Recursive unions use wrapper
        structs which are value types, so they skip pointer-variant form.
        """
        from ..typesys import UnionType
        return (isinstance(typ, UnionType)
                and typ.uses_pointer_repr()
                and not self.is_recursive_union(typ))

    def reset_scope(self) -> None:
        """Reset all per-scope state for a new function/method/module-init body."""
        self.declared_vars = set()
        self.var_types = {}
        self.local_scope_names = set()
        self.nested_def_locals = set()
        self.global_declared_vars = set()
        self.pointer_locals = set()
        self.optional_locals = set()
        self.ptr_variant_locals = set()
        self.const_indirect_locals = set()
        self.slots.reset()
        self.rebind_slots = {}
        self.plain_rebind_slots = set()
        self.reassigned_vars = set()
        self.rvalue_reassigned_vars = set()
        self.lvalue_reassigned_vars = set()
        self.movable_locals = set()
        self.ref_bound_locals = set()
        self.aliased_vars = set()
        self.alias_names = set()
        self.move_through_vars = set()
        self.hoisted_vars = set()
        self.branch_hoisted_vars = set()
        self.comp_local_names = set()
        self.pending_hoist_decls = []
        self.current_ns = None
        self.indent_level = 0
        self.current_return_type = None
        self.current_error_return = None
        self.error_return_stmt_handled = False
        self.current_func_params = {}
        self.current_type_param_bounds = {}
        self.in_method = False
        self.narrowed_vars = {}
        self.protocol_narrowings = {}
        self.assign_narrowed_types = {}
        self.literal_facts = {}
        self.walrus_pre_declared = set()
        self.overload_terminated = False
        # Note: overload_param_types and literal_overload_facts are NOT reset
        # here -- they're managed by the caller (set before gen_body, cleared
        # in a finally block).
        self.match_counter = 0
        self.iter_counter = 0
        self.unpack_counter = 0
        self.loop_else_labels = []
        self.loop_hoisted_vars = set()
        self.finally_stack = []
        self.finally_retval_var = None
        self.finally_retval_declared = False

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
            optional_locals=self.optional_locals.copy(),
            ptr_variant_locals=self.ptr_variant_locals.copy(),
            const_indirect_locals=self.const_indirect_locals.copy(),
            movable_locals=self.movable_locals.copy(),
            ref_bound_locals=self.ref_bound_locals.copy(),
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
        self.optional_locals = snap.optional_locals.copy()
        self.ptr_variant_locals = snap.ptr_variant_locals.copy()
        self.const_indirect_locals = snap.const_indirect_locals.copy()
        self.movable_locals = snap.movable_locals.copy()
        self.ref_bound_locals = snap.ref_bound_locals.copy()
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

    def save_protocol_narrowings(self) -> dict[str, 'TpyType']:
        """Snapshot protocol_narrowings before entering a branch."""
        return dict(self.protocol_narrowings)

    def restore_protocol_narrowings(self, saved: dict[str, 'TpyType']) -> None:
        """Restore protocol_narrowings after a branch block."""
        self.protocol_narrowings = dict(saved)

    def save_literal_facts(self) -> dict[str, 'TpyType']:
        """Snapshot literal_facts before entering a branch."""
        return dict(self.literal_facts)

    def restore_literal_facts(self, saved: dict[str, 'TpyType']) -> None:
        """Restore literal_facts after a branch block."""
        self.literal_facts = saved

    def any_ancestor_has_del(self, record_name: str) -> bool:
        """Check if any ancestor of the named record has __del__."""
        record_info = self.analyzer.registry.get_record(record_name)
        if record_info is None:
            return False
        return any(
            anc_rec.has_del
            for anc_rec in self.analyzer.registry.iter_ancestor_records(record_info)
        )

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

    def lookup_var_type(self, var_name: str) -> 'TpyType | None':
        """Resolve a variable's declared type in any visible scope.

        Function-locals and parameters live in `var_types`; module-level
        globals are bound on the analyzer's `global_ns`. Returns None when
        the name isn't found in either.
        """
        typ = self.var_types.get(var_name)
        if typ is not None:
            return typ
        global_ns = self.analyzer.ctx.global_ns
        if global_ns is not None:
            binding = global_ns.lookup(var_name)
            if binding is not None and binding.type is not None:
                return binding.type
        return None

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
                ns: Namespace | None = self.current_ns
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

        Unifies pointer-globals (T*), pointer-locals (T*), cross-module
        imported pointer variables, and `self` (this pointer in methods)
        -- all use -> for field/method access and (*x) for value dereference.
        """
        if isinstance(expr, TpyName) and expr.name in self.comp_local_names:
            return False
        # self -> this (pointer) in instance methods, unless inside a generator
        # where generator_self_ref is already a dereferenced reference
        if (isinstance(expr, TpyName) and expr.name == "self"
                and self.in_method and "self" not in self.current_func_params
                and self.generator_self_ref is None):
            return True
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
            raw_obj_type = self.analyzer.get_expr_type(obj)
            obj_type = unwrap_readonly(raw_obj_type) if raw_obj_type is not None else None
            rec = self.analyzer.registry.get_record_for_type(obj_type) if obj_type else None
            if rec is None and isinstance(obj, TpyName):
                rec = self.analyzer.registry.get_record(obj.name)
            if rec is not None and rec.is_native:
                return False
        rt = unwrap_ref_type(fi.return_type)
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
        # Exception: slice calls (e.g. list_stepped_slice) may return by value.
        if isinstance(expr, TpySubscript):
            if expr.slice_function_info is not None:
                return not self._call_returns_cpp_ref(expr.slice_function_info)
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
            if not is_bool_type(result_type):
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
            # Expression callees -> rvalue
            if not isinstance(expr.func, TpyName):
                return True
            # Record constructors -> rvalue
            if self.analyzer.registry.get_record(expr.func_name):
                return True
            # Generic type constructors -> rvalue
            if expr.call_type is not None:
                return True
            if self.analyzer.registry.get_function(expr.func_name) is not None:
                return not self._call_returns_cpp_ref(expr.resolved_function_info)
            # copy() -> rvalue
            if expr.func_name in self.analyzer.imported_names:
                module_name, func_name = self.analyzer.imported_names[expr.func_name]
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
            raw_ct = self.analyzer.get_expr_type(expr.obj)
            container_type = unwrap_readonly(raw_ct) if raw_ct is not None else None
            if isinstance(container_type, NominalType) and container_type.is_user_record:
                return True
        if isinstance(expr, TpyCall):
            return self.is_rvalue_source(expr)
        return False

    def unwrap_copy(self, expr: TpyExpr) -> TpyExpr:
        """If expr is tpy.copy(x), return x; otherwise return expr as-is."""
        if isinstance(expr, TpyCoerce):
            inner = self.unwrap_copy(expr.expr)
            return inner if inner is not expr.expr else expr
        if isinstance(expr, TpyCall) and len(expr.args) == 1 and isinstance(expr.func, TpyName):
            if expr.func_name in self.analyzer.imported_names:
                mod, fn = self.analyzer.imported_names[expr.func_name]
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
