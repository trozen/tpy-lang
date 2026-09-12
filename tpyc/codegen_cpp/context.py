"""
TurboPython Code Generation Context

Shared state and utilities for C++ code generation.
"""

from __future__ import annotations
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterator, Literal, TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, PtrType, OwnType, ReadonlyType, OptionalType, NominalType, SelfType,
    IntLiteralType, TypeParamRef, UnionType, TupleType, FunctionInfo, ModuleInfo, INT32,
    AliasRef, RecursiveUnionInfo,
    is_protocol_type, unwrap_readonly, unwrap_qualifiers, ensure_qualified, unwrap_ref_type,
    is_union_or_optional_type, is_own_pointer_repr_optional,
    polymorphic_source_is_pointer,
)
from ..parse import (
    SourceLocation, TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictLiteral, TpySetLiteral, TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression,
    TpyCoerce, TpyBinOp, TpyUnaryOp, TpyMethodCall, TpySubscript, TpySlice, TpyCall, TpyName, TpyFieldAccess,
    TpyIfExpr, TpyAssign, TpyVarDecl, TpyTupleUnpack, TpyStmt, VarLinkage,
    TpyNamedExpr, TpyTupleLiteral, TupleElemCapture, walrus_bindings,
)
from ..namespace import Namespace, BindingKind
from ..type_def_registry import (
    is_bool_type, is_dict, is_set, is_bytes_view_type, is_str_view_type,
    is_borrowing_view_type, is_big_int_type, is_fixed_int_type,
)
from ..symbol_binding import lookup_imported, lookup_qualified, resolve_definer, SymbolKind
from ..identity_map import IdentityMap
from ..compilation_context import get_current_compiler
from ..value_category import (
    is_rvalue_source as _is_rvalue_source_shared,
    call_returns_cpp_ref as _call_returns_cpp_ref_shared,
    _CONTAINER_LITERAL_NODES,
)
from .forms import (
    is_plain_nonvalue as _forms_is_plain_nonvalue,
    is_ptr_variant_union as _forms_is_ptr_variant_union,
    reads_storage_form_optional as _forms_reads_storage_form_optional,
)

if TYPE_CHECKING:
    from ..sema import SemanticAnalyzer
    from ..thir.nodes import THIRConstructor, THIRFunction, THIRResumableBody
    from ..thir.emit import ResumableLeafEmitter


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


def resumable_struct_name(name: str, owner_record: str | None = None,
                          prefix: str = "__coro_") -> str:
    """Name a module-scope frame without conflating nested owner components."""
    if owner_record and "." in owner_record:
        owners = owner_record.split(".")
        # A digit cannot start a Python identifier, keeping this namespace
        # disjoint from free functions and flat owners, including underscores.
        components = "_".join(f"{len(part)}_{part}" for part in [*owners, name])
        return f"{prefix}{len(owners)}_{components}"
    if owner_record:
        return f"{prefix}{escape_cpp_name(owner_record)}_{escape_cpp_name(name)}"
    return f"{prefix}{escape_cpp_name(name)}"


def expand_cpp_template(template: str, self_val: 'str | None' = None, *args: str,
                        self_type: 'TpyType | None' = None) -> str:
    """Substitute {self}, {cpp}, and positional {0}, {1}, ... into a
    @cpp_template body.

    Brace grammar follows Python str.format: `{{`/`}}` emit a literal
    `{`/`}` (so a template can spell C++ brace-init / lambda / scope
    braces), and a lone unescaped brace / unknown field / out-of-range
    index raises CodeGenError. Substituted values are inserted verbatim
    and never re-scanned, so C++ braces inside an argument (e.g.
    `std::vector<int>{30}`) can't be mistaken for placeholders. self_val
    is None for free functions (no receiver); a {self} is then an error.
    """
    if self_type is not None and "{cpp}" in template:
        template = template.replace("{cpp}", self_type.to_cpp())
    out: list[str] = []
    i = 0
    n = len(template)
    while i < n:
        c = template[i]
        if c == '{':
            if i + 1 < n and template[i + 1] == '{':
                out.append('{')
                i += 2
                continue
            close = template.find('}', i + 1)
            if close == -1:
                raise CodeGenError(
                    f"Unmatched '{{' in C++ template (use '{{{{' for a literal "
                    f"brace): {template}"
                )
            field = template[i + 1:close]
            if field == 'self':
                if self_val is None:
                    raise CodeGenError(
                        f"'{{self}}' placeholder is only valid in method templates: "
                        f"{template}"
                    )
                out.append(self_val)
            elif field == 'cpp':
                # Resolved up front from self_type; if it survives to here the
                # caller had no type context -- preserve it literally rather
                # than erroring, leaving the malformed C++ for the C++ compiler.
                out.append('{cpp}')
            elif field.isdigit():
                idx = int(field)
                if idx >= len(args):
                    raise CodeGenError(
                        f"Unreplaced placeholder {{{field}}} in C++ template: {template}"
                    )
                out.append(args[idx])
            else:
                raise CodeGenError(
                    f"Invalid placeholder '{{{field}}}' in C++ template (use "
                    f"'{{{{' and '}}}}' for literal braces): {template}"
                )
            i = close + 1
        elif c == '}':
            if i + 1 < n and template[i + 1] == '}':
                out.append('}')
                i += 2
                continue
            raise CodeGenError(
                f"Unmatched '}}' in C++ template (use '}}}}' for a literal "
                f"brace): {template}"
            )
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _escape_cpp_byte_seq(data: bytes) -> str:
    """Escape a raw byte sequence into a C++ double-quoted string literal body.

    Printable ASCII stays verbatim; the rest emits as \\xNN (byte-exact under
    any exec/input charset, since TPy lengths assume raw UTF-8 bytes). C++ hex
    escapes are maximal-munch, so a printable hex digit right after an escaped
    byte needs a `" "` break (adjacent literals concatenate).
    """
    simple = {0x5C: '\\\\', 0x22: '\\"', 0x0A: '\\n', 0x0D: '\\r',
              0x09: '\\t', 0x00: '\\000'}
    out: list[str] = []
    pending_hex = False
    for b in data:
        if b in simple:
            out.append(simple[b])
            pending_hex = False
        elif 0x20 <= b <= 0x7E:
            if pending_hex and chr(b) in '0123456789abcdefABCDEF':
                out.append('" "')
            out.append(chr(b))
            pending_hex = False
        else:
            out.append(f'\\x{b:02x}')
            pending_hex = True
    return ''.join(out)


def escape_cpp_string(value: str) -> str:
    """Escape a Python string for use in a C++ string literal (double-quoted).

    UTF-8 encode then escape per-byte (see `_escape_cpp_byte_seq`): the
    str and bytes literal paths share one escaping scheme.
    """
    return _escape_cpp_byte_seq(value.encode('utf-8'))


def cpp_bytes_literal_span(value: bytes) -> str:
    """Render a bytes literal as a `::tpy::bytes_literal(...)` call --
    a `::tpy::BytesView` over a C++ string literal (static
    storage), avoiding the heap allocation of a temporary vector."""
    return f'::tpy::bytes_literal("{_escape_cpp_byte_seq(value)}", {len(value)})'


def cpp_bytes_literal_owned(value: bytes) -> str:
    """Render a bytes literal landing in an owned (`::tpy::Bytes`) slot: copy
    the static-storage span into the owning buffer via the runtime helper (one
    byte-copy), keeping the generated source readable. The empty case spells
    the buffer type too -- `Bytes` does not convert from a bare
    `std::vector<uint8_t>` (buffer_types.hpp)."""
    if not value:
        return "::tpy::Bytes{}"
    return f'::tpy::bytes_literal_owned("{_escape_cpp_byte_seq(value)}", {len(value)})'


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


def bigint_index_narrow_type(container_type, analyzer) -> "TpyType | None":
    """Narrow target for a runtime-BigInt subscript index, derived from the
    receiver's declared key/index type. None means the declared key type is
    itself BigInt, so the index passes through unnarrowed (the container's
    C++ key type IS tpy::BigInt); a fixed-int key narrows to its declared
    width (dict[int64] -> int64, a user __getitem__(key: int32) -> int32);
    everything else keeps the int32-indexed sequence domain
    (list/str/bytes/tuple/Span/Array)."""
    t = unwrap_qualifiers(container_type)
    if isinstance(t, OptionalType):
        t = unwrap_qualifiers(t.inner)
    key = None
    if is_dict(t):
        type_args = getattr(t, "type_args", None)
        if type_args:
            key = type_args[0]
    elif isinstance(t, NominalType) and t.is_record:
        kr = analyzer.narrowing.record_getitem_key_ret(t)
        if kr is not None:
            key = kr[0]
    if key is None:
        return INT32
    key = unwrap_readonly(key)
    if is_big_int_type(key):
        return None
    if is_fixed_int_type(key):
        return key
    return INT32


def escape_cpp_char(value: str) -> str:
    """Escape a Python char for use in a C++ char literal (single-quoted)."""
    return (value.replace('\\', '\\\\').replace("'", "\\'")
                 .replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                 .replace('\x00', '\\000'))


def get_include_path(module_name: str) -> str | None:
    """Get the include path override for a module, or None for default.

    Returns None when no compilation is active (matches the previous
    empty-default-dict behavior for callers like BuildLayout's pure
    path-shape tests).
    """
    compiler = get_current_compiler()
    if compiler is None:
        return None
    return compiler.include_path_map.get(module_name)


def module_to_include_path(module_name: str) -> str:
    """Resolve include path for a module: override if set, else default from name."""
    override = get_include_path(module_name)
    if override is not None:
        return override
    parts = module_name.split('.')
    if len(parts) == 1:
        return f"{parts[0]}.hpp"
    return '/'.join(parts[:-1]) + f"/{parts[-1]}.hpp"


# Mapping from Python dunder methods to C++ binary operators.
# Both __truediv__ and __floordiv__ map to / in C++: for integer types, C++ /
# is truncating division (like Python //); user types should implement the
# appropriate semantics in their __truediv__/__floordiv__ methods.
def module_to_cpp_namespace(module_name: str) -> str:
    """Convert a dotted module name to a C++ namespace.

    Checks the namespace map first (for # tpy: namespace overrides),
    falls back to "tpyapp::{module_name}".
    """
    compiler = get_current_compiler()
    if compiler is not None and module_name in compiler.namespace_map:
        return compiler.namespace_map[module_name]
    return f"tpyapp::{module_name.replace('.', '::')}"


def module_has_cpp_namespace_override(module_name: str) -> bool:
    """True if `module_name` has an explicit `# tpy: cpp_namespace` directive
    (i.e. its C++ namespace differs from the default `tpyapp::<module>`)."""
    compiler = get_current_compiler()
    return compiler is not None and module_name in compiler.namespace_map


def qualified_cpp_name(module_name: str, name: str) -> str:
    """Build an absolute-qualified C++ name for cross-module references.

    Example: ("shapes", "Circle") -> "::tpyapp::shapes::Circle"
    Example: ("shapes", "Container.Inner") -> "::tpyapp::shapes::Container::Inner"
    """
    cpp_name = "::".join(escape_cpp_name(part) for part in name.split("."))
    return f"::{module_to_cpp_namespace(module_name)}::{cpp_name}"


def imported_free_callee_cpp(module_attributes, func_name: str,
                             mangled: str | None = None) -> str | None:
    """The cross-module free-callee spelling, or None when `func_name` is
    not bound as an imported FUNCTION in the calling module's attribute
    table (local definitions spell bare). The ONE qualification decision
    behind every cross-module free call. `mangled` overrides the canonical
    name for literal-specialized overload stubs (`_free_callee_kind`
    threads that spelling here)."""
    qual = lookup_imported(module_attributes, func_name, SymbolKind.FUNCTION)
    if qual is None:
        return None
    source_module, qual_name = qual
    return qualified_cpp_name(source_module,
                              mangled if mangled is not None else qual_name)


def module_qualified_callee_cpp(registry, module_attributes, module_name: str,
                                user_module: str, method: str, fi) -> str:
    """The plain cross-module dotted-call spelling (`import m; m.f(...)` ->
    `::tpyapp::m::f`). The ONE qualification decision behind every
    cross-module dotted call. A qualified `mod.X`
    is authoritative for X's module: resolve X by its qname under `mod`
    (records) or its sema-resolved originating module (functions) BEFORE the
    bare-name attribute lookup -- that lookup collides when a same-named
    symbol is imported from a different module. The bare-name lookup stays a
    last resort for re-export chains the qname/fi resolution misses."""
    rec = registry.find_record_by_qname(f"{user_module}.{method}")
    rec_qual = (registry.record_qualification(rec, module_name)
                if rec is not None else None)
    qual = (rec_qual
            or ((fi.originating_module, fi.name)
                if fi and fi.originating_module else None)
            or lookup_qualified(module_attributes, method, module_name))
    if qual is not None:
        qual_module, qual_name = qual
    else:
        qual_module = user_module
        qual_name = fi.name if fi else method
    return qualified_cpp_name(qual_module, qual_name)


def imported_variable_cpp(registry, imported_names: 'dict[str, tuple[str, str]]',
                          name: str) -> str | None:
    """The cross-module imported-variable spelling, or None when `name` is
    not an imported module-level VARIABLE. The ONE qualification decision
    behind every cross-module variable read.
    Detection keys on the IMMEDIATE import source's `variables` dict (the
    shadow-resilient `imported_names` history, not the attribute table);
    the spelling follows the re-export chain to the ultimate defining
    module so the qname renders against a module that actually emits the
    symbol. A native_global variable spells its user-specified C++ symbol
    name instead (already absolute-qualified at registration)."""
    imp = imported_names.get(name)
    if imp is None:
        return None
    src_mod, orig = imp
    src_info = registry.get_module(src_mod)
    if src_info is None or orig not in src_info.variables:
        return None
    source_module, original_name = resolve_definer(
        registry, src_mod, orig, SymbolKind.VARIABLE)
    source_info = registry.get_module(source_module)
    if source_info is not None:
        var_info = source_info.variables.get(original_name)
        if var_info is not None and var_info.native_cpp_name is not None:
            return var_info.native_cpp_name
    return qualified_cpp_name(source_module, original_name)


def _native_facade_init_targets(registry, native_module: str) -> list[str]:
    """Defining modules of variables re-exported by ``native_module``.

    Records, functions, and protocols re-exported by the facade are pure
    declarations; only re-exported variables involve runtime construction the
    consumer must trigger. The attribute table's VARIABLE bindings carry the
    chain-flattened ultimate definer in `binding.defining_module`, so a single
    pass over the facade's table yields the set of init targets.
    """
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


def module_init_targets(stmt, *, registry, module_name: str,
                        user_module_imports, all_user_modules: 'set[str]',
                        emitted: 'set[str]') -> list[str]:
    """Modules whose `__tpy_init()` a top-level import chains into, in emit
    order -- the render behind `__tpy_init`'s import statements.

    Top-level lowering resolves the call list here, at lowering time;
    `emitted` is the caller's own once-per-module dedup set, mutated in
    place."""
    info = registry.get_module(stmt.module_name)
    has_init = info is None or info.has_runtime_init
    out: list[str] = []
    if (info is not None and info.is_native_module
            and stmt.module_name in user_module_imports):
        # Native facades have no __tpy_init() of their own; chain into the
        # non-native source modules of any re-exported variables.
        for reached in _native_facade_init_targets(registry, stmt.module_name):
            if reached in emitted:
                continue
            out.append(reached)
            emitted.add(reached)
    if stmt.module_name not in user_module_imports or not has_init:
        return out
    # Dotted imports init each parent package first (Python semantics):
    # "pkg.utils" -> pkg, then pkg.utils.
    parts = stmt.module_name.split('.')
    for i in range(1, len(parts)):
        parent = '.'.join(parts[:i])
        if (parent == module_name or parent not in all_user_modules
                or parent in emitted):
            continue
        parent_info = registry.get_module(parent)
        if parent_info is not None and not parent_info.has_runtime_init:
            # Native/builtin packages have no __tpy_init symbol; mark visited
            # so sibling submodules don't retry.
            emitted.add(parent)
            continue
        out.append(parent)
        emitted.add(parent)
    if stmt.module_name != module_name and stmt.module_name not in emitted:
        out.append(stmt.module_name)
        emitted.add(stmt.module_name)
    return out


def module_native_global_names(top_level_stmts) -> dict[str, str]:
    """Python name -> C/C++ symbol for module-level vars with non-DEFAULT
    linkage -- the map behind every native-global read/write render
    (`qualify_native_name(map[name])`), shared by the generator's
    `ctx.native_global_names` seeding and the THIR seeding mirror (which
    runs before the generator populates ctx). Mirrors the generator's
    seen_globals dedup exactly: first decl of a name wins (a later native
    re-decl of an already-seen DEFAULT name maps nothing), tuple-unpack
    targets count as seen, module_init_local temps are skipped."""
    seen: set[str] = set()
    out: dict[str, str] = {}
    for stmt in top_level_stmts:
        if isinstance(stmt, TpyVarDecl):
            if stmt.module_init_local or stmt.name in seen:
                continue
            seen.add(stmt.name)
            if stmt.linkage != VarLinkage.DEFAULT:
                out[stmt.name] = stmt.native_name or stmt.name
        elif isinstance(stmt, TpyTupleUnpack):
            for name in stmt.targets:
                if name is not None:
                    seen.add(name)
    return out


def static_method_callee_cpp(registry, implicit_stdlib_modules: 'set[str]',
                             module_name: str, class_name: str, method: str,
                             fi, owner=None) -> str:
    """The non-generic static-method-call spelling (`Rec.m(...)` ->
    `Rec::m`). Native records spell the C++ class and any explicit method rename;
    implicit-stdlib peers don't emit a `using ::ns::Foo;` alias (suppressed
    to avoid include cycles), so the class qualifies explicitly there.
    `owner` is sema's resolved record, used when the receiver's spelling
    names no record of its own (`cls` inside a @classmethod)."""
    record_info = registry.get_record(class_name)
    if record_info is None and owner is not None:
        record_info = owner
        class_name = owner.name
    if record_info and record_info.is_native:
        cpp_method = (fi.native_name if fi and fi.native_name
                      else escape_cpp_name(method))
        return f"{record_info.native_name}::{cpp_method}"
    if (record_info is not None
            and record_info.module is not None
            and record_info.module in implicit_stdlib_modules
            and record_info.module != module_name):
        class_name = qualified_cpp_name(record_info.module, record_info.name)
    return f"{class_name}::{escape_cpp_name(method)}"


def module_static_class_cpp(registry, user_module: str,
                            class_short: str) -> str:
    """The module-qualified static call's CLASS spelling (`m.Cls.m(...)`):
    a native record spells its C++ class name, everything else qualifies
    through the module namespace (`::tpyapp::m::Cls`). Only the CLASS
    composition lives here; the caller appends the method half
    (`fi.native_name or escape_cpp_name(method)`) and any targs."""
    record_info = registry.find_record_by_qname(f"{user_module}.{class_short}")
    if record_info and record_info.is_native and record_info.native_name:
        return record_info.native_name
    return qualified_cpp_name(user_module, class_short)


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


def enum_cpp_name(enum_type: TpyType, current_module: str, *,
                  absolute: bool = False, einfo=None) -> str:
    """Authoritative C++ spelling for an enum type.

    Resolution order:
    - @native qname (canonical, sema-normalized to ::-prefixed form);
    - cross-module imported qualification (driven by EnumInfo.module_name);
    - local / nested form.

    `absolute=False` (default): bare short / nested form for local
    enums, which works inside the declaring module's user namespace.
    `absolute=True`: fully-qualify local enums as
    `::tpyapp::<module>::E` for sites that emit at global scope
    (EnumUtil specializations, std::ostream operators outside the
    user namespace block).

    `einfo` lets callers hoist a previously-resolved EnumInfo so this
    helper doesn't repeat the lookup (hot in member-access codegen).
    """
    if einfo is None:
        from ..type_def_registry import enum_info_of
        einfo = enum_info_of(enum_type)
    if einfo is not None and einfo.is_native and einfo.native_name:
        return einfo.native_name
    if (einfo is not None and einfo.module_name is not None
            and einfo.module_name != current_module):
        return qualified_cpp_name(einfo.module_name, enum_type.name)
    if absolute:
        return qualified_cpp_name(current_module, enum_type.name)
    if "." in enum_type.name:
        return enum_type.name.replace(".", "::")
    return enum_type.name


def enum_member_cpp(enum_type: TpyType, current_module: str, member: str) -> str:
    """C++ spelling for a type-level enum member access (`Color.RED` ->
    `Color::Red`, or the @native `native_member` rename). The single source
    for member-access codegen and enum-member default parameter values."""
    from ..type_def_registry import enum_info_of
    einfo = enum_info_of(enum_type)
    member_cpp = (einfo.cpp_member_name_map.get(member, member)
                  if einfo is not None else member)
    return f"{enum_cpp_name(enum_type, current_module, einfo=einfo)}::{member_cpp}"


def loop_var_binding(
    elem_type: TpyType, cpp_var: str, deref_expr: str,
    const_loop_var: bool, hoisted: bool = False,
    consuming: bool = False,
    hoisted_tuple_lift_cpp: str | None = None,
) -> str:
    """Return the C++ loop variable binding line (no trailing newline).

    Shared by for-loop and comprehension codegen to avoid duplicating the
    const_loop_var / value_type / auto&& decision tree.

    consuming=True uses auto&& to bind into OwnIter's move-iterator
    storage. Zero cost (no per-element move), but move-ready: codegen
    can later emit std::move(var) for per-element ownership transfer.

    hoisted_tuple_lift_cpp: a hoisted pointer-repr tuple var is forward-
    declared in BORROW form (std::tuple<..., T*>, see the branch-hoist
    pre-declaration), so the per-iteration assignment from the storage
    element must lift element-wise; the caller passes the borrow C++ type.
    """
    # Own[T] from consuming iterators uses the same binding as T.
    if isinstance(elem_type, OwnType):
        elem_type = elem_type.wrapped
    if hoisted:
        if hoisted_tuple_lift_cpp is not None:
            return (f"{cpp_var} = ::tpy::tuple_to_pointer"
                    f"<{hoisted_tuple_lift_cpp}>({deref_expr});")
        return f"{cpp_var} = {deref_expr};"
    if consuming:
        # Forwarding ref into OwnIter storage: zero-cost, move-ready.
        return f"auto&& {cpp_var} = {deref_expr};"
    # Composite types (variants, tuples) use reference binding -- they may
    # contain heap-allocated members, making copies expensive. Peel readonly:
    # a readonly[tuple[...]] yield is the same composite shape (and a typed
    # copy of its borrow form would not even compile for pointer slots).
    if isinstance(unwrap_readonly(elem_type), (UnionType, TupleType)):
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


def is_constructor_call(expr: 'TpyExpr',
                       get_record: 'Callable[[str], object | None]') -> bool:
    """Whether this call CONSTRUCTS a value, i.e. yields a C++ prvalue.

    Three spellings reach it, and the middle one is why this is shared rather
    than re-derived: a builtin or generic type instantiation carries
    `call_type`; a resolved user-record constructor carries `is_constructor`
    on its fi, which holds however the callee is SPELLED (`cls(...)` inside a
    @classmethod names no record at all); and a record lookup by name covers
    the builtin constructors whose fi is a `@cpp_template` `__init__` rather
    than a synthetic ctor.

    Binding `auto&` to such a result does not compile, so every
    iterable-lvalue decision must ask exactly this question.
    """
    if not isinstance(expr, TpyCall):
        return False
    if expr.call_type is not None:
        return True
    rfi = expr.resolved_function_info
    if rfi is not None and rfi.is_constructor:
        return True
    return (isinstance(expr.func, TpyName)
            and get_record(expr.func_name) is not None)


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
        if is_constructor_call(expr, get_record):
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

# Reflected (right-hand) dunders -> the same C++ operator, emitted as a friend
# with the operands swapped (the record is the right operand). Mirrors
# BINOP_TO_RMETHOD; no comparisons (Python reflects those via __lt__/__gt__) and
# no __rpow__ (pow is std::pow, not an operator -- like forward __pow__, it is
# not exposed as a friend operator).
DUNDER_TO_REVERSE_BINARY_OP: dict[str, str] = {
    "__radd__": "+", "__rsub__": "-", "__rmul__": "*",
    "__rtruediv__": "/", "__rfloordiv__": "/", "__rmod__": "%",
    "__rand__": "&", "__ror__": "|", "__rxor__": "^",
    "__rlshift__": "<<", "__rrshift__": ">>",
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


class CondRegion:
    """One open conditional-operand region (see `TempState.conditional_region`).

    `prefix` is empty until the region closes; the emit site appends it in
    front of the operand render.
    """

    def __init__(self):
        self.prefix: str = ""
        self._slots: list[tuple[int, str, str]] = []

    def bank(self, index: int, name: str, emplace_arg: str) -> None:
        self._slots.append((index, name, emplace_arg))


def _slot_spellable(cpp_type: str) -> bool:
    """True if `std::optional<cpp_type>` is a legal spelling.

    `auto` cannot appear in a template argument list, and `std::optional` of a
    reference is ill-formed before C++26 -- such temps stay eager.
    """
    stripped = cpp_type.strip()
    if stripped.endswith("&"):
        return False
    return not re.match(r"^(const\s+)?auto\b", stripped)


def banks_in_region(cpp_type: str, movable: 'bool | None') -> bool:
    """True if a temp of this shape goes into an open conditional-operand
    region's deferred `std::optional` slot instead of hoisting eagerly at the
    enclosing statement.

    The single authority for that question: the lowering gate PREDICTS it to
    decide whether an eager placement ahead of the guard is acceptable, and
    both temp sinks act on it. Two copies of the condition drifted apart once
    already, which is how a conditionally-evaluated argument's copy came to
    run unconditionally.
    """
    return bool(movable) and _slot_spellable(cpp_type)


def _as_expression(type_cpp: str, init_expr: str, brace_init: bool) -> str:
    """Render an initializer as a standalone expression of type `type_cpp`.

    A temp's initializer is authored for a copy-initialized declaration
    (`T t = init;`), where a braced form carries the target type implicitly.
    Forwarding the same string (to `emplace`, or to a relocated
    `std::optional<T> t = ...;` decl) puts it where nothing supplies that
    type: `emplace` deduces `initializer_list<U>` from the elements alone and
    `std::optional` has no initializer-list constructor. Naming the type
    closes both holes. Whether the render is braced is decided deep in the
    array-literal target-type cascade, so it cannot be re-derived from the
    string by the callers that produce it.
    """
    if brace_init:
        return f"{type_cpp}{{{init_expr}}}"
    if init_expr.startswith("{"):
        return f"{type_cpp}{init_expr}"
    return init_expr


class TempState:
    """Manages temporary variables for array literals passed to mutable reference params."""

    def __init__(self):
        self._pending: list[tuple[str, str, str | None, bool]] = []
        self._pending_named: list[tuple[str, str, str | None, bool]] = []
        self._counter: int = 0
        self._regions: list[CondRegion] = []

    @contextmanager
    def conditional_region(self) -> Iterator[CondRegion]:
        """Defer temps created inside to the operand instead of the statement.

        A temp hoisted for a conditionally-evaluated operand (an `and`/`or`
        RHS, a ternary arm, a later chained-comparison comparator) would
        otherwise be initialized at the enclosing statement, running work
        Python skips. Inside a region a temp is declared as an uninitialized
        `std::optional<T>` and its initializer is banked as
        `__tmp_N.emplace(init)` for the emit site to splice in front of the
        operand, so it runs only when the branch is taken. Block-scope
        lifetime is preserved (unlike a statement expression, which would end
        the temp's life at the operand and dangle any view taken of it).

        Regions nest: a temp banks to the innermost open region, which sits
        inside any enclosing one, so a nested `a and (b and c)` chain defers
        at each level.
        """
        region = CondRegion()
        self._regions.append(region)
        try:
            yield region
        finally:
            self._regions.pop()
            self._close_region(region)

    def _close_region(self, region: CondRegion) -> None:
        parts = []
        for index, name, emplace_arg in region._slots:
            if index >= len(self._pending) or self._pending[index][0] != name:
                # An intervening flush relocated the decl into a nested scope
                # that is itself inside the conditional (a comprehension loop
                # body), or a rollback discarded the render. Leaving the entry
                # untouched keeps its eager `std::optional<T> t = init;` form,
                # which is correct in that position and still derefs as
                # `(*t)`.
                continue
            _, cpp_type, _, brace_init = self._pending[index]
            self._pending[index] = (name, cpp_type, None, brace_init)
            parts.append(f"{name}.emplace({emplace_arg})")
        region.prefix = "".join(f"{p}, " for p in parts)

    def _register(self, temp_name: str, type_cpp: str,
                  init_expr: str, brace_init: bool, movable: bool) -> str:
        """Queue a temp decl and return the expression that reads it."""
        region = self._regions[-1] if self._regions else None
        if region is None or not banks_in_region(type_cpp, movable):
            self._pending.append((temp_name, type_cpp, init_expr, brace_init))
            return temp_name
        emplace_arg = _as_expression(type_cpp, init_expr, brace_init)
        region.bank(len(self._pending), temp_name, emplace_arg)
        self._pending.append(
            (temp_name, f"std::optional<{type_cpp}>", emplace_arg, False))
        return f"(*{temp_name})"

    def create(self, param_type: TpyType, init_expr: str) -> str:
        """Create a temp variable and return its name for use in the call."""
        param_type = unwrap_ref_type(param_type)
        self._counter += 1
        temp_name = f"__tmp_{self._counter}"
        is_protocol = is_protocol_type(param_type)
        type_cpp = "auto" if is_protocol or isinstance(param_type, TypeParamRef) else param_type.to_cpp()
        return self._register(temp_name, type_cpp, init_expr, False,
                              param_type.is_movable())

    def create_typed(self, cpp_type: str, init_expr: str, *,
                     brace_init: bool = False, movable: bool = False) -> str:
        """Create a temp variable with an explicit C++ type.

        `movable` gates deferral into an open conditional region and defaults
        to False because a C++ spelling alone does not answer it: deferring
        emplaces the temp, and `emplace(T(...))` needs a move ctor that the
        eager `T t = T(...);` form does not (it gets guaranteed elision). A
        caller holding the TpyType can opt back in with `type.is_movable()`.
        """
        self._counter += 1
        temp_name = f"__tmp_{self._counter}"
        return self._register(temp_name, cpp_type, init_expr, brace_init,
                              movable)

    def declare_named_auto(self, prefix: str, cpp_type: str, *, init: str | None = None) -> str:
        """Register a uniquely-named hoisted declaration and return its name.

        For a block-scoped slot that must outlive the expression referencing it
        (e.g. the optional<T> backing a short-circuit pointer-select), where the
        caller needs the generated name back rather than supplying it."""
        self._counter += 1
        name = f"{prefix}_{self._counter}"
        self._pending_named.append((name, cpp_type, init, False))
        return name

    def declare_named(self, name: str, cpp_type: str, *,
                      init: str | None = None, brace_init: bool = False) -> None:
        """Register a named pre-declaration (for walrus operator variables)."""
        self._pending_named.append((name, cpp_type, init, brace_init))

    def has_pending_since(self, checkpoint: tuple[int, ...]) -> bool:
        """True if anonymous temps were registered after `checkpoint`."""
        return len(self._pending) > checkpoint[0]

    def has_named_since(self, checkpoint: tuple[int, ...]) -> bool:
        """True if named pre-declarations were registered after `checkpoint`."""
        return len(self._pending_named) > checkpoint[1]

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
        self._render(out, indent, self._pending_named, self._pending)
        self._pending_named.clear()
        self._pending.clear()

    def flush_since(self, out: TextIO, checkpoint: tuple[int, int], indent: str) -> None:
        """Emit (and remove) only the anonymous temps registered after `checkpoint`.

        Relocates per-iteration comprehension/genexpr temps into the nested loop
        body (where the loop var is in scope and the temp recurs each iteration)
        instead of the enclosing statement flush. Named pre-declarations stay for
        the enclosing flush: a comprehension walrus target binds in the
        containing scope (PEP 572), so it must not move into the loop body.
        """
        pending_n, _named_n = checkpoint
        self._render(out, indent, [], self._pending[pending_n:])
        del self._pending[pending_n:]

    def flush_named_since(self, out: TextIO, checkpoint: tuple[int, int],
                          indent: str) -> None:
        """Emit (and remove) only the named pre-declarations registered after
        `checkpoint`.

        For emission contexts whose enclosing scope is not the statement
        flush's scope (the simple-generator lambda): a walrus target bound in
        the condition must be declared inside the lambda body, not left for a
        flush the lambda never runs.
        """
        named_n = checkpoint[1]
        self._render(out, indent, self._pending_named[named_n:], [])
        del self._pending_named[named_n:]

    @staticmethod
    def _render(out: TextIO, indent: str,
                named: list[tuple[str, str, str | None, bool]],
                pending: list[tuple[str, str, str | None, bool]]) -> None:
        for name, cpp_type, init_val, brace_init in named:
            if init_val is not None:
                out.write(f"{indent}{cpp_type} {name} = {init_val};\n")
            elif brace_init:
                out.write(f"{indent}{cpp_type} {name}{{}};\n")
            else:
                out.write(f"{indent}{cpp_type} {name};\n")
        for temp_name, type_cpp, init_expr, brace_init in pending:
            if init_expr is None:
                # A conditional-region slot: the initializer moved into the
                # operand as an emplace, so only the empty slot is declared.
                out.write(f"{indent}{type_cpp} {temp_name};\n")
            elif brace_init:
                out.write(f"{indent}{type_cpp} {temp_name}{{{init_expr}}};\n")
            else:
                out.write(f"{indent}{type_cpp} {temp_name} = {init_expr};\n")


def any_isinstance_check(subject_cpp: str, member_cpps: 'tuple[str, ...]') -> str:
    """The Any-isinstance condition render (D15): the has_value guard + one
    typeid check per member (tuple form OR-joined in one paren group). An
    empty/moved-from Any has no value() and is not any concrete type, so the
    guard is essential. The one spelling behind `THIRAnyIsinstance`."""
    if len(member_cpps) == 1:
        return (f"({subject_cpp}.value.has_value() && "
                f"{subject_cpp}.value.type() == typeid({member_cpps[0]}))")
    checks = " || ".join(f"{subject_cpp}.value.type() == typeid({m})"
                         for m in member_cpps)
    return f"({subject_cpp}.value.has_value() && ({checks}))"


def contains_named_expr(expr: TpyExpr | None) -> bool:
    """True if `expr` contains a walrus (TpyNamedExpr) anywhere in its subtree.

    Gates the while-head temp restructure: a walrus is the only way a value
    produced in a condition escapes it, and per-iteration temps must neither
    run before the walrus assignment they read nor be aliased by a binding
    that outlives the loop (see the mixed walrus+temp BUGS.md entry)."""
    if expr is None:
        return False
    if isinstance(expr, TpyNamedExpr):
        return True
    return any(contains_named_expr(c) for c in expr.children())


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


@contextmanager
def stamp_codegen_error_file(source_name: str, is_entry_point: bool):
    """Stamp a codegen error raised while emitting a non-entry module with
    that module's source name. Without it every codegen error in a
    multi-module program formats against the ENTRY module's filename (the
    `format()` fallback), so a `# tpyc: error(...)` annotation could never
    sit on the real subject line. The caller supplies the spelling it wants
    the user to see."""
    try:
        yield
    except CodeGenError as e:
        if e.filename is None and not is_entry_point:
            e.filename = source_name
        raise


class ThirRejectError(CodeGenError):
    """A construct THIR has no lowering for. Kept distinct from other codegen
    diagnostics so tooling can tell a lowering GAP (the source is valid TPy;
    the compiler is what is missing) from a diagnostic about the source, and
    carries the failing position and reason tag as fields rather than only in
    its message."""

    def __init__(self, message: str, loc: SourceLocation | None = None,
                 filename: str | None = None, *, component: str | None = None,
                 reason: str | None = None):
        super().__init__(message, loc, filename)
        self.component = component
        self.reason = reason


@dataclass
class CodeGenOptions:
    """Options for C++ code generation."""
    emit_source_comments: bool = False  # Embed Python source as comments in generated C++
    comment_line_numbers: bool = True   # Include .py line numbers in source comments
    no_main: bool = False               # Skip main() generation, emit __tpy_main() only


class LocalCppForm(Enum):
    """Coarse C++ representation of a local/param name.

    The codegen tracks several parallel `set[str]` fields (`pointer_locals`,
    `optional_locals`, `ptr_variant_locals`, `storage_form_tuple_locals`,
    `storage_form_optional_locals`) plus a `current_func_params` lookup
    for `Own[Union]` params. Every boundary-handling site (call arg,
    return, var-decl init, rebind, tuple-unpack, etc.) needs to ask
    "what shape is this name?" and route to the right lift / wrap
    helper. `local_cpp_form` is the single classifier those sites consult.

    Variants:
      * `POINTER` -- `T*` / `const T*`. Pointer-form Optional or hoisted
        non-value local. Membership in `pointer_locals` minus
        `optional_locals`.
      * `OPTIONAL_STORAGE` -- `std::optional<T>` storage form. `Own[Opt[T_ref]]`
        params (also in `pointer_locals` for arrow field access). Lifts via
        `tpy::optional_to_ptr` when consumed as a `T*` slot.
      * `STORAGE_OPTIONAL` -- `std::optional<T>` storage form, but from
        an iteration source (for-loop var iterating `list[P|None]` /
        `dict[K, P|None]`, comprehension/genexpr unpack var bound from
        a storage-form-tuple slot). Same lift as `OPTIONAL_STORAGE`
        (`tpy::optional_to_ptr`); separate variant because these names
        are NOT in `pointer_locals` (their access doesn't route through
        `->`) and may be in `const_storage_form_optional_locals` for
        const-source iteration.
      * `VALUE_VARIANT` -- `::tpy::Union<A, B>` storage form. `Own[Union nonvalue]`
        params at the ABI. Lifts via `tpy::to_ptr_variant` when consumed
        as a pointer-variant slot.
      * `PTR_VARIANT` -- `::tpy::Union<T*, ...>` borrow form. Non-value
        union local already in pointer-variant shape; no lift needed.
      * `STORAGE_TUPLE` -- `std::tuple<std::optional<T>, ...>` storage form.
        For-loop variables iterating storage containers, locals initialized
        from another storage-form source, `Own[tuple[T|None, ...]]` params.
        Wraps via `tpy::tuple_to_pointer` when feeding pointer-form tuple
        params/destructure targets.
      * `BORROW_TUPLE` -- `std::tuple<..., T*>` borrow form. A reassigned /
        branch-hoisted pointer-repr tuple local: it aliases storage (its
        element pointers), an owning-call RHS materializes into a storage
        slot, and a storage-form lvalue RHS lifts via `tpy::tuple_to_pointer`.
        The tuple analog of a scalar rvalue-reassigned `POINTER` local.
      * `OPTIONAL_BORROW_TUPLE` -- `std::optional<std::tuple<..., T*>>`. A
        nullable BORROW_TUPLE: `tuple[..., Box] | None` local whose inner tuple
        takes borrow form so reference elements alias storage on rebind. Same
        storage<->pointer wrap as BORROW_TUPLE but lifted into / out of the
        `std::optional`; narrowed access derefs via `*t`; `t = None` is nullopt.
      * `VALUE` -- everything else (value types, T& ref-bound locals,
        plain non-value locals rendered via T&).
    """
    POINTER = auto()
    OPTIONAL_STORAGE = auto()
    STORAGE_OPTIONAL = auto()
    VALUE_VARIANT = auto()
    PTR_VARIANT = auto()
    STORAGE_TUPLE = auto()
    BORROW_TUPLE = auto()
    OPTIONAL_BORROW_TUPLE = auto()
    VALUE = auto()


class CppForm(Enum):
    """The borrow-vs-storage axis of a non-value type's C++ representation.

    Coarser than `LocalCppForm` (which encodes the specific local shapes
    codegen tracks): `CppForm` is the *kind* of slot -- does it borrow its
    data or own it -- which is the only axis `convert()` bridges. The
    concrete helper is then selected from the value's TPy type kind
    (Optional -> optional_to_ptr family, Union -> variant family, Tuple ->
    tuple family, record -> address-of) and the const flag. For value types
    the two forms coincide (cheaply copyable), so `VALUE` needs no bridge.

      * `BORROW`   -- `T*` / `const T*` / `T&` / `::tpy::Union<A*, B*>` /
                      `std::tuple<..., T*>`. Indirect into storage elsewhere.
      * `STORAGE`  -- `T` / `std::optional<T>` / `std::variant<A, B>` /
                      `std::tuple<..., std::optional<T>>`. Self-contained.
      * `VALUE`    -- value type; borrow and storage coincide.
    """
    BORROW = auto()
    STORAGE = auto()
    VALUE = auto()


@dataclass
class FormValue:
    """A rendered C++ expression plus the facts `convert()` needs to bridge
    its form to a destination slot.

    The load-bearing rule (see `convert`): `form` is a *derived* fact handed
    over by the producing emitter, never re-guessed by `convert()` from
    `code` + `type`. A single TPy type (`Box | None`) can render as either
    `Box*` (borrow) or `std::optional<Box>` (storage) depending on
    `force_pointer_repr` / storage-field / local form, so the producer -- the
    only site that knows which C++ it emitted -- must record it here.

    Fields:
      * `code`     -- the rendered C++ expression string.
      * `type`     -- its TPy type (selects the conversion helper family).
      * `form`     -- BORROW / STORAGE / VALUE, as actually emitted.
      * `is_const` -- the borrow is const-qualified (`const T*` / `const T&` /
                      const-ptr-variant); picks the const helper overload.
      * `move`     -- this is a last-use move into an owned sink (an ownership
                      decision made by the producer/call site, NOT by
                      `convert()`); selects the `_move` helper variant.
    """
    code: str
    type: TpyType
    form: CppForm
    is_const: bool = False
    move: bool = False


@dataclass
class LocalScopeSnap:
    """Snapshot of the C++ local-variable declaration state inside a function body.

    Covers every field that tracks which locals exist and what C++ representation
    they use (pointer-local, const-indirect, movable, rebind slot), plus the
    two isinstance-narrowing maps -- `narrowed_vars` (per-source-variable
    alias bindings) and `declared_persistent_aliases` (the scope-global set of
    persistent alias names, used by `_fresh_alias_local` for collision
    avoidance). Both maps reference C++ locals that live only within the
    block that declared them, so they must be revoked when the surrounding
    C++ block closes. Used to restore scope between if/else branches so that
    declarations inside one branch don't bleed into sibling branches.

    If a new "what locals exist" field is added to CodeGenContext, add it here too.

    Note: hoisted_vars and branch_hoisted_vars are NOT snapshotted -- they are
    function-scoped accumulators. A stale entry from a prior branch's nested if
    causes unnecessary hoisting but not incorrect code. borrow_form_tuple_locals
    is excluded for the same reason: its names are branch-hoisted declarations
    emitted BEFORE the branches, so they are correct for every sibling branch
    and persist to function exit (reset_scope clears them). Similarly,
    frame_field_shadows is NOT snapshotted -- entries are either discarded by
    the emit site that introduced them (for-loop iter var, scoped to the loop
    body) or persist to function exit where `reset_scope` clears them (with-as
    / tuple-unpack, which outlive their source statement in Python scoping).
    Both lifetimes are correct relative to branch-snapshot boundaries.
    """
    declared_vars: set[str]
    var_types: dict[str, TpyType]
    local_scope_names: set[str]
    pointer_locals: set[str]
    optional_locals: set[str]
    ptr_variant_locals: set[str]
    const_indirect_locals: set[str]
    storage_form_tuple_locals: set[str]
    const_storage_form_tuple_locals: set[str]
    own_borrow_tuple_locals: set[str]
    storage_form_optional_locals: set[str]
    const_storage_form_optional_locals: set[str]
    movable_locals: set[str]
    ref_bound_locals: set[str]
    rebind_slots: dict[str, str]
    plain_rebind_slots: set[str]
    assign_narrowed_types: dict[str, 'TpyType']
    narrowed_vars: dict[str, str]
    declared_persistent_aliases: set[str]


@dataclass
class FinallyContext:
    """Tracks an active try/finally (or with) block.

    The finally body is emitted inline at each exit site: before any
    fall-through, return, break, or continue out of the try block, and
    inside the catch(...) wrapper for the throw path. The unified pattern
    is `try { body } catch (...) { <finally>; throw; } <finally>;` with
    explicit `<finally>;` calls inserted before any non-throw exit while
    the frame is on the stack.

    The same shape is used by the resumable-frame async codegen, where
    each suspension's case body re-establishes the active try/finally
    structure -- making the lowering shared across sync and async.
    """
    emit_finally: Callable[[TextIO, str], None]
    """Emit the finally body inline at the given (out, indent)."""

    terminates: bool
    """True if the finally body's last reachable statement is raise/return.
    When set, callers must suppress any trailing `throw;` / `return ...;`
    after invoking emit_finally, since the body itself transferred control."""

    loop_depth: int
    """len(loop_else_labels) at frame push time. break/continue walk
    finally frames whose loop_depth >= current loop count, since those
    are the frames pushed inside the innermost active loop."""

    guard_name: str | None = None
    """Name of this frame's `bool __fin_ran_<n>` guard, set true right
    before an exit-site copy of the finally runs. The frame's own
    catch(...) tests it and skips its copy, so a finally that RAISES at
    an exit site is not re-run by the catch it throws into. An OUTER
    frame's guard is still false there, so its finally still runs --
    which is what Python requires. The guard is declared/tested only when
    an exit site actually walked this frame -- tracked by membership of
    the name in `CodeGenContext.live_finally_guards` (see there); a
    try/finally with no return/break/continue inside emits no guard."""


@dataclass
class CodeGenContext:
    """Shared state for C++ code generation."""

    # --- Core ---
    analyzer: SemanticAnalyzer
    options: CodeGenOptions
    module_name: str = "generated"
    source_lines: list[str] = field(default_factory=list)
    # Lowered bodies, keyed by the source TpyFunction. A per-@overload-stub
    # specialization lands in `thir_overload_functions` instead (impl -> stub
    # -> body). Populated per-module in CodeGenerator.generate.
    thir_functions: IdentityMap = field(default_factory=IdentityMap)
    thir_overload_functions: IdentityMap = field(default_factory=IdentityMap)
    # The `__tpy_init` body, lowered from the module's top-level statements.
    # Seeded right before gen_module_init, which is where the generator's
    # global-type map exists.
    thir_top_level: "THIRFunction | None" = None
    # A constructor's member-init-list + body tail emit from its
    # THIRConstructor; the signature comes from the record skeleton.
    # Keyed by the source __init__ TpyFunction; consumed in gen_record_decl.
    thir_constructors: IdentityMap = field(default_factory=IdentityMap)
    # Attempt-once cache of async-body leaf lowerings, keyed by the
    # source TpyFunction. Populated lazily at first frame
    # emission (the CFG needs live codegen ctx), unlike the seeding-loop maps.
    thir_resumables: IdentityMap = field(default_factory=IdentityMap)
    # The active body's leaf renderer while gen_async emits its state
    # machine; every seam site (leaf stmts, Branch conds, emplace args, the
    # async-return value) consults it. None outside a frame emission.
    thir_resumable_leaf: "ResumableLeafEmitter | None" = None
    # A peephole generator's leaves (init block, while cond, pre-/post-yield
    # blocks, yield value, iterable / range args) emit from its
    # THIRSimpleGenBody inside the skeleton's
    # lambda. Keyed by the source TpyFunction; populated in
    # the same seeding loop as `thir_functions` (no live-ctx dependency,
    # unlike resumables); consumed in gen_simple_generator_inline.
    thir_simple_gens: IdentityMap = field(default_factory=IdentityMap)
    # Peer modules in the same import-graph SCC. When a `<peer>.hpp`
    # would be included from this module's header (vs cpp file), the
    # codegen swaps it for `<peer>_fwd.hpp` to break the cyclic
    # complete-type include. Empty for non-cycle modules.
    cycle_peers: frozenset[str] = field(default_factory=frozenset)

    # --- Scope tracking ---
    indent_level: int = 0
    declared_vars: set[str] = field(default_factory=set)
    var_types: dict[str, TpyType] = field(default_factory=dict)
    local_scope_names: set[str] = field(default_factory=set)
    nested_def_locals: set[str] = field(default_factory=set)
    # Walrus pre-declarations already emitted (not snapshot/restored across branches)
    walrus_pre_declared: set[str] = field(default_factory=set)
    # Deref rewrites for walrus optional-slot locals. The pre-decl is
    # function-scope (hoisted via temps), so the `(*name)` read rewrite must
    # survive the branch-scope narrowed_vars restores the same way
    # walrus_pre_declared does -- restore_local_scope re-applies these.
    walrus_deref_rewrites: dict[str, str] = field(default_factory=dict)
    # Walrus locals holding the owning STORAGE tuple form: their
    # storage_form_tuple_locals membership must survive the same restores.
    walrus_storage_tuple_locals: set[str] = field(default_factory=set)
    # Resumable-frame `__await_lift_*` temps -- one-shot/movable sources, so a
    # tuple-unpack reading one binds by rvalue-ref and moves its elements out
    # rather than copying the whole tuple (assigned fresh per resumable body in
    # setup_resumable_frame_locals; read only under in_generator_body).
    one_shot_lift_locals: set[str] = field(default_factory=set)
    # Borrow-only walrus locals bound as T* pointers (decl is function-scope
    # via temps): pointer_locals/declared membership (+ const) must survive
    # the branch-scope restores like the rewrites above.
    walrus_pointer_locals: set[str] = field(default_factory=set)
    walrus_const_pointer_locals: set[str] = field(default_factory=set)
    # Owning-slot name for a function-scope tuple slot (a branch-hoisted owning
    # tuple local, or a reassigned-borrow walrus) whose `rebind_slots` mapping
    # must survive branch-scope restores. The slot decl is function-scope
    # (pending_hoist_decls / temps), but rebind_slots IS snapshot/restored, so
    # without this a sibling branch would allocate a SECOND slot for the same
    # local. Re-applied to rebind_slots in restore_local_scope.
    persistent_rebind_slots: dict[str, str] = field(default_factory=dict)
    global_names: set[str] = field(default_factory=set)
    current_ns: Namespace | None = None
    in_method: bool = False
    in_consuming_method: bool = False
    in_property_getter: bool = False
    # The NominalType of the record whose method is currently being emitted.
    # Set by records.py / functions.py around method bodies and read by
    # `lookup_var_type` so `self` resolves to the enclosing class type --
    # enables `isinstance(self, Sub)` polymorphic-dispatch routing in codegen.
    current_method_record_type: 'TpyType | None' = None
    current_return_type: TpyType | None = None
    # Exact C++ spelling of the current function's emitted return type
    # (signature source of truth, including std::expected wrapping and
    # method special cases). Read by _make_return to type the pre-finally
    # return-value temp; 'auto' would reject the braced / std::nullopt
    # spellings some return sites pass.
    current_return_cpp: str | None = None
    # Whether the current function's return slot was rendered const (a readonly
    # method projects const onto its borrow returns). The signature reads
    # `func.is_readonly` at `_resolve_return_type(const=...)`; return-value
    # emission must read the SAME fact, or the body builds a mutable-pointer
    # value against a const-pointer slot.
    current_return_const: bool = False
    # Generator yield type. Set at generator-body entry points (state-machine
    # __next__, simple-for/simple-while inline lambdas) and read by all yield
    # emission sites so they share one source of truth instead of threading
    # the type through call signatures.
    current_yield_type: TpyType | None = None
    current_error_return: str | None = None
    error_return_stmt_handled: bool = False
    current_func_params: dict[str, TpyType] = field(default_factory=dict)
    current_type_param_bounds: dict[str, TpyType] = field(default_factory=dict)
    const_ref_params: set[str] = field(default_factory=set)
    # Param names whose declared const-inferred surface goes deep -- inner
    # tuple slots, pointer-form Optional inner, etc. Drives body codegen
    # decisions for tuple unpack, alias propagation, call-site lowering.
    # Subset of const_ref_params for ordinary T& params; also includes
    # Optional/Tuple param names that const_ref_params doesn't track.
    deep_const_borrow_params: set[str] = field(default_factory=set)

    # --- Temporary variable management ---
    temps: TempState = field(default_factory=TempState)

    # --- Global declaration tracking (from `global x` statements) ---
    global_declared_vars: set[str] = field(default_factory=set)

    # --- Pointer-local tracking ---
    pointer_locals: set[str] = field(default_factory=set)
    # Locals whose C++ shape is std::optional<T> (storage form) rather than
    # T* (pointer form). Two populations:
    #   - Non-value hoisted vars not reassigned (branch-emitted as
    #     std::optional<T> to avoid a separate T*+slot pair).
    #   - Own[OptionalType[P_ref]] params (rendered as std::optional<P>&&).
    # Both also live in pointer_locals so the field-access dispatch routes
    # via optional<T>::operator-> (correct arrow). The is_storage_form_-
    # optional_source predicate deliberately excludes them; consumer sites
    # that need an optional_to_ptr lift to feed a T* slot check
    # optional_locals directly (the pointer-form value read, the optional-ptr
    # argument pass, and the var-decl / rebind paths).
    optional_locals: set[str] = field(default_factory=set)
    const_indirect_locals: set[str] = field(default_factory=set)
    # Pre-bound polymorphic-isinstance cast locals for the C++17 if-init form
    # (var -> ptr local name), bound before the condition is emitted; the
    # isinstance bool-check and cast-and-cache extraction both consult this
    # map and reuse the local instead of re-emitting dynamic_cast.
    isinstance_init_locals: dict[str, str] = field(default_factory=dict)
    # Locals whose tuple type contains pointer-repr Optional but whose C++
    # representation is the storage form (std::tuple<std::optional<T>, ...>):
    # for-loop variables iterating storage-form containers, and locals
    # initialized from a storage-form source (field, subscript, global).
    # Read sites need a tuple_to_pointer wrap to flow into pointer-form
    # tuple params/destructure targets.
    storage_form_tuple_locals: set[str] = field(default_factory=set)
    # Subset of storage_form_tuple_locals: locals iterated from a const-bound
    # source (const list&, dict.values(), self.field in readonly method, ...).
    # When unpacking these, the storage->pointer wrap must produce const slots
    # because optional_to_ptr returns `const T*` from a const optional<T>&.
    const_storage_form_tuple_locals: set[str] = field(default_factory=set)
    # Subset of storage_form_tuple_locals: locals bound from a per-element-Own
    # tuple RETURN, whose C++ shape is that return's MIXED borrow render
    # (`std::tuple<A, B*>`) rather than a storage materialization. They are
    # storage-form for the whole-tuple lift (the owned element still needs
    # `tuple_to_pointer`) but their ref elements are already pointers, so a
    # per-element read must not take them for by-value slots.
    own_borrow_tuple_locals: set[str] = field(default_factory=set)
    # Locals whose C++ shape is `std::optional<T>` (storage form) because they
    # bind a pointer-repr-Optional element of a storage-form source:
    #   * for-loop variable iterating `list[P|None]` / `dict[K, P|None]`
    #   * comprehension/genexpr unpack variable whose tuple slot is
    #     pointer-repr-Optional from a storage-form source
    # `is_storage_form_optional_source(TpyName(...))` returns True for these;
    # consumer sites needing `P*` insert `optional_to_ptr` based on it.
    # Disjoint from `optional_locals` (which tracks Own[Opt[T_ref]] params).
    storage_form_optional_locals: set[str] = field(default_factory=set)
    # Subset of storage_form_optional_locals: locals bound from a const-bound
    # source (mirror of const_storage_form_tuple_locals -- when the producing
    # iteration yields const optional<T>&, the lift must produce const T*).
    const_storage_form_optional_locals: set[str] = field(default_factory=set)
    # Names where codegen has emitted a C++-scoped local declaration in the
    # current scope that shadows a putative frame field of the same name.
    # Sema treats every function-level local as a potential frame field
    # (added to func.generator_locals -> generator_optional_fields), but
    # for-loop iter vars on loops without internal yield/await are emitted
    # as `auto&& it = *__beg_n` C++ locals instead. Body-emit consults this
    # set to suppress the `(*name)` peel that would otherwise apply to a
    # putative storage-form-optional frame field. NOT in LocalScopeSnap --
    # the set is body-emit scoped; entries are added and discarded around
    # the emit site that introduced the shadow.
    frame_field_shadows: set[str] = field(default_factory=set)

    # --- Pointer-variant locals (non-value union variables) ---
    # Variables that are ::tpy::Union<T*...> instead of ::tpy::Union<T...>.
    ptr_variant_locals: set[str] = field(default_factory=set)
    pointer_globals: set[str] = field(default_factory=set)
    final_globals: set[str] = field(default_factory=set)
    slots: SlotState = field(default_factory=SlotState)
    rebind_slots: dict[str, str] = field(default_factory=dict)
    plain_rebind_slots: set[str] = field(default_factory=set)
    # Rebind-slot declarations held back until a rebind actually consumes the
    # slot. The slot is reserved at the declaration site (a later rebind must
    # not emplace over the init value an alias may hold), but whether any
    # rebind follows is not known there -- a name whose every assignment is a
    # fresh declaration in its own scope would otherwise leave a dead
    # `std::optional<T>` per declaration. Flushed by `use_rebind_slot`.
    deferred_rebind_slot_decls: dict[str, str] = field(default_factory=dict)
    reassigned_vars: set[str] = field(default_factory=set)
    rvalue_reassigned_vars: set[str] = field(default_factory=set)
    lvalue_reassigned_vars: set[str] = field(default_factory=set)

    # --- Auto-move tracking (last-use -> std::move) ---
    # movable_locals is the WORKING set: the deliberate param seeds
    # (seed_param_locals: Own / owned-tuple / value-opt-expensive params)
    # plus decl-driven adds from sema_movable_locals as decls are emitted.
    # sema_movable_locals is the RAW per-function sema fact; an
    # assigned-never-declared frame field (await result) appears only
    # here. Do not unify: consumers pick the set matching their site.
    movable_locals: set[str] = field(default_factory=set)
    sema_movable_locals: set[str] = field(default_factory=set)
    # Locals ever bound to a fresh rvalue (sema fact). A branch-declared
    # non-value local absent here is borrow-only and must not get owned
    # storage -- its decl form is a pointer that aliases the source.
    sema_ever_owned_locals: set[str] = field(default_factory=set)
    # Statement-level borrow bindings (sema fact): name -> any-const.
    # Together with sema_ever_owned_locals this decides the branch
    # pre-decl form; names bound by with-as/for/match are excluded by sema.
    sema_stmt_borrow_decls: dict[str, bool] = field(default_factory=dict)

    # --- Reference-bound locals (T& aliases -- del must not move-sink) ---
    # ref_bound_locals grows during codegen as T& decls are emitted -> in LocalScopeSnap.
    # aliased_vars/alias_names are set once per function from prescan -> NOT in LocalScopeSnap.
    ref_bound_locals: set[str] = field(default_factory=set)
    aliased_vars: set[str] = field(default_factory=set)
    alias_names: set[str] = field(default_factory=set)

    # --- Move-through vars (lvalue alias promoted to owned via std::move) ---
    move_through_vars: set[str] = field(default_factory=set)

    # --- Hoisted variable tracking ---
    # Locals whose storage must live at FUNCTION scope because a branch
    # construct pre-declares them (try/finally, branch decls).
    hoisted_vars: set[str] = field(default_factory=set)
    # Branch-hoisted pointer-locals: declared by _emit_branch_decls for if/match/try.
    # Rvalue slots for these vars must go to pending_hoist_decls, not block scope.
    branch_hoisted_vars: set[str] = field(default_factory=set)
    # Tuple locals forward-declared in borrow (pointer) form `std::tuple<..., T*>`
    # (no-init branch-hoist of a pointer-repr tuple). Their assignments stay in
    # borrow form -- the storage<->pointer wrap must NOT fire when writing into
    # them (the local IS the borrow, not a storage slot).
    borrow_form_tuple_locals: set[str] = field(default_factory=set)
    # Subset of borrow_form_tuple_locals declared with const element pointers
    # (`std::tuple<..., const T*>`) because at least one binding source is a
    # const-storage location. Codegen derives this as the OR over every source
    # feeding the local (final post-inference const), and every tuple_to_pointer
    # lift into the local must target this const borrow type; a write through a
    # const element is rejected in sema. Populated by `_compute_borrow_tuple_const`.
    const_borrow_form_tuple_locals: set[str] = field(default_factory=set)
    # Nullable borrow-form tuple locals: `tuple[..., Box] | None` declared as
    # `std::optional<std::tuple<..., T*>>`. The optional wraps the borrow-form
    # inner tuple so reference elements ALIAS storage on rebind (matching
    # CPython) rather than copying. Parallel to borrow_form_tuple_locals but
    # the value lives behind `std::optional` (narrowed access derefs via `*t`).
    optional_borrow_tuple_locals: set[str] = field(default_factory=set)
    # Subset of optional_borrow_tuple_locals with const element pointers.
    const_optional_borrow_tuple_locals: set[str] = field(default_factory=set)

    # --- Comprehension-local variable names ---
    # Loop variables inside comprehensions shadow globals during element
    # expression codegen.  Checked early in is_global_name().
    comp_local_names: set[str] = field(default_factory=set)
    pending_hoist_decls: list[str] = field(default_factory=list)
    # Identifies the current hoist scope so a held-back rebind-slot decl
    # drains into the scope that reserved it (see use_rebind_slot).
    hoist_scope_id: int = 0
    _hoist_scope_counter: int = 0
    # slot -> the hoist scope that reserved it; outlives the held-back decl.
    rebind_slot_scopes: dict[str, int] = field(default_factory=dict)

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
    # FunctionGenerator.gen_body right after the opening brace and then
    # cleared so inner bodies don't re-emit them.
    overload_missing_param_locals: list[tuple[str, 'TpyType', object]] = field(default_factory=list)
    # THIR interception key for the per-stub emission in flight:
    # (id(impl), id(stub)). id(func) alone cannot key a specialization --
    # the impl body is emitted once per stub, and the method path emits a
    # synthetic clone whose id matches nothing. gen_body CONSUMES the key
    # on entry so bodies nested under the specialization never inherit it.
    thir_overload_key: "tuple[TpyFunction, TpyFunction] | None" = None

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

    # --- finally (inline emit pattern) ---
    # Stack of active try/finally (and with) blocks. Codegen invokes
    # frame.emit_finally inline before each non-throw exit (return/break/
    # continue/fall-through) and inside each try block's catch(...) wrapper.
    # No shared __retval variable: the exit-site copy keeps the return temp
    # copy-initialized at the site, and a per-frame `bool` guard (see
    # FinallyContext.guard_name) stops the frame's own catch re-running a
    # copy that raised -- parking the value to run the copy outside the try
    # instead would defeat copy elision on every such return.
    finally_stack: list[FinallyContext] = field(default_factory=list)
    # Numbers the `__fin_ran_N` cleanup guards. MUST stay monotonic across the
    # whole compilation -- never reset per function/case: live_finally_guards
    # (below) is a set keyed by these NAMES, so uniqueness is what keeps one
    # frame's membership from aliasing another's. reset_scope leaves it alone.
    finally_guard_counter: int = 0
    # Resumable path only: region -> `bool __fin_ran_N` guard name for the
    # C++ try the current switch case opened for that region. An exit-edge
    # cleanup copy sets it; the region's own catch tests it, so a raising
    # cleanup is not re-run by the catch it throws into (the sync path's
    # FinallyContext.guard_name, expressed across the three resumable emit
    # methods). Populated per case in _emit_case, cleared at case end.
    resumable_region_guards: IdentityMap = field(default_factory=IdentityMap)
    # Live `bool __fin_ran_N` guard names: a guard lands here when an exit
    # edge actually emits its `= true`. The one liveness channel for BOTH
    # paths -- the sync try/finally + with catches and the resumable region
    # catches declare and test a guard only when its name is present, so a
    # cleanup that never runs on a normal exit (e.g. a with-body that always
    # raises) emits no dead guard. Names are globally unique
    # (finally_guard_counter is monotonic), so cross-frame membership is
    # unambiguous; the resumable path clears it per case for hygiene.
    live_finally_guards: set[str] = field(default_factory=set)

    # --- with statement ---
    with_counter: int = 0

    # --- Match/case label counter (for goto-based guard fallthrough) ---
    match_counter: int = 0

    # --- Generator function codegen ---
    in_generator_body: bool = False
    # Builder for the frame-local placement plan (wired by AsyncCoroCodegen;
    # `setup_resumable_frame_locals` calls it for bodies whose plan is not
    # yet cached -- the simple-peephole path, which never emits a frame
    # struct).
    frame_layout_builder: 'Callable[[TpyFunction], object] | None' = None
    generator_field_names: set[str] = field(default_factory=set)
    # write stmt -> frame-field slot name for rvalue writes into
    # pointer-form frame locals (seeded per body from the resumable
    # state's ptr_slot_map).
    resumable_ptr_slot_map: IdentityMap = field(default_factory=IdentityMap)
    # Hoisted locals that are compile-time aliases of a captured static-protocol
    # param (`xs = it`): they occupy no frame field of their own; every storage
    # access resolves to the backing param via `generator_storage_name`.
    generator_forwarded_locals: dict[str, str] = field(default_factory=dict)
    # Fields read through an outer wrap (`(*name)` deref) -- the union
    # of user locals stored as `tpy::frame_slot<T>` and synthetic for-
    # loop / async-with / async-for / try-finally fields still stored
    # as `std::optional<T>`. Read sites can use the same `(*name)`
    # access for both.
    generator_optional_fields: set[str] = field(default_factory=set)
    # Subset of generator_optional_fields whose C++ slot is the new
    # `tpy::frame_slot<T>` (user locals from `func.generator_locals`,
    # non-value, non-pointer-form). Writes here must go through
    # `.emplace(value)` -- the helper has no operator= for arbitrary T.
    generator_frame_slot_locals: set[str] = field(default_factory=set)
    # Subset of pointer_locals that are borrow-form for-loop vars (a `T*`
    # aliasing a live container element). Yielding one by name needs a deref
    # to the value yield slot -- but pointer-repr Optional/Union locals (also
    # in pointer_locals) must NOT be deref'd that way, hence a dedicated set.
    generator_borrow_form_loop_vars: set[str] = field(default_factory=set)
    # For-loops with yields in state machine generators: keyed by TpyForEach.
    # Values are GeneratorForInfo (not imported here to avoid circular dep)
    generator_for_loop_info: IdentityMap = field(default_factory=IdentityMap)
    # TpyWith -> per-item `__with_ctx_<n>` number for a non-decomposed region
    # whose owned manager was promoted to the frame to back an aliasing target.
    generator_with_owned_ctx: IdentityMap = field(default_factory=IdentityMap)
    # Statement-level borrow-alias frame locals (single-assign / tuple-unpack
    # targets aliasing existing storage). Seeded into pointer_locals by
    # setup_resumable_frame_locals so the frame field is a `T*` alias, not an
    # owning frame_slot<T>. Populated by _classify_pointer_alias_locals.
    generator_pointer_alias_locals: set[str] = field(default_factory=set)
    # Subset whose source is const: seeded into const_indirect_locals so the
    # field is `const T*` and reads stay const-correct.
    generator_const_pointer_alias_locals: set[str] = field(default_factory=set)
    # When generating a generator method's __next__() body, self -> __self
    generator_self_ref: str | None = None
    # Params whose str/bytes view was copied into OWNED storage on the way into
    # this generator/coroutine body (`is_owned_in_coro_frame` -- the resumable
    # frame's OWNED_COPY field, the simple-generator lambda's init-capture).
    # A read of one is owned storage even though the enclosing function's
    # SIGNATURE takes a view; the yield sink consults this so its view->owned
    # copy does not fire on an already-owned source.
    owned_view_frame_params: set[str] = field(default_factory=set)

    # --- Async coroutine function codegen ---
    # When generating an `async def` body inside its struct's poll() method,
    # `return v` is rewritten to `__state = <done>; return Poll<T>::ready(v);`.
    # The flag piggybacks on in_generator_body for the field-rewrite path
    # (locals -> this->field) -- both share the resumable-frame shape -- but
    # has its own return-rewrite handling (`_resumable_return_code`).
    in_async_coro_body: bool = False
    async_coro_return_cpp: str | None = None  # C++ return type for Poll<T>::ready
    async_coro_done_state: str | None = None  # name of the DONE state enumerator
    # Generator body lowered onto the resumable frame (yield -> __next__ ->
    # expected<T, StopIteration>). Distinct from in_generator_body, which is
    # the legacy goto-label generator codegen: there a bare `return` emits
    # `goto __done`, but the resumable while/switch has no such label, so a
    # bare `return` / fall-off-end lowers to `__state = S_DONE; return
    # make_unexpected(StopIteration{})` instead.
    in_generator_resumable_body: bool = False
    generator_resumable_done_state: str | None = None
    # Set when emitting a helper-based finally body for a generator on the
    # resumable frame. `return` here sets `this->__finally_stop = true` and
    # void-returns; callers check the flag and emit StopIteration.
    in_generator_finally_helper: bool = False
    # True iff the current generator's struct has a `__finally_stop` field
    # (i.e. at least one helper-based finally body contains a `return`).
    # Controls whether _emit_finally_helper_call appends the stop check.
    generator_has_finally_stop: bool = False
    # Pending-return routing inside a CFG-based finally region
    #: when set, `return v` inside the active case saves `v`
    # to the slot, sets the flag, walks finally frames up to
    # `async_pending_return_boundary` (the position in finally_stack
    # belonging to regions OUTSIDE the CFG-based finally -- they're
    # walked later by AsyncFinallyExit), then transitions state to
    # the finally entry. `slot` is None for void async defs.
    async_pending_return_flag: str | None = None
    async_pending_return_slot: str | None = None
    async_pending_return_target_state: str | None = None
    async_pending_return_boundary: int = 0

    # --- for/else, while/else label stack ---
    # When generating a loop with an else clause, the goto label name is
    # pushed here so TpyBreak codegen can emit `goto label` instead of `break`.
    loop_else_labels: list[str] = field(default_factory=list)

    # --- break-past-switch support ---
    # match lowering emits C++ `switch` blocks that would capture a user
    # `break;`. Each loop pushes "" here; when a break is emitted with
    # match_switch_depth > 0 (a switch sits between the break and the
    # innermost loop) the top entry is filled with a label name and the
    # loop emits `label:;` after its closing brace. Loops save/zero
    # match_switch_depth around their bodies; the switch-based match
    # emitters increment it around arm-body emission.
    loop_break_labels: list[str] = field(default_factory=list)
    match_switch_depth: int = 0

    # --- Cross-module import tracking ---
    user_module_imports: set[str] = field(default_factory=set)
    all_user_modules: set[str] = field(default_factory=set)
    implicit_stdlib_modules: set[str] = field(default_factory=set)
    macro_dep_modules: set[str] = field(default_factory=set)
    top_level_decls: dict[str, int] = field(default_factory=dict)

    # --- Native global variable imports ---
    # TODO: replace with dict[str, NativeGlobalInfo] holding c_name, linkage, var_type
    # instead of a flat name->name mapping (the TpyVarDecl nodes already carry this)
    native_global_names: dict[str, str] = field(default_factory=dict)

    def iter_imported_recursive_unions(self) -> 'Iterator[RecursiveUnionInfo]':
        """Yield each cross-module recursive alias once.

        Skips aliases whose origin is the current module. The compiler-wide
        index contains each alias under both its full member tuple and the
        non-None subset, so id-based de-dup is needed -- both keys point
        at the same RecursiveUnionInfo instance produced by `_register`.
        """
        compiler = get_current_compiler()
        if compiler is None:
            return
        seen: set[int] = set()
        for info in compiler.union_wrapper_index.values():
            if info.origin == self.module_name:
                continue
            if id(info) in seen:
                continue
            seen.add(id(info))
            yield info

    def is_plain_nonvalue(self, t: 'TpyType') -> bool:
        """True for non-value types that need indirection (list, dict,
        record, recursive-union wrapper). Unwraps Own[T] and excludes
        pointer-repr Optional and ptr-variant Union, which have their own
        codegen paths. Shared by the branch pre-decl form choice and the
        walrus borrow arm so their eligibility guards cannot drift."""
        return _forms_is_plain_nonvalue(t)

    def is_ptr_variant_union(self, typ: 'TpyType') -> bool:
        """Check if a union type uses pointer-variant representation.

        Returns True for non-value unions (e.g. Dog | Cat with records)
        that are NOT recursive union aliases. Recursive unions use wrapper
        structs which are value types, so they skip pointer-variant form.
        """
        return _forms_is_ptr_variant_union(typ)

    def is_ptr_variant_source(self, expr: TpyExpr) -> bool:
        """Check if an expression produces a pointer variant (vs value variant).

        Pointer-variant sources: ptr_variant locals, union params, function
        calls returning non-value unions. Value-variant sources: constructors,
        Own returns, field access, container subscript, bare alternative values.

        Note: only `ReadonlyType` is unwrapped on params. `OwnType[A | B]`
        params lower to a value-variant `std::variant<A, B>&&` (the caller
        gave up ownership), so they should NOT take the ptr-variant path.
        """
        if isinstance(expr, TpyCoerce):
            return self.is_ptr_variant_source(expr.expr)
        if isinstance(expr, TpyName):
            if expr.name in self.ptr_variant_locals:
                return True
            # Fallback to the param table for any context that set
            # current_func_params without populating ptr_variant_locals -- so a
            # union-param source resolves even outside a seeded scope.
            ptype = self.current_func_params.get(expr.name)
            if ptype is not None and self.is_ptr_variant_union(unwrap_readonly(ptype)):
                return True
            return False
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            fi = expr.resolved_function_info
            if fi is not None and self.is_ptr_variant_union(fi.return_type):
                return True
        # A ptr-variant union ternary: the ternary emit normalizes each arm to
        # variant<A*, B*>, so the ternary itself yields the pointer variant and
        # must not be re-wrapped by the consuming sink.
        if isinstance(expr, TpyIfExpr):
            rt = self.get_expr_type(expr)
            return rt is not None and self.is_ptr_variant_union(unwrap_readonly(rt))
        return False

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
        self.storage_form_tuple_locals = set()
        self.const_storage_form_tuple_locals = set()
        self.own_borrow_tuple_locals = set()
        self.borrow_form_tuple_locals = set()
        self.const_borrow_form_tuple_locals = set()
        self.optional_borrow_tuple_locals = set()
        self.const_optional_borrow_tuple_locals = set()
        self.storage_form_optional_locals = set()
        self.const_storage_form_optional_locals = set()
        self.slots.reset()
        self.rebind_slots = {}
        self.plain_rebind_slots = set()
        self.deferred_rebind_slot_decls = {}
        self.rebind_slot_scopes = {}
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
        self.frame_field_shadows = set()
        self.pending_hoist_decls = []
        self.current_ns = None
        self.indent_level = 0
        self.current_return_type = None
        self.current_return_cpp = None
        self.current_return_const = False
        self.current_yield_type = None
        self.current_error_return = None
        self.error_return_stmt_handled = False
        self.current_func_params = {}
        self.current_type_param_bounds = {}
        self.in_method = False
        self.narrowed_vars = {}
        # All persistent cast-and-cache aliases declared in the current C++
        # scope. narrowed_vars holds only the *most recent* alias per source
        # variable, so it loses earlier aliases after a bump (`__p` -> `__p_2`
        # rewrites narrowed_vars[p] and the `__p` declaration becomes
        # untracked even though it's still live). This set retains every
        # emitted alias name in the current scope so `_fresh_alias_local`'s
        # collision check spans the full history.
        self.declared_persistent_aliases: set[str] = set()
        self.protocol_narrowings = {}
        self.assign_narrowed_types = {}
        self.literal_facts = {}
        self.walrus_pre_declared = set()
        self.persistent_rebind_slots = {}
        self.walrus_deref_rewrites = {}
        self.walrus_storage_tuple_locals = set()
        self.walrus_pointer_locals = set()
        self.walrus_const_pointer_locals = set()
        self.overload_terminated = False
        # The `if` node whose folded-True terminating branch set
        # overload_terminated. The function-body emit truncates the rest of a
        # list only when the just-emitted top-level stmt IS this node; a flag
        # set by a fold nested in a non-terminating compound (loop/branch) has
        # a different node, so the reachable post-compound code still emits.
        self.overload_terminated_node = None
        # Note: overload_param_types and literal_overload_facts are NOT reset
        # here -- they're managed by the caller (set before gen_body, cleared
        # in a finally block).
        self.match_counter = 0
        self.iter_counter = 0
        self.unpack_counter = 0
        self.loop_else_labels = []
        self.loop_break_labels = []
        self.match_switch_depth = 0
        self.finally_stack = []

    def indent(self) -> str:
        """Get current indentation string."""
        return INDENT * self.indent_level

    def declare_rebind_slot(self, name: str, slot: str, slot_cpp: str) -> None:
        """Reserve `name`'s rebind slot, holding its declaration back.

        The single registration point for a *pre-declared* slot (one reserved at
        a declaration site for a rebind that may never come). Emitting the
        declaration here instead is what left dead `std::optional<T>` locals
        behind: the slot number must be reserved now (a later rebind must not
        emplace over an init value an alias may hold), but whether any rebind
        follows is only known once the body is walked. `use_rebind_slot`
        materializes the declaration if and when a rebind consumes the slot.

        A slot allocated AT a rebind site is consumed by construction -- those
        register through `rebind_slots` directly.
        """
        static_kw = "static " if self.slots.global_scope else ""
        self.rebind_slots[name] = slot
        # The owning scope outlives the declaration: the decl drains on the FIRST
        # consume, but a later rebind from another scope is the same hazard.
        self.rebind_slot_scopes[slot] = self.hoist_scope_id
        self.deferred_rebind_slot_decls[slot] = f"{static_kw}{slot_cpp} {slot};\n"

    def use_rebind_slot(self, name: str, loc=None) -> str | None:
        """The rebind slot for `name`, emitting its held-back declaration.

        The declaration drains into the hoist scope that RESERVED the slot. A
        rebind reached from a different scope -- a lambda-rendered body
        consuming a slot reserved outside it -- has no sound placement: inside
        the lambda the slot dies each invocation while the pointer aliasing it
        is captured and outlives it, and outside it the lambda cannot name it.
        Reject rather than emit either.
        """
        slot = self.rebind_slots.get(name)
        if slot is None:
            return None
        owner = self.rebind_slot_scopes.get(slot)
        if owner is not None and owner != self.hoist_scope_id:
            # Local import: emit_prims imports this module at module level, so
            # only a deferred import keeps the module-level edge acyclic.
            from . import emit_prims
            emit_prims.reject_rebind_slot_crosses_scope(name, loc)
        decl = self.deferred_rebind_slot_decls.pop(slot, None)
        if decl is not None:
            self.pending_hoist_decls.append(decl)
        return slot

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
            storage_form_tuple_locals=self.storage_form_tuple_locals.copy(),
            const_storage_form_tuple_locals=self.const_storage_form_tuple_locals.copy(),
            own_borrow_tuple_locals=self.own_borrow_tuple_locals.copy(),
            storage_form_optional_locals=self.storage_form_optional_locals.copy(),
            const_storage_form_optional_locals=self.const_storage_form_optional_locals.copy(),
            movable_locals=self.movable_locals.copy(),
            ref_bound_locals=self.ref_bound_locals.copy(),
            rebind_slots=dict(self.rebind_slots),
            plain_rebind_slots=self.plain_rebind_slots.copy(),
            assign_narrowed_types=dict(self.assign_narrowed_types),
            narrowed_vars=dict(self.narrowed_vars),
            declared_persistent_aliases=self.declared_persistent_aliases.copy(),
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
        self.storage_form_tuple_locals = snap.storage_form_tuple_locals.copy()
        self.const_storage_form_tuple_locals = snap.const_storage_form_tuple_locals.copy()
        self.own_borrow_tuple_locals = snap.own_borrow_tuple_locals.copy()
        self.storage_form_optional_locals = snap.storage_form_optional_locals.copy()
        self.const_storage_form_optional_locals = snap.const_storage_form_optional_locals.copy()
        self.movable_locals = snap.movable_locals.copy()
        self.ref_bound_locals = snap.ref_bound_locals.copy()
        self.rebind_slots = dict(snap.rebind_slots)
        self.plain_rebind_slots = snap.plain_rebind_slots.copy()
        self.assign_narrowed_types = dict(snap.assign_narrowed_types)
        self.narrowed_vars = dict(snap.narrowed_vars)
        # Walrus optional-slot rewrites outlive C++ block scopes (the decl is
        # function-scope); re-apply over the snapshot so sibling branches
        # don't read the bare optional, and keep the storage-form
        # classification of walrus storage-tuple locals alive with them.
        self.narrowed_vars.update(self.walrus_deref_rewrites)
        self.storage_form_tuple_locals.update(self.walrus_storage_tuple_locals)
        self.pointer_locals.update(self.walrus_pointer_locals)
        self.declared_vars.update(self.walrus_pointer_locals)
        self.local_scope_names.update(self.walrus_pointer_locals)
        self.const_indirect_locals.update(self.walrus_const_pointer_locals)
        self.rebind_slots.update(self.persistent_rebind_slots)
        self.declared_persistent_aliases = snap.declared_persistent_aliases.copy()

    @contextmanager
    def nested_hoist_scope(self) -> 'Iterator[list[str]]':
        """Collect a lambda-rendered body's held-back declarations separately.

        `pending_hoist_decls` is drained at the enclosing FUNCTION prologue,
        which a lambda body cannot reach: the declaration would either never be
        written (an emitter that does not drain) or land outside the lambda's
        explicit capture list. A body rendered into a lambda therefore collects
        its own and drains them at its own prologue -- the caller buffers the
        body and writes the yielded list ahead of it.
        """
        saved = self.pending_hoist_decls
        saved_id = self.hoist_scope_id
        self.pending_hoist_decls = []
        self._hoist_scope_counter += 1
        self.hoist_scope_id = self._hoist_scope_counter
        try:
            yield self.pending_hoist_decls
        finally:
            self.pending_hoist_decls = saved
            self.hoist_scope_id = saved_id

    @contextmanager
    def nested_def_emission_scope(self, return_type: TpyType | None,
                                  return_cpp: str | None,
                                  error_return_cpp: str | None) -> 'Iterator[None]':
        """Isolate per-function emission context across a nested-def body.

        A nested def emits as a C++ lambda inside the enclosing function's
        body, but it is its own function: a `return` inside it must not walk
        the OUTER finally_stack (inlining the outer finally into the lambda),
        an @error_return call must not `goto` the enclosing function's
        except label, and async/generator frame emission modes must not
        apply to the lambda body. Save/clear everything reset_scope
        initializes per function except local-scope state (the caller
        handles that via snapshot_local_scope) and name counters (which
        must keep incrementing across the boundary).
        """
        saved = (
            self.finally_stack,
            self.try_except_label,
            self.try_except_err_opt,
            self.in_except_tier,
            self.loop_else_labels,
            self.loop_break_labels,
            self.match_switch_depth,
            self.current_return_type,
            self.current_return_cpp,
            self.current_return_const,
            self.current_error_return,
            self.error_return_stmt_handled,
            self.in_async_coro_body,
            self.in_generator_resumable_body,
            self.in_generator_finally_helper,
            self.async_coro_return_cpp,
            self.async_coro_done_state,
            self.generator_resumable_done_state,
            self.current_yield_type,
            self.async_pending_return_flag,
            self.async_pending_return_slot,
            self.async_pending_return_target_state,
            self.async_pending_return_boundary,
            self.in_property_getter,
            self.in_consuming_method,
        )
        self.finally_stack = []
        self.try_except_label = None
        self.try_except_err_opt = None
        self.in_except_tier = None
        self.loop_else_labels = []
        self.loop_break_labels = []
        self.match_switch_depth = 0
        self.current_return_type = return_type
        self.current_return_cpp = return_cpp
        self.current_return_const = False
        self.current_error_return = error_return_cpp
        self.error_return_stmt_handled = False
        self.in_async_coro_body = False
        self.in_generator_resumable_body = False
        self.in_generator_finally_helper = False
        self.async_coro_return_cpp = None
        self.async_coro_done_state = None
        self.generator_resumable_done_state = None
        self.current_yield_type = None
        self.async_pending_return_flag = None
        self.async_pending_return_slot = None
        self.async_pending_return_target_state = None
        self.async_pending_return_boundary = 0
        self.in_property_getter = False
        self.in_consuming_method = False
        try:
            yield
        finally:
            (self.finally_stack,
             self.try_except_label,
             self.try_except_err_opt,
             self.in_except_tier,
             self.loop_else_labels,
             self.loop_break_labels,
             self.match_switch_depth,
             self.current_return_type,
             self.current_return_cpp,
             self.current_return_const,
             self.current_error_return,
             self.error_return_stmt_handled,
             self.in_async_coro_body,
             self.in_generator_resumable_body,
             self.in_generator_finally_helper,
             self.async_coro_return_cpp,
             self.async_coro_done_state,
             self.generator_resumable_done_state,
             self.current_yield_type,
             self.async_pending_return_flag,
             self.async_pending_return_slot,
             self.async_pending_return_target_state,
             self.async_pending_return_boundary,
             self.in_property_getter,
             self.in_consuming_method) = saved

    def register_walrus_deref(self, name: str, deref: str) -> None:
        """Install the `(*slot)` read rewrite for a walrus optional-slot local
        in both the live map and the restore-surviving registry."""
        self.narrowed_vars[name] = deref
        self.walrus_deref_rewrites[name] = deref

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
        """Emit the original Python source line(s) as a comment if enabled.

        Walks subsequent lines until the logical line ends -- detected as the
        first `NEWLINE` token at bracket-depth zero. Lets multi-line
        signatures (`def f(\\n  a: T,\\n) -> T:`) and other paren-continued
        expressions render their full source instead of just the opening line.
        """
        if not self.options.emit_source_comments:
            return
        if loc is None:
            return
        start_idx = loc.line - 1
        if not (0 <= start_idx < len(self.source_lines)):
            return
        end_idx = start_idx
        # Tokenize from this line forward to find the logical-line boundary.
        # 32-line cap is a safety bound; real signatures fit easily. Track
        # paren depth via OP tokens so the boundary is the first NEWLINE/NL
        # at depth 0 -- `NEWLINE` ends a statement; `NL` at depth 0 ends a
        # comment-only or blank line (and we shouldn't pull the next stmt's
        # source into this comment block).
        snippet = "".join(
            line if line.endswith("\n") else line + "\n"
            for line in self.source_lines[start_idx:start_idx + 32]
        )
        try:
            import io
            import tokenize
            depth = 0
            for tok in tokenize.generate_tokens(io.StringIO(snippet).readline):
                if tok.type == tokenize.OP:
                    if tok.string in "([{":
                        depth += 1
                    elif tok.string in ")]}":
                        depth -= 1
                elif tok.type in (tokenize.NEWLINE, tokenize.NL) and depth <= 0:
                    end_idx = start_idx + tok.start[0] - 1
                    break
        except (tokenize.TokenError, IndentationError):
            pass  # Fall back: just the start line.
        end_idx = min(end_idx, len(self.source_lines) - 1)
        for i in range(start_idx, end_idx + 1):
            self._write_source_comment(out, i + 1, self.source_lines[i].rstrip(), indent)

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
        globals are bound on the analyzer's `global_ns`. `self` inside a
        method body resolves to the enclosing record's type via
        `current_method_record_type`. Returns None when the name isn't
        found anywhere.
        """
        typ = self.var_types.get(var_name)
        if typ is not None:
            return typ
        if var_name == "self" and self.self_renders_as_this():
            return self.current_method_record_type
        global_ns = self.analyzer.ctx.global_ns
        if global_ns is not None:
            binding = global_ns.lookup(var_name)
            if binding is not None and binding.type is not None:
                return binding.type
        return None

    def self_renders_as_this(self) -> bool:
        """True when a bare `self` in the current emission scope is the
        method receiver (rendered through C++ `this`), not an ordinary
        local/param that happens to be named self. Staticmethods set
        in_method but have no receiver (current_method_record_type stays
        None), and a free function may declare its own `self` param."""
        return (self.in_method
                and "self" not in self.current_func_params
                and self.current_method_record_type is not None)

    def self_captures_this(self) -> bool:
        """True when a lambda / nested-def capture list must spell a
        captured `self` as `this`. Covers every receiver render form: the
        plain method body (`this`), the simple-generator wrapper lambda
        (self renders `(*this)` through the wrapper's captured this), and
        the resumable frame member (`__self` is a frame field reached
        through the member's own this)."""
        return (self.generator_self_ref == "(*this)"
                or self.self_renders_as_this())

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

    def is_storage_form_optional_source(self, expr: TpyExpr) -> bool:
        """True when expr renders as a storage-form Optional[T] lvalue
        (`std::optional<T>&` for a non-value T) -- i.e. needs the same
        downstream handling at consumer sites as a record's storage-form
        Optional field: `optional_to_ptr` at borrow sites, double-deref
        at narrowing sites, `ptr_to_optional` at assignments.

        Covers three source shapes that produce the same C++ shape:

          * TpyFieldAccess on a storage-form Optional[T] field of a
            record (`obj.maybe_p` where the field is stored as
            `std::optional<T>`).
          * TpySubscript yielding a pointer-repr Optional element of a
            storage-form container (`pairs[i]` where
            `pairs: list[P|None]`, `d[k]` where `d: dict[K, P|None]`).
            `__getitem__` returns `std::optional<P>` (storage form).
          * TpyName whose `local_cpp_form` is `STORAGE_OPTIONAL` -- a
            for-loop variable iterating such a container, or a
            comprehension/genexpr unpack variable bound from a
            storage-form-tuple slot whose element type is pointer-repr
            Optional. Routed through the classifier rather than direct
            set lookup so all name-keyed shape dispatch goes through one
            place; the backing set is `storage_form_optional_locals`.

        Note: `optional_locals` names (`Own[OptionalType[P_ref]]` params
        rendered as `std::optional<P>&&`) are NOT included. They share
        the storage-form C++ shape but are also in `pointer_locals`,
        and the downstream field-access dispatch already routes via
        `optional<T>::operator->`. The consumer sites that DO need a
        `optional_to_ptr` lift for these names (call-arg, return into
        pointer-form, pointer-local rebind) check `optional_locals`
        directly.
        """
        if isinstance(expr, (TpyFieldAccess, TpySubscript)):
            # Analyzer-pure field/subscript core, shared with THIR lowering.
            return _forms_reads_storage_form_optional(self.analyzer, expr)
        if isinstance(expr, TpyName):
            if self.local_cpp_form(expr.name) is LocalCppForm.STORAGE_OPTIONAL:
                return True
        return False

    def _is_pointer_global(self, expr: TpyExpr) -> bool:
        """Check if expression is a reference to a non-value-type global (T*)."""
        if not isinstance(expr, TpyName):
            return False
        return expr.name in self.pointer_globals and self.is_global_name(expr)

    def renders_own_borrow_tuple(self, expr: TpyExpr) -> bool:
        """True when `expr` yields the MIXED borrow render of a per-element-Own
        tuple -- `std::tuple<A, B*>`, owned elements by value and plain ref
        elements as pointers.

        Only a per-element-Own RETURN produces that shape, so it survives in
        the call result itself and in a local bound straight from one. Every
        storage sink runs the value through `tuple_to_storage` first, which
        materializes the ref element as `B&` (container slot) or `B` (nested
        tuple / dict value) -- so a container element, field or loop variable
        is NOT this, even though its tuple type still carries the `Own`.

        Distinct from `is_storage_form_source`, which stays True for these:
        the owned element really is held by value, so the whole-tuple
        `tuple_to_pointer` lift is still owed. This answers the narrower
        per-element question those consumers must not read off that verdict.
        """
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            fi = expr.resolved_function_info
            rt = unwrap_readonly(fi.return_type) if fi is not None else None
            return isinstance(rt, TupleType) and rt.is_mixed_own()
        # Composition mirrors `is_storage_form_source`: C++ evaluates one arm,
        # so a ternary is this shape only when BOTH arms are (mixed arms have
        # no common tuple type to deduce anyway).
        if isinstance(expr, TpyIfExpr):
            return (self.renders_own_borrow_tuple(expr.then_expr)
                    and self.renders_own_borrow_tuple(expr.else_expr))
        if isinstance(expr, TpyName):
            return expr.name in self.own_borrow_tuple_locals
        return False

    def tuple_elem_renders_pointer(self, obj: TpyExpr, elem: TpyType) -> bool:
        """Whether element `elem` of the tuple expression `obj` is ALREADY a
        bare pointer in this source's render, so a consumer wanting a borrow
        must not lift it again.

        The question is per element and about the SOURCE: a borrow-form tuple
        renders every pointer-repr element as `T*`, and a MIXED render does too
        for its borrowed half even though `is_storage_form_source` calls the
        tuple storage (that verdict describes only its owned half). Keying on
        the tuple TYPE instead would misjudge every container-stored mixed
        tuple, which really has been through `tuple_to_storage`.
        """
        return (TupleType._element_is_pointer_repr(elem)
                and (self.renders_own_borrow_tuple(obj)
                     or not self.is_storage_form_source(obj)))

    def needs_tuple_storage_lift(self, expr: TpyExpr) -> bool:
        """Whether an OWNING tuple slot must run `expr` through
        `tuple_to_storage` rather than take its value as-is.

        A borrow-form source obviously must. A MIXED-render source must too,
        even though `is_storage_form_source` calls it storage: that verdict
        describes only its owned half, while its borrowed half is a pointer the
        owning slot has to materialize. Asking `is_storage_form_source` alone
        here is what let every storage sink take the mixed render unconverted.
        """
        return (not self.is_storage_form_source(expr)
                or self.renders_own_borrow_tuple(expr))

    def is_storage_form_source(self, expr: TpyExpr) -> bool:
        """True when `expr` reads a value from a storage location.

        Storage locations are fields, container subscripts, globals,
        locals flagged as storage-form (loop vars iterating storage
        containers, locals initialized from another storage-form source),
        and function calls whose return type is `Own[tuple[T_ref,...]]`
        (the storage form is the function's return ABI). Used by
        tuple-of-pointer-Optional callers to decide whether to emit an
        element-wise tuple_to_pointer wrap; the value's actual type /
        shape is the caller's responsibility to validate.

        Its any-`Own` branch is scoped to that elementwise-conversion reading:
        it says the OWNED half is storage, not that every element is. A
        per-element consumer must ask `tuple_elem_renders_pointer` instead --
        keying on this verdict is what made a mixed render's borrowed element
        get lifted a second time.
        """
        if isinstance(expr, (TpyFieldAccess, TpySubscript)):
            return True
        # A ternary of two storage reads is itself a storage lvalue (C++
        # evaluates one branch); mixed-form arms don't compose into one C++
        # conditional and are not claimed here.
        if isinstance(expr, TpyIfExpr):
            return (self.is_storage_form_source(expr.then_expr)
                    and self.is_storage_form_source(expr.else_expr))
        if isinstance(expr, TpyName):
            if expr.name in self.storage_form_tuple_locals:
                return True
            if self.is_global_name(expr):
                return True
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            fi = expr.resolved_function_info
            if fi is not None:
                rt = unwrap_readonly(fi.return_type)
                # Two shapes both produce a storage-form tuple return:
                #   * Own[tuple[T,...]] (pre-lowering form, may persist in
                #     some call paths).
                #   * tuple[Own[T_ref], T_value, ...] (post-own_tuple_target
                #     synthesis -- the per-element Own pushes storage form
                #     into each ref-typed slot).
                if isinstance(rt, OwnType) and isinstance(rt.wrapped, TupleType):
                    return True
                if isinstance(rt, TupleType) and any(
                        isinstance(et, OwnType) for et in rt.element_types):
                    return True
        return False

    def is_const_union_source(self, expr: TpyExpr) -> bool:
        """True when `expr` is an lvalue rooted in a const source -- a field /
        container element off a const-ref param or const-indirect local,
        recursing through chained field/subscript access to the base name.
        The general "rooted in a const source" predicate: consulted by the
        value-variant lift (needs `to_const_ptr_variant`), the storage-optional
        lift, the REF_ALIAS borrow-local arm, the `.get()` accessor local, and
        `is_const_storage_source` (the tuple sinks)."""
        if isinstance(expr, TpyCoerce):
            return self.is_const_union_source(expr.expr)
        if isinstance(expr, (TpyFieldAccess, TpySubscript)):
            obj = expr.obj
            if isinstance(obj, TpyName):
                return (obj.name in self.const_ref_params
                        or obj.name in self.const_indirect_locals)
            # Chained access (outer.inner.pet, self.store[k]): recurse on
            # the object
            return self.is_const_union_source(obj)
        return False

    def is_const_storage_source(self, expr: TpyExpr) -> bool:
        """True when `expr` reads from a const-bound storage location, so
        element addresses derived from it come out `const T*`: an lvalue chain
        (field / subscript, arbitrarily deep) rooted at a const receiver
        (self in a readonly method, const param/local), or a name bound const
        (const-storage loop var, const-inferred param/local).
        """
        if self.is_const_union_source(expr):
            return True
        if isinstance(expr, TpyName):
            return (expr.name in self.const_storage_form_tuple_locals
                    or expr.name in self.const_borrow_form_tuple_locals
                    or expr.name in self.const_optional_borrow_tuple_locals
                    or expr.name in self.const_ref_params
                    or expr.name in self.const_indirect_locals)
        return False

    def convert(self, val: 'FormValue', *, dst_type: TpyType, dst_form: CppForm) -> str:
        """The single door for borrow<->storage form conversion.

        Bridges `val.code` from `val.form` to `dst_form`, selecting the runtime
        helper from the TPy type kind (+ const, + move). It NEVER re-derives
        `val.form` from `val.code`/`val.type` -- the producing emitter records
        the form it actually emitted (see `FormValue`), because one TPy type can
        render in multiple C++ shapes.

        Optional and Union are handled here; an unhandled kind raises so a caller
        can never get a silent partial conversion.
        """
        if val.form is CppForm.VALUE or dst_form is CppForm.VALUE or val.form is dst_form:
            return val.code
        t = unwrap_qualifiers(val.type)
        if isinstance(t, OptionalType):
            if dst_form is CppForm.BORROW:
                # storage optional<T> -> T* (const overload auto-selected by
                # the optional's own const-ness).
                return f"::tpy::optional_to_ptr({val.code})"
            helper = "ptr_to_optional_move" if val.move else "ptr_to_optional"
            return f"::tpy::{helper}({val.code})"
        if isinstance(t, UnionType):
            if dst_form is CppForm.BORROW:
                # value variant<A, B> -> pointer variant<A*, B*>.
                helper = "to_const_ptr_variant" if val.is_const else "to_ptr_variant"
                return f"::tpy::{helper}({val.code})"
            # pointer variant<A*, B*> -> value variant<A, B> (target spelled;
            # to_cpp() self-resolves a union alias).
            return f"::tpy::to_value_variant<{t.to_cpp()}>({val.code})"
        if isinstance(t, TupleType):
            # The runtime helper absorbs the per-element pointer/optional/value
            # mask from the destination tuple type, so the only thing to spell
            # is that destination. `val.type` must be pending-resolved by the
            # caller (TypeResolver.resolve_tuple_pending) -- to_cpp*/to_cpp_return
            # do not resolve, matching tuple_storage_cpp / tuple_borrow_cpp.
            if dst_form is CppForm.BORROW:
                borrow_cpp = t.to_cpp_return_const() if val.is_const else t.to_cpp_return()
                return f"::tpy::tuple_to_pointer<{borrow_cpp}>({val.code})"
            helper = "tuple_to_storage_move" if val.move else "tuple_to_storage"
            return f"::tpy::{helper}<{t.to_cpp()}>({val.code})"
        # Plain reference type (record / container): the borrow form is a
        # pointer to the storage lvalue. Only storage->borrow is bridged;
        # the reverse is a copy -- an ownership decision that belongs to
        # the producing site, never to a form conversion.
        if (isinstance(t, NominalType) and not t.is_value_type()
                and dst_form is CppForm.BORROW):
            return f"&({val.code})"
        raise NotImplementedError(
            f"convert(): {type(t).__name__} bridging not yet routed through the "
            f"chokepoint (val.form={val.form}, dst_form={dst_form})")

    def callee_returns_own_ptr_optional(self, init: 'TpyExpr') -> bool:
        """True when `init` is a function call returning
        `Own[OptionalType[T_ref]]` (storage form). Codegen sites bridging
        the function's `std::optional<T>` return into a pointer-form local
        check this to know they need the `optional_to_ptr` lift.
        """
        if not isinstance(init, (TpyCall, TpyMethodCall)):
            return False
        fi = init.resolved_function_info
        if fi is None:
            return False
        return is_own_pointer_repr_optional(fi.return_type)

    def is_own_ptr_variant_param(self, name: str) -> bool:
        """True when `name` is a function parameter typed
        `Own[UnionType non-value]` (pointer-variant Union, storage form
        at the param ABI). Mirror of `optional_locals` for unions;
        consumers needing the `to_ptr_variant` lift check this.
        """
        ptype = self.current_func_params.get(name)
        if ptype is None:
            return False
        peeled = unwrap_readonly(ptype)
        return (isinstance(peeled, OwnType)
                and isinstance(peeled.wrapped, UnionType)
                and self.is_ptr_variant_union(peeled.wrapped))

    def local_cpp_form(self, name: str) -> LocalCppForm:
        """Classify a name's C++ representation -- see `LocalCppForm`.

        Priority order matters: `optional_locals` is a subset of
        `pointer_locals` (Own[Opt[T_ref]] params live in both for arrow
        access vs. lift dispatch), so OPTIONAL_STORAGE must take
        precedence over POINTER. STORAGE_OPTIONAL tracks iteration-source
        loop/unpack vars; its population is producer-disjoint from
        `optional_locals` today, so the priority slot is conventional, not
        load-bearing -- a same-name shadow between a param and a loop var
        would still resolve consistently because both forms lift the same
        way via `optional_to_ptr`.
        VALUE_VARIANT (Own[Union] param) is looked up via
        `current_func_params`, not a set; it precedes PTR_VARIANT
        because a single name cannot match both.

        For arrow-vs-deref field access use `is_pointer_local(expr)`
        instead -- it covers both POINTER and OPTIONAL_STORAGE.
        """
        if name in self.optional_locals:
            return LocalCppForm.OPTIONAL_STORAGE
        if name in self.storage_form_optional_locals:
            return LocalCppForm.STORAGE_OPTIONAL
        if self.is_own_ptr_variant_param(name):
            return LocalCppForm.VALUE_VARIANT
        if name in self.ptr_variant_locals:
            return LocalCppForm.PTR_VARIANT
        if name in self.storage_form_tuple_locals:
            return LocalCppForm.STORAGE_TUPLE
        if name in self.borrow_form_tuple_locals:
            return LocalCppForm.BORROW_TUPLE
        if name in self.optional_borrow_tuple_locals:
            return LocalCppForm.OPTIONAL_BORROW_TUPLE
        if name in self.pointer_locals:
            return LocalCppForm.POINTER
        return LocalCppForm.VALUE

    def iteration_yields_const(self, iterable: TpyExpr) -> bool:
        """True when iterating `iterable` binds the loop var as const.

        Detects const-source iteration patterns where the resulting tuple
        elements come out as `const optional<T>&` -- so the storage->pointer
        wrap must use `to_cpp_return_const()` to match optional_to_ptr's
        `const T*` output.
        """
        # field-of-self in a readonly method: self is const, field is const ref
        if isinstance(iterable, TpyFieldAccess) and isinstance(iterable.obj, TpyName):
            if iterable.obj.name == "self" and "self" in self.const_ref_params:
                return True
        # const-inferred param or alias of one
        if isinstance(iterable, TpyName):
            return (iterable.name in self.const_indirect_locals
                    or iterable.name in self.const_ref_params)
        # Borrowing-view accessor call (d.items() / d.values()): the view's
        # const-ness tracks the receiver's. Sema may have selected the
        # mutable clone (receiver const-ness inferred only in Phase 2), so
        # re-derive from the receiver at emit time.
        if isinstance(iterable, TpyMethodCall) and iterable.obj is not None:
            fi = iterable.resolved_function_info
            if (fi is not None
                    and is_borrowing_view_type(unwrap_ref_type(fi.return_type))):
                return self.iteration_yields_const(iterable.obj)
        return False

    def register_frame_field_shadow(self, name: str) -> bool:
        """Mark `name` as a C++-local shadow of a frame field for the
        rest of the current scope. Body-emit consults `frame_field_shadows`
        to suppress the `(*name)` peel that would otherwise apply to a
        `generator_optional_fields` member. No-op outside a resumable
        body or if `name` is already shadowed.

        Returns True iff this call added the entry. Callers whose
        C++-local binding is block-scoped (e.g. for-loop iter var) use
        the return value to gate a paired discard at scope exit; callers
        whose binding outlives its source statement (with-as, tuple-
        unpack -- Python scoping) ignore the return and rely on
        `reset_scope` to clear the set at function exit.
        """
        if not self.in_generator_body or name in self.frame_field_shadows:
            return False
        self.frame_field_shadows.add(name)
        return True

    def needs_optional_to_ptr_lift(self, name: str) -> bool:
        """True when `name` is in `OPTIONAL_STORAGE` form -- an
        `Own[Opt[T_ref]]` param whose C++ shape is `std::optional<T>`
        and must be lifted via `tpy::optional_to_ptr` to feed a `T*`
        slot.

        For call-expression sources (function returning `Own[Opt[T_ref]]`),
        use `callee_returns_own_ptr_optional` instead -- those paths
        also allocate a slot to hold the rvalue and aren't a pure lift.
        """
        return self.local_cpp_form(name) is LocalCppForm.OPTIONAL_STORAGE

    def needs_to_ptr_variant_lift(self, name: str) -> bool:
        """True when `name` is in `VALUE_VARIANT` form -- an
        `Own[Union nonvalue]` param whose C++ shape is `std::variant<A, B>`
        and must be lifted via `tpy::to_ptr_variant` to feed a
        `::tpy::Union<A*, B*>` slot.
        """
        return self.local_cpp_form(name) is LocalCppForm.VALUE_VARIANT

    def polymorphic_cast_arg(self, var_name: str, var_decl: 'TpyType | None') -> str:
        """C++ expression to use as the input to dynamic_cast<Sub*>(...)
        when narrowing the polymorphic source `var_name`.

        Polymorphic isinstance/dynamic_cast needs a pointer input; the right
        prefix depends on the variable's C++ binding shape:
          - `self` inside a generator/async body -> `&{generator_self_ref}`
            (the body sees self via `__self: T&` for async/multi-yield or
            `(*this)` for simple generators -- both yield `T*` when address-
            of'd; the `&(*this)` form is folded by the optimizer)
          - `self` in a regular method -> `this` (already a pointer)
          - pointer-globals, pointer-locals, imported pointers
            (`is_indirect_name`) -> the bare name (already a pointer)
          - declared `Optional[Polymorphic]` (pointer-repr) param -> the bare
            name (the C++ binding is `T*`)
          - bare polymorphic param (`T&`) or anything else not above
            -> `&name` (take the address)
        """
        name_expr = TpyName(var_name)
        if var_name == "self" and self.generator_self_ref is not None:
            # In simple-generator bodies generator_self_ref is `(*this)`;
            # &(*this) == this. In async/multi-yield bodies it's `__self`
            # (a T& field on the frame struct); &__self gives T*.
            if self.generator_self_ref == "(*this)":
                return "this"
            return f"&{self.generator_self_ref}"
        if var_name == "self" and self.is_indirect_name(name_expr):
            return "this"
        escaped = escape_cpp_name(var_name)
        if self.is_indirect_name(name_expr):
            return escaped
        if polymorphic_source_is_pointer(var_decl):
            return escaped
        return f"&{escaped}"

    def deref_dispatch_inner(self, var_decl: 'TpyType | None', depth: int) -> 'TpyType | None':
        """Peel `depth` __deref__ steps off `var_decl` to recover the
        dispatch inner (the @dynamic protocol / polymorphic class behind an
        owning wrapper), so codegen can pick dynamic_cast vs dyn_adapter_cast
        for a deref-view narrowing."""
        cur = unwrap_readonly(var_decl) if var_decl is not None else None
        type_ops = self.analyzer.type_ops
        for _ in range(depth):
            if cur is None:
                return None
            cur = type_ops.get_deref_target_type(cur)
            if cur is not None:
                cur = unwrap_readonly(cur)
        return cur

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
                and self.self_renders_as_this()
                and self.generator_self_ref is None):
            return True
        if self._is_pointer_global(expr) or self.is_pointer_local(expr):
            return True
        if isinstance(expr, TpyName):
            imp = lookup_imported(
                self.analyzer.ctx.module_attributes,
                expr.name, SymbolKind.VARIABLE)
            if imp is not None:
                source_module, original_name = imp
                module_info = self.analyzer.registry.get_module(source_module)
                if module_info and original_name in module_info.variables:
                    return module_info.variables[original_name].is_pointer
        return False

    def generator_storage_name(self, name: str) -> str:
        """Resolve a frame-resident name to its backing storage name.

        A forwarded proto-param alias (`xs = it`) has no field of its own, so
        every storage access resolves to the captured param it aliases. Plain
        names (and uses outside a generator body) pass through unchanged.
        """
        return self.generator_forwarded_locals.get(name, name)

    def frame_slot_deref(self, name: str) -> str | None:
        """The `(*storage)` read expression for a name backed by a
        `tpy::frame_slot<T>` field, or None when no such unwrap is needed.

        A non-value, non-pointer-form generator/async frame local lives in a
        `frame_slot<T>` and is read via `(*name)` (see setup_resumable_frame_locals).
        Single source of truth for that decision, shared by the name-read path
        and the isinstance-narrowing get/holds_alternative sites.
        """
        if not self.in_generator_body:
            return None
        storage = self.generator_storage_name(name)
        if (storage in self.generator_optional_fields
                and storage not in self.frame_field_shadows):
            return f"(*{escape_cpp_name(storage)})"
        return None

    def owning_generator_tuple_locals(
            self, func: 'TpyFunction') -> dict[str, 'TpyType | None']:
        """Generator/async frame locals that OWN tuple element storage, mapped
        to the EFFECTIVE tuple type when it differs from the declared one.

        A frame field for such a local is `frame_slot<std::tuple<..., T>>`, not
        the borrow-form `std::tuple<..., T*>`: a borrow field can't hold owned
        elements and the owning rvalue can't be address-taken into it. Aliasing
        tuple locals (every element bound from a storage lvalue) stay borrow
        form.

        Two sources qualify:
          - typed as a tuple with an `OWN` element (e.g. an await-result lift
            temp `tuple[Own[socket], ...]`). `is_value_type()` reports such a
            tuple True, so without this it would take the raw-field path and
            make the frame default ctor ill-formed (the owned element by value
            is not default-constructible);
          - an init that hands the frame element storage: a tuple LITERAL that
            VALUE-captures a reference element (`t = (A(1), 2)` -- a fresh
            rvalue no one else owns, so a borrow field would dangle at the end
            of the statement), or an owning call (`t = make_pair()`, whose
            result is equally the frame's).

        Ownership is decided PER ELEMENT by each init (`init_verdict`) and
        joined across every init of the name: element i is owned iff every init
        hands it over. The verdict is expressed as an effective TupleType
        with the owned reference elements wrapped in `Own[...]`, so
        `is_owned_movable` / `is_mixed_own` / `tuple_borrow_cpp` route it into
        the frame kinds that already exist. An element one init hands over and
        another only borrows has no single form and is rejected at the decl --
        the borrow would have to point into the owning init's dead temporary.
        """
        # Local import: top-level would cycle (resumable_cfg -> gen_generators
        # -> context).
        from . import resumable_cfg as _rcfg
        one_shot = _rcfg.resumable_state(func).one_shot_lift_names
        scan = self.analyzer.function_scan_results.get(func)
        reassigned = scan.reassigned if scan is not None else set()
        gen_types = dict(func.generator_locals or [])
        owning: dict[str, 'TpyType | None'] = {}

        # Type/provenance-driven owning tuple locals:
        #  - a tuple with an OWN element owns its storage regardless of how it
        #    is bound;
        #  - an `__await_lift_*` temp is ALWAYS an owned result, so even a
        #    reference-element tuple (e.g. `tuple[list[T], int]`) needs owning
        #    `frame_slot<std::tuple<...>>` rather than the borrow-form
        #    `std::tuple<T*, ...>` field a non-await borrow tuple would get --
        #    the owned result can't be assigned into a pointer-element field.
        for lname, ltype in (func.generator_locals or []):
            inner = unwrap_ref_type(ltype)
            if not (isinstance(inner, TupleType) and lname not in reassigned):
                continue
            if (inner.has_own_element()
                    or (lname in one_shot and inner.has_pointer_repr_element())):
                owning[lname] = None

        # Per-name join of the per-element ownership each init implies. A
        # missing entry means "no init said anything", which is the borrow
        # default.
        elem_owned: dict[str, list[bool]] = {}
        # An element ANY init wants owned: the join alone cannot tell "no init
        # owned it" (plain borrow, fine) from "one did and another did not"
        # (no single form -- the diagnostic below).
        elem_wanted: dict[str, list[bool]] = {}
        conflicts: dict[str, TpyStmt] = {}

        def init_verdict(init: TpyExpr, inner: TupleType) -> list[bool]:
            """Per-element ownership THIS ONE init implies, decided here and
            nowhere else -- the join below only merges verdicts.

            A tuple LITERAL owns the elements it VALUE-captures (a fresh
            object no one else holds). An owning CALL hands the frame the
            whole result, so every element the result owns is the frame's;
            which those are is in the return type -- `Own[tuple[...]]` owns
            each reference element, a per-element `tuple[Own[A], B]` owns
            exactly its `Own` slots. Every other init (including a
            borrow-returning call) contributes the borrow default."""
            n = len(inner.element_types)
            if isinstance(init, TpyTupleLiteral) and init.elem_capture:
                verdict = [
                    (init.elem_capture[i] is TupleElemCapture.VALUE
                     and not unwrap_readonly(
                         unwrap_ref_type(inner.element_types[i])).is_value_type())
                    for i in range(min(n, len(init.elem_capture)))]
            elif (isinstance(init, (TpyCall, TpyMethodCall))
                    and self.is_storage_form_source(init)):
                fi = init.resolved_function_info
                rt = unwrap_readonly(fi.return_type) if fi is not None else None
                if isinstance(rt, TupleType):
                    verdict = [isinstance(et, OwnType)
                               for et in rt.element_types][:n]
                else:
                    verdict = [
                        not unwrap_readonly(
                            unwrap_ref_type(et)).is_value_type()
                        for et in inner.element_types]
            else:
                verdict = []
            return verdict + [False] * (n - len(verdict))

        def record(name: str | None, src: TpyExpr | None,
                   at: 'TpyStmt | None') -> None:
            """Join one init's per-element ownership into the name's verdict."""
            ltype = gen_types.get(name) if name is not None else None
            inner = unwrap_ref_type(ltype) if ltype is not None else None
            if name is None or src is None or not isinstance(inner, TupleType):
                return
            init = src.expr if isinstance(src, TpyCoerce) else src
            verdict = init_verdict(init, inner)
            prev = elem_owned.get(name)
            if prev is None:
                elem_owned[name] = verdict
                elem_wanted[name] = list(verdict)
                return
            want = elem_wanted[name]
            for i, (a, b) in enumerate(zip(prev, verdict)):
                if a != b and at is not None:
                    conflicts.setdefault(name, at)
                prev[i] = a and b
                want[i] = want[i] or b

        def visit(stmts: list[TpyStmt]) -> None:
            for stmt in stmts:
                if isinstance(stmt, TpyVarDecl) and stmt.init is not None:
                    record(stmt.name, stmt.init, stmt)
                elif (isinstance(stmt, TpyAssign)
                      and isinstance(stmt.target, TpyName)):
                    record(stmt.target.name, stmt.value, stmt)
                # A walrus binds in expression position: without this row its
                # owning-call source is invisible here and the frame gets a
                # borrow-form tuple field aliasing the dead result temporary.
                for ne in walrus_bindings(stmt):
                    record(ne.target, ne.value, stmt)
                for body in stmt.sub_bodies():
                    visit(body)

        visit(func.body)
        for lname, flags in elem_owned.items():
            if not any(elem_wanted[lname]):
                continue
            if lname in conflicts:
                at = conflicts[lname]
                raise CodeGenError(
                    f"'{lname}' is bound both to a tuple holding a fresh "
                    f"object and to one borrowing an existing object, and "
                    f"lives across a suspension; the two need different "
                    f"storage. Bind the two forms to separate names",
                    loc=getattr(at, "loc", None))
            inner = unwrap_ref_type(gen_types[lname])
            eff = TupleType(tuple(
                OwnType(et) if own and not isinstance(et, OwnType) else et
                for et, own in zip(inner.element_types, flags)))
            owning[lname] = eff
        return owning

    def setup_resumable_frame_locals(self, func: 'TpyFunction') -> None:
        """Populate `pointer_locals`, `generator_optional_fields`, and
        `generator_frame_slot_locals` for a resumable-frame body
        (generator __next__ or async __poll__).

        Also refreshes `one_shot_lift_locals` (the `__await_lift_*` temps) for
        this body, so a tuple-unpack reading one can move its elements out.

        For each local in `func.generator_locals`:
          - value type: not tracked (frame slot is `T name;`, no peel)
          - pointer-form (pointer-repr Optional[NonValue] OR a for-loop
            iter var with a stable lvalue source): added to
            `pointer_locals` -- same dispatch as sync pointer-locals
          - other non-value: added to both `generator_optional_fields`
            and `generator_frame_slot_locals` (frame slot is
            `tpy::frame_slot<T> name;`, reads via `(*name)`,
            writes via `.emplace(value)`)

        Caller's contract: all three sets are cleared at body entry and
        restored on exit (matches the existing `generator_*` save/restore
        dance). `generator_for_loop_info` must already be populated.
        """
        # Local import: top-level would cycle (resumable_cfg -> gen_generators
        # -> context).
        from . import resumable_cfg as _rcfg
        self.one_shot_lift_locals = set(
            _rcfg.resumable_state(func).one_shot_lift_names)
        # Per-write-site frame homes for rvalue writes into pointer-form
        # frame locals (see _prescan_resumable_ptr_slots); empty for the
        # simple-peephole path, whose locals live on the lambda stack.
        self.resumable_ptr_slot_map = (
            _rcfg.resumable_state(func).ptr_slot_map.copy())

        if not func.generator_locals:
            return

        # One placement verdict per hoisted local, shared with the struct
        # field-decl emit (gen_async._frame_layout). The resumable path has
        # the plan cached by struct-emit time; the simple-peephole path (no
        # frame struct, locals on the lambda stack) builds it here -- its
        # prescan sets are empty by construction, which is what keeps the
        # peephole verdicts alias-free.
        plan = _rcfg.resumable_state(func).frame_layout
        if plan is None:
            plan = self.frame_layout_builder(func)
        for lname, verdict in plan.bindings.items():
            kind = verdict.kind
            if kind is _rcfg.FrameLocalKind.PTR_ALIAS:
                # A `T*` field aliasing live storage (pointer-form loop var,
                # unpack target, or statement-level borrow alias): same
                # dispatch as sync pointer-locals, seeded up-front so the
                # for-loop emit path doesn't mutate `pointer_locals`
                # mid-emission.
                self.pointer_locals.add(lname)
                self.generator_borrow_form_loop_vars.add(lname)
                if verdict.const:
                    self.const_indirect_locals.add(lname)
            elif kind is _rcfg.FrameLocalKind.OPT_PTR:
                self.pointer_locals.add(lname)
                if verdict.const:
                    self.const_indirect_locals.add(lname)
            elif kind is _rcfg.FrameLocalKind.BORROW_TUPLE:
                # Borrow-form tuple frame fields are declared
                # std::tuple<..., T*> (gen_coro_struct), so assignments from
                # storage sources need the element-wise pointer lift like any
                # borrow-form local; proxy-ref loop elements (dict_items)
                # carry the same form.
                self.borrow_form_tuple_locals.add(lname)
            elif kind is _rcfg.FrameLocalKind.SOURCE_FORM_SLOT:
                # A source-derived loop var is a frame slot whatever its
                # element turns out to be -- including a value element:
                # reads go through `(*name)` whether the trait picked alias
                # or own; that is the point of deferring the choice to C++.
                self.generator_optional_fields.add(lname)
                self.generator_frame_slot_locals.add(lname)
            elif kind is _rcfg.FrameLocalKind.OWNING_TUPLE_SLOT:
                self.generator_optional_fields.add(lname)
                self.generator_frame_slot_locals.add(lname)
                # The frame slot holds the tuple BY VALUE (storage form), so
                # element reads use `.` not `->` -- mark it a storage-form
                # source like the sync owning local.
                self.storage_form_tuple_locals.add(lname)
            elif kind is _rcfg.FrameLocalKind.MIXED_TUPLE_SLOT:
                self.generator_optional_fields.add(lname)
                self.generator_frame_slot_locals.add(lname)
                # A MIXED tuple is by value only in its OWNED half, so the
                # slot holds the mixed render: element reads split, `.` for
                # the owned element and `->` for the borrowed pointer.
                self.storage_form_tuple_locals.add(lname)
                self.own_borrow_tuple_locals.add(lname)
            elif kind in (_rcfg.FrameLocalKind.FRAME_SLOT,
                          _rcfg.FrameLocalKind.PROTOCOL):
                # Non-pointer-form non-value frame fields are backed by
                # `tpy::frame_slot<T>`: writes route through `.emplace(...)`,
                # reads use the `(*name)` access. PROTOCOL locals seed the
                # same sets: the resumable struct emit raises its user-facing
                # error before any read renders, and the peephole never
                # renders fields at all, so the membership is inert but keeps
                # the dispatch total.
                self.generator_optional_fields.add(lname)
                self.generator_frame_slot_locals.add(lname)
            # VALUE / OWNED_STR: no context seeding -- value-typed access
            # needs no peel, and the owned-str field reads as a plain string.

    def is_already_pointer_source(self, expr: TpyExpr) -> bool:
        """True when `expr` renders as a `T*` value with no further lifting.

        Composes `is_indirect_name` (pointer locals/globals/self/imports --
        names whose rendering is already `T*`) with the `Ptr[T]` source
        case (any expression whose sema type is `PtrType`). Used by the
        codegen sites that lift other source shapes via `&(...)` or
        `optional_to_ptr`: when this returns True, the bare rendering is
        already in the right shape and the lift is a bug (`T**` / wrong type).
        """
        if self.is_indirect_name(expr):
            return True
        return isinstance(self.get_expr_type(expr), PtrType)

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

    def _call_returns_cpp_ref(self, fi: FunctionInfo | None) -> bool:
        return _call_returns_cpp_ref_shared(self.analyzer, fi)

    def is_rvalue_source(self, expr: TpyExpr) -> bool:
        return _is_rvalue_source_shared(self.analyzer, expr)

    def is_container_literal_expr(self, expr: TpyExpr) -> bool:
        """Predicate form of `_CONTAINER_LITERAL_NODES`; see the constant
        comment for the value-emit-rvalue rationale."""
        return isinstance(expr, _CONTAINER_LITERAL_NODES)

    def is_value_emit_rvalue(self, expr: TpyExpr) -> bool:
        """Rvalue source that renders as a value (not a `T*`), so a
        pointer-form Optional sink needs to materialize a named slot before
        taking address. Container/comprehension/generator literals plus
        rvalue field accesses (`temp.field` on a moved-from object) are the
        exhaustive set under the pointer-repr-Optional outer guard at the
        pointer-local init and rebind sites.
        """
        return self.is_container_literal_expr(expr) or (
            isinstance(expr, TpyFieldAccess) and self.is_rvalue_source(expr))

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
        # Container literals, comprehensions, generator expressions.
        if self.is_container_literal_expr(expr):
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
