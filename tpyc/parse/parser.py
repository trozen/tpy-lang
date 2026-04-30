"""
TurboPython Parser.

Uses CPython's ast module to parse TurboPython source code.
Validates that only allowed constructs are used.
"""

from __future__ import annotations
import ast
import copy
import dataclasses
import re
import textwrap
from typing import Any, Literal, NoReturn, TYPE_CHECKING

from ..typesys import (
    FieldInfo, RecordInfo, TypeRegistry,
    FunctionInfo, MethodSignature, ProtocolInfo, TypeParamKind, LiteralValue,
)
from ..module_names import public_module_name
from ..type_def_registry import (
    is_bool_type, is_str_type, type_def_of,
    find_factory_by_simple_name, find_factory_in_module,
)
from .type_resolver import TypeResolver

if TYPE_CHECKING:
    from ..typesys import TpyType
from .nodes import (
    ParseError, SourceLocation, ParseWarning, RecordLinkage, FunctionLinkage,
    TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef, TpyInferFromDefaultRef,
    ResolverInputNode, TypeRefNode,
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_ASCII,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct,
    TpyStarUnpack, TpyCall, TpyMethodCall,
    TpyFieldAccess, TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyComprehensionGenerator, TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr, TpyLambda,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyDelVar, TpyExprStmt, TpyReturn, TpyYield,
    TpyAssert, TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue,
    TpyPassStmt, TpyGlobal, TpyNonlocal, TpyRaise, TpyExceptHandler, TpyTry, TpyWithItem, TpyWith,
    TpyNestedDef,
    TpyPattern, TpyWildcardPattern, TpyCapturePattern, TpyClassPattern,
    TpyLiteralPattern, TpyValuePattern, TpyOrPattern, TpyAsPattern,
    TpyMatchCase, TpyMatch,
    RelativeImportKey, TpyImport, TpyFunction, TpyRecord, TpyProtocol, TpyEnum, TpyModule,
    ModuleDirectives,
)
from .imports import (
    ImportProcessor, StarImportResolver, _PRIVATE_MODULE_PUBLIC_NAMES, _IMPLICIT_MODULES,
    get_builtins_exports, get_typing_exports, get_tpy_exports,
)
from .. import qnames

# Modules whose names the parser resolves structurally (base class detection,
# enum auto(), Unpack).  A local def/class/assignment that shadows one of these
# names is warned about so the user knows the parser keyword is hidden.
# Only typing and enum -- tpy/builtins names are resolved via normal sema and
# shadowing them is routine (e.g. user-defined `copy` replacing tpy.copy).
_PARSER_KEYWORD_MODULES = frozenset({"typing", "enum"})

# Decorator names that mark stdlib stubs (builtin types, decorators, functions).
# Definitions with these decorators are excluded from _local_defs because they
# intentionally re-define imported names (e.g. @builtin_type class Protocol).
# Checked via raw AST name (_decorator_raw_name) since import resolution hasn't
# run yet during pre-scan.
_BUILTIN_DEC_NAMES = frozenset({"builtin_type", "builtin_decorator", "builtin_function"})

# Fixed-int constructor names recognized syntactically by the parser for
# default-value validation (_validate_const_default) and the C++ literal
# simplification in _get_default_value. The actual TpyType lookup happens
# in the TypeResolver via `_FIXED_INT_MAP`; here we only need name
# membership. Kept in sync with typesys.ALL_FIXED_INTS.
_FIXED_INT_NAMES: frozenset[str] = frozenset({
    "Int8", "Int16", "Int32", "Int64",
    "UInt8", "UInt16", "UInt32", "UInt64",
})

# Operator-to-string mappings for AST binary, comparison, and unary operators
_BINOP_TO_STR: dict[type, str] = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
    ast.Div: "div", ast.Mod: "%", ast.FloorDiv: "//",
    ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
    ast.LShift: "<<", ast.RShift: ">>",
    ast.Pow: "**",
}

_CMPOP_TO_STR: dict[type, str] = {
    ast.Eq: "==", ast.NotEq: "!=",
    ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=",
    ast.In: "in", ast.NotIn: "not in",
    ast.Is: "is", ast.IsNot: "is not",
}

_UNARYOP_TO_STR: dict[type, str] = {
    ast.UAdd: "+", ast.USub: "-", ast.Not: "!", ast.Invert: "~",
}


def _validate_fstring_format_spec(spec: str) -> str | None:
    """Validate an f-string format spec against C++ std::format support.

    Returns an error message for Python-only features, or None if valid.
    Python features not supported by std::format: '=' alignment, 'z' option,
    ',' and '_' grouping, 'n' and '%' type codes.
    """
    if not spec:
        return None

    pos = 0
    n = len(spec)

    # [[fill]align] -- fill can be ANY character if followed by an align char
    _ALIGN = '<>^='
    if n >= 2 and spec[1] in _ALIGN:
        if spec[1] == '=':
            return "'=' alignment is not supported"
        pos = 2
    elif spec[0] in _ALIGN:
        if spec[0] == '=':
            return "'=' alignment is not supported"
        pos = 1

    # [sign]
    if pos < n and spec[pos] in '+- ':
        pos += 1

    # [z]
    if pos < n and spec[pos] == 'z':
        return "'z' option is not supported"

    # [#]
    if pos < n and spec[pos] == '#':
        pos += 1

    # [0]
    if pos < n and spec[pos] == '0':
        pos += 1

    # [width]
    while pos < n and spec[pos].isdigit():
        pos += 1

    # [grouping_option]
    if pos < n and spec[pos] in ',_':
        return f"'{spec[pos]}' grouping is not supported"

    # [.precision]
    if pos < n and spec[pos] == '.':
        pos += 1
        while pos < n and spec[pos].isdigit():
            pos += 1

    # [type]
    if pos < n:
        t = spec[pos]
        if t == 'n':
            return "'n' (locale-aware) type is not supported"
        if t == '%':
            return "'%' (percentage) type is not supported"

    return None


def _extract_subscript_slices(node: ast.Subscript) -> list[ast.expr]:
    """Extract individual type argument nodes from a subscript slice.

    Handles both single-arg (X[T]) and multi-arg (X[T, U]) forms.
    """
    if isinstance(node.slice, ast.Tuple):
        return node.slice.elts
    return [node.slice]


def _attr_chain_to_dotted(node: ast.expr) -> str | None:
    """Flatten ast.Name / ast.Attribute chains into a dotted string.

    Returns None for any other expression shape (call result, subscript, etc.).
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return None


# Requires whitespace after `tpy:` to avoid matching C++ namespace comments (# tpy::Foo)
_DIRECTIVE_LINE_RE = re.compile(r'^#\s*tpy:\s+(\w.+)$')

# Schema: (positional arg types, allowed keyword arg types)
# Keys are the known directive names; unknown names produce a warning.
_DIRECTIVE_SPECS: dict[str, tuple[list[type], dict[str, type]]] = {
    "native_module":     ([], {}),
    "macro_module":      ([], {}),
    "include":           ([str], {"platform": str}),
    # link: raw `-lfoo` by default; `managed=True` routes through the
    # third-party registry (tpyc/build/third_party.py) for bundled/system/
    # auto resolution via the `--<lib>=<mode>` CLI flag.
    "link":              ([str], {"platform": str, "managed": bool}),
    "cpp_namespace":     ([str], {}),
}

_CPP_NAMESPACE_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(::[A-Za-z_][A-Za-z0-9_]*)*$')


def _parse_directive_call(content: str) -> tuple[str, list, dict] | None:
    """Parse directive content as a bare name or Python-style call.

    Returns (name, positional_args, keyword_args), or None on parse error.
    """
    content = content.strip()
    if re.match(r'^\w+$', content):
        return (content, [], {})
    try:
        tree = ast.parse(content, mode='eval')
        expr = tree.body
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            name = expr.func.id
            pos_args = [ast.literal_eval(a) for a in expr.args]
            kw_args = {kw.arg: ast.literal_eval(kw.value)
                       for kw in expr.keywords if kw.arg is not None}
            return (name, pos_args, kw_args)
    except (SyntaxError, ValueError):
        pass
    return None


def _check_directive_args(
    name: str, args: list, kwargs: dict,
    spec: tuple[list[type], dict[str, type]],
    loc: SourceLocation, warnings: list[ParseWarning],
) -> bool:
    """Validate args/kwargs against a directive spec. Returns True if valid."""
    pos_types, kw_types = spec
    if len(args) != len(pos_types):
        warnings.append(ParseWarning(
            f"'{name}' expects {len(pos_types)} positional argument(s), got {len(args)}", loc))
        return False
    for i, (val, typ) in enumerate(zip(args, pos_types)):
        if not isinstance(val, typ):
            warnings.append(ParseWarning(
                f"'{name}' argument {i + 1} must be a {typ.__name__}", loc))
            return False
    unknown = {k for k in kwargs if k not in kw_types}
    if unknown:
        warnings.append(ParseWarning(
            f"'{name}' unknown keyword arguments: {sorted(unknown)}", loc))
        return False
    for k, val in kwargs.items():
        if not isinstance(val, kw_types[k]):
            warnings.append(ParseWarning(
                f"'{name}' keyword '{k}' must be a {kw_types[k].__name__}", loc))
            return False
    return True


def _scan_directives(source_lines: list[str]) -> tuple[ModuleDirectives, list[ParseWarning]]:
    """Scan all standalone # tpy: comment lines and return parsed directives."""
    includes: list[tuple[str, str | None]] = []
    link_libs: list[tuple[str, str | None]] = []
    third_party_deps: list[tuple[str, str | None]] = []
    native_module = False
    cpp_namespace: str | None = None
    warnings: list[ParseWarning] = []

    preamble_ended = False
    for lineno, line in enumerate(source_lines, start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            preamble_ended = True
        if not stripped.startswith('#'):
            continue
        m = _DIRECTIVE_LINE_RE.match(stripped)
        if not m:
            continue
        content = m.group(1).strip()
        loc = SourceLocation(line=lineno)
        if preamble_ended:
            warnings.append(ParseWarning(
                "# tpy: directives must appear before any code", loc))
            continue

        parsed = _parse_directive_call(content)
        if parsed is None:
            warnings.append(ParseWarning(f"invalid # tpy: directive syntax: {content!r}", loc))
            continue

        name, args, kwargs = parsed
        spec = _DIRECTIVE_SPECS.get(name)
        if spec is None:
            warnings.append(ParseWarning(f"unknown # tpy: directive: {name!r}", loc))
            continue
        if not _check_directive_args(name, args, kwargs, spec, loc, warnings):
            continue

        if name == "native_module":
            native_module = True
        elif name == "include":
            includes.append((args[0], kwargs.get("platform")))
        elif name == "link":
            # managed=True -> registry-resolved third-party dep.
            # managed=False (or omitted) -> raw -lfoo linker flag.
            if kwargs.get("managed", False):
                third_party_deps.append((args[0], kwargs.get("platform")))
            else:
                link_libs.append((args[0], kwargs.get("platform")))
        elif name == "cpp_namespace":
            ns_value = args[0]
            if not _CPP_NAMESPACE_RE.match(ns_value):
                warnings.append(ParseWarning(
                    f"invalid namespace: {ns_value!r} (must be valid C++ namespace like 'foo::bar')", loc))
                continue
            if cpp_namespace is not None:
                warnings.append(ParseWarning(
                    f"duplicate 'cpp_namespace' directive (previous: {cpp_namespace!r})", loc))
            cpp_namespace = ns_value

    return ModuleDirectives(
        includes=includes, link_libs=link_libs,
        third_party_deps=third_party_deps,
        native_module=native_module,
        cpp_namespace=cpp_namespace,
    ), warnings


def _collect_bitor_arms(node: ast.BinOp) -> list[ast.expr]:
    """Flatten a left-recursive chain of A | B | C into [A, B, C]."""
    arms: list[ast.expr] = []
    if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.BitOr):
        arms.extend(_collect_bitor_arms(node.left))
    else:
        arms.append(node.left)
    arms.append(node.right)
    return arms


class _NameArg:
    """Decorator argument that is a name reference (e.g. StopIteration in @error_return(StopIteration))."""
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


@dataclasses.dataclass(frozen=True)
class _DecoratorArgSchema:
    """Schema for a decorator's positional and keyword arguments.

    pos_type: expected type for the positional arg (str, bool, _NameArg), or None = bare only
    pos_required: whether the positional arg must be provided
    pos_description: human-readable type description for error messages (e.g. "bool")
    kwargs: allowed keyword arg names -> expected types (None = no kwargs)
    """
    pos_type: type | None = None
    pos_required: bool = False
    pos_description: str | None = None
    kwargs: dict[str, type] | None = None


# Argument schemas for all known decorators. Decorators not listed here
# (macro decorators, etc.) are validated by their own paths.
_DECORATOR_ARG_SCHEMAS: dict[str, _DecoratorArgSchema] = {
    # Only decorators without @builtin_decorator stubs need explicit schemas.
    # All other schemas are derived from stub signatures in .py files
    # (see Parser._schema_from_stub and Parser._decorator_schemas).
    qnames.STATICMETHOD:      _DecoratorArgSchema(),  # Python builtin, no stub
}


def _stmt_child_bodies(stmt: TpyStmt) -> list[list[TpyStmt]]:
    """Return the child statement bodies of a compound statement (no nested defs)."""
    if isinstance(stmt, TpyIf):
        return [stmt.then_body, stmt.else_body]
    elif isinstance(stmt, TpyWhile):
        bodies = [stmt.body]
        if stmt.orelse:
            bodies.append(stmt.orelse)
        return bodies
    elif isinstance(stmt, TpyForEach):
        bodies = [stmt.body]
        if stmt.orelse:
            bodies.append(stmt.orelse)
        return bodies
    elif isinstance(stmt, TpyWith):
        return [stmt.body]
    elif isinstance(stmt, TpyTry):
        bodies = [stmt.try_body, stmt.else_body, stmt.finally_body]
        for h in stmt.handlers:
            bodies.append(h.body)
        return bodies
    elif isinstance(stmt, TpyMatch):
        return [case.body for case in stmt.cases]
    return []


def _body_contains_yield(stmts: list[TpyStmt]) -> bool:
    """Check if a function body contains any yield statements (non-recursive into nested defs)."""
    for stmt in stmts:
        if isinstance(stmt, TpyYield):
            return True
        for child_body in _stmt_child_bodies(stmt):
            if _body_contains_yield(child_body):
                return True
    return False


def _check_no_return_value_in_generator(
    stmts: list[TpyStmt], func_name: str,
) -> None:
    """Reject 'return value' inside a generator function body."""
    for stmt in stmts:
        if isinstance(stmt, TpyReturn) and stmt.value is not None:
            # Create a minimal object with lineno for ParseError
            err = ParseError(
                f"Generator function '{func_name}' cannot use 'return' with a value")
            if stmt.loc:
                err.lineno = stmt.loc.line
            raise err
        for child_body in _stmt_child_bodies(stmt):
            _check_no_return_value_in_generator(child_body, func_name)


class Parser:
    """Parser for TurboPython source code."""

    FORBIDDEN_CONSTRUCTS = {
        "with", "async", "await",
    }

    def __init__(self, decorator_schemas: dict[str, '_DecoratorArgSchema'] | None = None,
                 star_import_resolver: StarImportResolver | None = None):
        self.registry = TypeRegistry()
        self.source_lines: list[str] = []
        self._type_param_scope: dict[str, TypeParamKind] | None = None
        self._warnings: list[ParseWarning] = []
        self._imports = ImportProcessor(self._warn)
        self._module_aliases: dict[str, str] = {}
        self._bare_module_imports: set[str] = set()
        self._reverse_module_aliases: dict[str, str] = {}
        self._for_unpack_counter: int = 0
        self._multi_assign_counter: int = 0
        # Schemas derived from @builtin_decorator stubs (populated by compiler
        # from previously-parsed modules, or from same-file definitions)
        self._decorator_schemas: dict[str, _DecoratorArgSchema] = dict(decorator_schemas) if decorator_schemas else {}
        self._star_import_resolver = star_import_resolver
        # Top-level class names pre-scanned from the module body, used to
        # resolve forward references in type annotations.
        self._module_class_names: frozenset[str] = frozenset()
        # Top-level type alias names pre-scanned, for forward references
        # (e.g. Box[Expr] in a record field before `type Expr = ...` is parsed)
        self._module_type_alias_names: frozenset[str] = frozenset()
        # Union aliases detected as recursive (self- or mutually-referencing)
        self._recursive_union_names: set[str] = set()
        # All module-level definitions (def, class, assignment) pre-scanned
        # to detect when local names shadow imports for parser keyword resolution.
        self._local_defs: frozenset[str] = frozenset()
        # Maps short nested type names to dotted names while inside a class body.
        # E.g., while parsing class Message: class Kind(Enum): ..., maps "Kind" -> "Message.Kind"
        self._nested_type_scope: dict[str, str] = {}
        # Type-ref resolver: holds a back-reference to this parser so it
        # reads live state (registry, imports, local_defs, ...) each call.
        # Attached to TpyModule.resolver at end of parse(); sema delegates
        # here.
        self._resolver = TypeResolver(self)
        # Module # tpy: directives, populated by parse() before
        # _parse_module so class/protocol/enum registration can see
        # cpp_namespace at registration time.
        self._directives = ModuleDirectives()
        # True when the module is compiled as an entry point. Overrides
        # the module name used by `_public_module()` to `"__main__"`.
        # Set by parse() from its `is_entry_point` argument.
        self._is_entry_point: bool = False

    def _loc(self, node: ast.AST) -> SourceLocation | None:
        """Create a SourceLocation from an AST node."""
        if hasattr(node, 'lineno'):
            col = getattr(node, 'col_offset', 0)
            return SourceLocation(line=node.lineno, column=col)
        return None

    def _warn(self, message: str, node: ast.AST | None = None) -> None:
        """Record a parser warning."""
        loc = self._loc(node) if node else None
        self._warnings.append(ParseWarning(message, loc))

    @staticmethod
    def _qualify(resolved: tuple[str, str]) -> str:
        """Join a (module, name) resolution to a qualified name string."""
        return f"{resolved[0]}.{resolved[1]}"

    def _public_module(self) -> str:
        """Return the public module name for records/protocols/enums
        registered from this module, collapsing private submodules via
        `public_module_name`.  Used to populate `RecordInfo.module`,
        `ProtocolInfo.module`, and enum placeholder qnames.

        Entry-point modules use `"__main__"` regardless of their on-disk
        file name, matching sema's convention (`analyzer.analyze` sets
        `ctx.module_name = "__main__"` for the entry point) and Python's
        runtime `__name__` semantics.  Parser and sema must agree on the
        qname so the resolver mints authoritative `_module_qname` values
        on the first pass.

        Fallback: when the parser is invoked without a `module_name`
        (e.g. the `test_parse_type_ref.py` unit harness parses snippets
        without a module context), return `"__main__"` so qname
        construction stays well-formed.  Compiler-driven parses always
        set `module_name` + `is_entry_point`, so this branch is only
        reached from fragment-level tests."""
        if self._is_entry_point:
            return "__main__"
        return public_module_name(
            self._imports._module_name,
            self._directives.cpp_namespace,
        ) or "__main__"

    def _resolve_type_name(self, local_name: str) -> tuple[str, str] | None:
        """Resolve annotation name -> (module, original_name) or None.

        Checks explicit imports, then Python builtins, then local @builtin_type
        definitions. Returns None when the name is shadowed by a module-level
        def/class/assignment (excluding @builtin_type/decorator/function stubs).
        """
        if local_name in self._local_defs:
            return None
        source = self._imports.get_import_source(local_name)
        if source:
            return source

        if local_name in get_builtins_exports():
            return ("builtins", local_name)

        # Check for @builtin_type / @builtin_decorator defined locally in this file
        builtin_key = self.registry.get_builtin_type_key(local_name) or self.registry.get_builtin_decorator_key(local_name)
        if builtin_key:
            parts = builtin_key.rsplit(".", 1)
            if len(parts) == 2:
                return (parts[0], parts[1])

        # Auto-resolve builtin_decorator within tpy.extern's own module
        if local_name == "builtin_decorator" and self._imports._module_name:
            pub = _PRIVATE_MODULE_PUBLIC_NAMES.get(self._imports._module_name) or public_module_name(self._imports._module_name)
            if pub == "tpy.extern":
                return ("tpy.extern", local_name)

        return None

    def _resolve_parser_keyword(self, node: ast.expr) -> tuple[str, str] | None:
        """Resolve a Name or Attribute node through import resolution.

        Convenience wrapper that dispatches to _resolve_type_name (for bare
        names) or _resolve_qualified_type_name (for module.attr). Both
        underlying methods already respect _local_defs shadowing.
        """
        if isinstance(node, ast.Name):
            return self._resolve_type_name(node.id)
        elif isinstance(node, ast.Attribute):
            return self._resolve_qualified_type_name(node)
        return None

    def _resolve_qualified_type_name(self, node: ast.Attribute) -> tuple[str, str] | None:
        """Resolve module.Name -> (module, name) or None.

        Only resolves if the module was bare-imported (import X or import X as Y).
        'from X import ...' does NOT put the module name in scope.
        Returns None if the module prefix is shadowed by a local definition.
        """
        if not isinstance(node.value, ast.Name):
            return None
        local_module = node.value.id
        if local_module in self._local_defs:
            return None
        canonical = self._reverse_module_aliases.get(local_module, local_module)
        # Verify the module was bare-imported (imports[canonical] is None means
        # whole-module import; the key being absent means no import at all).
        # For parser-keyword modules, None marks whole-module import.
        # For user modules, they're in bare_module_imports (checked via imports dict).
        imports = self._imports.imports
        if imports is None or canonical not in imports:
            return None
        entry = imports[canonical]
        # None means whole-module import (parser-keyword modules).
        # For user modules, bare import sets an empty set AND adds to bare_module_imports.
        if entry is not None and not (isinstance(entry, set) and canonical in self._bare_module_imports):
            return None
        return (canonical, node.attr)

    def _resolve_dotted_class_name(self, node: ast.Attribute) -> str | None:
        """Resolve Outer.Inner (or Outer.Mid.Inner) to a dotted name string.

        Returns the dotted name if the root is a known class name, else None.
        """
        parts: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in self._module_class_names:
            parts.append(cur.id)
            parts.reverse()
            return ".".join(parts)
        return None

    def _raise_unresolved_import_error(
        self, raw_name: str, node: ast.expr | None = None,
        *, loc: SourceLocation | None = None,
    ) -> None:
        """Raise a helpful error for unresolved type names with import hints."""
        if raw_name in get_typing_exports():
            raise ParseError(
                f"'{raw_name}' requires: from typing import {raw_name}", node, loc=loc,
            )
        # In stdlib _core modules, unresolved names may be forward references
        # to types defined later in the same file. Let them through.
        mod = self._imports._module_name
        if mod and "._" in mod and raw_name in self._module_class_names:
            if public_module_name(mod) in _IMPLICIT_MODULES:
                return
        if raw_name in get_tpy_exports():
            raise ParseError(
                f"'{raw_name}' requires: from tpy import {raw_name}", node, loc=loc,
            )

    def _is_ignorable_for_import_order(self, node: ast.stmt) -> bool:
        """Check if a statement should be ignored for import ordering.

        Module docstrings and pass statements don't count as "code"
        for the purpose of detecting late imports.
        """
        if isinstance(node, ast.Pass):
            return True
        # Module docstring (expression statement with a string literal)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return True
        return False

    def parse(self, source: str, module_name: str | None = None,
              is_package_init: bool = False,
              is_entry_point: bool = False) -> TpyModule:
        """Parse TurboPython source code into a TpyModule.

        `is_entry_point` mirrors the compiler's convention of treating
        the entry-point module as `__main__` for qname purposes
        (matching sema's `ctx.module_name = "__main__"` rename and
        Python's runtime `__name__` convention). It only affects the
        value returned by `_public_module()` and hence the qnames
        attached to record / protocol / enum-placeholder registrations;
        import resolution still uses the original `module_name`.
        """
        self.source_lines = source.splitlines()
        self._warnings = []
        self._is_entry_point = is_entry_point
        self._imports = ImportProcessor(self._warn, module_name=module_name,
                                        is_package_init=is_package_init,
                                        star_import_resolver=self._star_import_resolver)
        self._module_aliases = {}
        self._bare_module_imports = set()
        self._reverse_module_aliases = {}
        tree = ast.parse(source)
        # Scan directives first so cpp_namespace is available while
        # _parse_module registers records/protocols/enums.
        directives, directive_warnings = _scan_directives(self.source_lines)
        self._directives = directives
        module = self._parse_module(tree)
        module.directives = directives
        module.parse_warnings.extend(directive_warnings)
        # Attach the ref resolver so sema can resolve TypeRefNodes emitted
        # at annotation sites.  The TypeResolver instance holds a back-ref
        # to this parser and reads parser state each call, so sema sees
        # live containers (registry, imports, local_defs, ...).
        module.resolver = self._resolver
        return module

    # Names that _parse_type_annotation resolves directly (not through registry)
    _BUILTIN_TYPE_NAMES = frozenset({
        "int", "float", "bool", "str", "None", "tuple",
    })

    def _is_type_name(self, name: str) -> bool:
        """Check if a name is recognizable as a type by _parse_type_annotation."""
        resolved = self._resolve_type_name(name)
        if resolved:
            original = resolved[1]
            if original in self._BUILTIN_TYPE_NAMES or original in get_tpy_exports():
                return True
            if original == "Self":
                return True
        # Also check registry directly for user-defined types
        return self.registry.is_known_type(name)

    def _could_be_type(self, name: str) -> bool:
        """Check if a name could plausibly refer to a type.

        Checks the local class pre-scan, the parser registry, and import
        resolution. Used at call-parsing sites where the parser needs to
        decide whether Name[args](...) could be a type instantiation.
        """
        return (name in self._module_class_names
                or name in self._module_type_alias_names
                or self.registry.is_known_type(name)
                or self._resolve_type_name(name) is not None)

    def _is_type_alias_assign(self, node: ast.Assign) -> bool:
        """Check if an assignment is an old-style type alias (e.g., Shape = Circle | Rect).

        Triggers when ALL arms are Names/None AND at least one arm is a
        confirmed type (registered or builtin). Pure forward-ref aliases
        (all arms are class names defined in this module) also match.
        """
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return False
        if not (isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.BitOr)):
            return False
        arms = _collect_bitor_arms(node.value)
        has_confirmed_type = False
        all_module_classes = True
        for arm in arms:
            if isinstance(arm, ast.Constant) and arm.value is None:
                has_confirmed_type = True
                continue
            if not isinstance(arm, ast.Name):
                return False
            if self._is_type_name(arm.id):
                has_confirmed_type = True
            elif arm.id not in self._module_class_names and arm.id not in self._module_type_alias_names:
                all_module_classes = False
        return has_confirmed_type or all_module_classes

    def _register_type_alias(
        self, name: str, type_node: ast.expr,
        type_aliases: 'dict[str, tuple[TpyType | TypeRefNode, SourceLocation | None]]'
    ) -> None:
        """Parse a type alias RHS as a TypeRefNode.

        Sema's `_resolve_pending_type_refs` resolves the ref with a
        `pending_alias` kwarg so same-body self-references produce a
        `NominalType(name)` placeholder.  Sema then detects recursive
        unions and registers the resolved alias in parser.registry so
        later alias bodies can reference it.
        """
        ref = self._parse_type_ref(type_node)
        loc = SourceLocation(line=type_node.lineno) if hasattr(type_node, 'lineno') else None
        type_aliases[name] = (ref, loc)

    def _parse_module(self, tree: ast.Module) -> TpyModule:
        """Parse a module."""
        self._module_class_names = frozenset(
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        )
        self._module_type_alias_names = frozenset(
            node.name.id for node in tree.body if isinstance(node, ast.TypeAlias)
        )
        # Pre-scan all module-level definitions (def, class, assignment) so that
        # _resolve_type_name returns None for shadowed imports.  This prevents
        # parser keywords (Enum, Protocol, auto, decorators, type names) from
        # being misresolved when shadowed by a local name.
        local_defs: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                if not any(self._decorator_raw_name(d) in _BUILTIN_DEC_NAMES
                           for d in node.decorator_list):
                    local_defs.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        local_defs.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                local_defs.add(node.target.id)
        self._local_defs = frozenset(local_defs)
        records = []
        functions = []
        protocols = []
        enums = []
        top_level_stmts = []
        type_aliases: dict[str, tuple[TpyType, SourceLocation | None]] = {}
        imports: dict[str, set[tuple[str, str]] | None | str] = {}
        user_module_imports: dict[str, int] = {}
        module_aliases: dict[str, str] = {}
        bare_module_imports: set[str] = set()
        self._module_aliases = module_aliases
        self._bare_module_imports = bare_module_imports
        # Set imports reference early so _resolve_qualified_type_name can check it
        self._imports.imports = imports
        seen_non_import = False

        for node in tree.body:
            is_import = isinstance(node, (ast.Import, ast.ImportFrom))

            # Check for late imports (imports after non-import code)
            if is_import and seen_non_import:
                self._warn("Import statement should be at the top of the file", node)

            if isinstance(node, ast.ImportFrom):
                self._imports.process_import_from(node, imports, user_module_imports, top_level_stmts, module_aliases)
                # Rebuild reverse alias mapping after each import
                self._reverse_module_aliases = {v: k for k, v in module_aliases.items()}
            elif isinstance(node, ast.Import):
                self._imports.process_import(node, imports, user_module_imports, top_level_stmts, module_aliases, bare_module_imports)
                # Rebuild reverse alias mapping after each import
                self._reverse_module_aliases = {v: k for k, v in module_aliases.items()}
            elif isinstance(node, ast.ClassDef):
                seen_non_import = True
                # Warn if class shadows an imported parser keyword name
                # (skip for @builtin_type classes -- shadow is intentional)
                source = self._imports.get_import_source(node.name)
                has_builtin_type = any(
                    self._decorator_local_name(d) in ("builtin_type", qnames.BUILTIN_TYPE)
                    for d in node.decorator_list)
                if source and source[0] in _PARSER_KEYWORD_MODULES and not has_builtin_type:
                    self._warn(f"class '{node.name}' shadows import from '{source[0]}'", node)
                result = self._parse_class(node)
                if isinstance(result, TpyProtocol):
                    protocols.append(result)
                    # Register the protocol type
                    self.registry.register_protocol(ProtocolInfo(
                        name=result.name,
                        methods=result.methods,
                        type_params=result.type_params,
                        is_dynamic=result.is_dynamic,
                        module=self._public_module(),
                    ))
                elif isinstance(result, TpyEnum):
                    enums.append(result)
                    # Register an enum placeholder so it can be used in type
                    # annotations within the same file. Sema re-registers with
                    # the fully-populated NominalType + TypeDef.enum payload.
                    self.registry.register_enum_placeholder(
                        result.name, module=self._public_module())
                else:
                    records.append(result)
                    # Register the record type
                    self.registry.register_record(RecordInfo(
                        name=result.name,
                        fields=result.fields,
                        has_init=result.init_method is not None,
                        builtin_type_key=result.builtin_type_key,
                        module=self._public_module(),
                    ))
                    # Prefix nested type names with parent chain and register
                    self._prefix_nested_names(result, result.name)
                    self._register_nested_types(result)
            elif isinstance(node, ast.FunctionDef):
                seen_non_import = True
                # Warn if function shadows an imported parser keyword name
                # (skip for @builtin_function/builtin_decorator -- shadow is intentional)
                source = self._imports.get_import_source(node.name)
                if source and source[0] in _PARSER_KEYWORD_MODULES:
                    has_builtin_dec = any(
                        self._decorator_local_name(d) in (
                            "builtin_function", "builtin_decorator",
                            qnames.BUILTIN_FUNCTION, qnames.BUILTIN_DECORATOR)
                        for d in node.decorator_list)
                    if not has_builtin_dec:
                        self._warn(f"def '{node.name}' shadows import from '{source[0]}'", node)
                func = self._parse_function(node)
                functions.append(func)
                if func.builtin_decorator_key:
                    # @builtin_decorator stub signatures drive parse-time
                    # schema derivation for decorator argument validation
                    # (_schema_from_stub inspects param types). Resolve
                    # TypeRefNodes now so _schema_from_stub sees TpyType;
                    # sema's pre-pass will no-op on already-resolved fields.
                    # Module-level scope -- no enclosing type params.
                    self._finalize_function_refs(func, outer_scope=None)
                    # Register for decorator resolution (like @builtin_type for records)
                    # return_type may be None (no annotation) -- FunctionInfo
                    # here is used solely for decorator-key lookup; the value
                    # is not read for call resolution.
                    self.registry.register_function(FunctionInfo(
                        name=func.name, params=[], return_type=func.return_type,
                        builtin_decorator_key=func.builtin_decorator_key,
                    ))
                    # Derive arg schema from stub signature
                    schema = self._schema_from_stub(func)
                    if schema is not None:
                        self._decorator_schemas[func.builtin_decorator_key] = schema
            elif isinstance(node, ast.TypeAlias):
                seen_non_import = True
                self._register_type_alias(node.name.id, node.value, type_aliases)
            elif isinstance(node, ast.Assign) and self._is_type_alias_assign(node):
                seen_non_import = True
                self._register_type_alias(node.targets[0].id, node.value, type_aliases)
            else:
                # Skip docstrings and pass statements for late import detection
                if not self._is_ignorable_for_import_order(node):
                    seen_non_import = True
                # All other statements go through _parse_stmt (same as function bodies)
                stmt = self._parse_stmt(node)
                if isinstance(stmt, list):
                    top_level_stmts.extend(stmt)
                else:
                    top_level_stmts.append(stmt)

        return TpyModule(records=records, functions=functions, protocols=protocols, enums=enums, top_level_stmts=top_level_stmts, source_lines=self.source_lines, imports=imports, tpy_star_import=self._imports.tpy_star_import, star_imports=self._imports.star_imports, user_module_imports=user_module_imports, module_aliases=module_aliases, bare_module_imports=bare_module_imports, type_aliases=type_aliases, parse_warnings=self._warnings, recursive_union_names=self._recursive_union_names)

    def _is_protocol_base(self, base: ast.expr) -> bool:
        """Check if a base class expression refers to typing.Protocol."""
        return self._resolve_parser_keyword(base) == ("typing", "Protocol")

    def _is_enum_base(self, base: ast.expr) -> bool:
        """Check if a base class expression refers to enum.Enum."""
        return self._resolve_parser_keyword(base) == ("enum", "Enum")

    def _is_int_enum_base(self, base: ast.expr) -> bool:
        """Check if a base class expression refers to enum.IntEnum."""
        return self._resolve_parser_keyword(base) == ("enum", "IntEnum")

    def _is_typed_dict_base(self, base: ast.expr) -> bool:
        """Check if a base class expression refers to typing.TypedDict."""
        return self._resolve_parser_keyword(base) == ("typing", "TypedDict")

    def _parse_unpack_annotation(self, annotation: ast.expr, type_param_scope, error_node) -> 'TpyType | TypeRefNode':
        """Parse Unpack[TypedDict] annotation from **kwargs.

        Returns the inner TypedDict reference as a TypeRefNode; sema
        resolves it via `_resolve_pending_type_refs` and validates it
        is a TypedDict in registration.
        """
        if not isinstance(annotation, ast.Subscript):
            raise ParseError("**kwargs must have Unpack[TypedDict] annotation", error_node)
        resolved = self._resolve_parser_keyword(annotation.value)
        if resolved != ("typing", "Unpack"):
            raise ParseError("**kwargs must have Unpack[TypedDict] annotation", error_node)
        return self._parse_type_ref(annotation.slice, type_param_scope)

    # Valid integer mixin types for IntEnum: class P(int, Enum) or class P(Int8, Enum)
    _INT_MIXIN_TYPES: dict[str, str] = {
        "int": "int",
        "Int8": "Int8", "Int16": "Int16", "Int32": "Int32", "Int64": "Int64",
        "UInt8": "UInt8", "UInt16": "UInt16", "UInt32": "UInt32", "UInt64": "UInt64",
    }

    def _resolve_int_mixin(self, base: ast.expr) -> str | None:
        """Resolve a base class to an integer mixin type name, or None."""
        if isinstance(base, ast.Name):
            return self._INT_MIXIN_TYPES.get(base.id)
        return None

    # Sentinels for _resolve_decorator arg_value
    _EMPTY_CALL = object()  # @name() -- call with zero args
    _BAD_ARGS = object()    # @name(x, y) or non-constant -- caller must error

    def _resolve_decorator(self, dec: ast.expr) -> tuple[str, str, object] | None:
        """Resolve a decorator to (module, original_name, arg_value).

        Handles bare (@name), qualified (@mod.name), call-with-args (@name(arg)),
        and qualified-call (@mod.name(arg)) forms.

        arg_value meanings:
          None       -- bare form (@name)
          _EMPTY_CALL -- call with zero args (@name())
          _BAD_ARGS  -- invalid args (multiple or non-constant)
          <value>    -- single constant arg value (str, bool, int, etc.)

        Returns None if the decorator name cannot be resolved through imports.
        """
        # Extract the function node and args from Call decorators
        func_node = dec
        arg_value = None
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if dec.keywords:
                # @native("name", function=True) -- positional + keyword args
                if len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant):
                    kw_dict: dict[str, object] = {}
                    for kw in dec.keywords:
                        if isinstance(kw.value, ast.Constant):
                            kw_dict[kw.arg] = kw.value.value
                    arg_value = (dec.args[0].value, kw_dict)
                elif not dec.args:
                    # @type_param_default(T=int) -- kwargs only
                    kw_dict = {}
                    for kw in dec.keywords:
                        if isinstance(kw.value, ast.Constant):
                            kw_dict[kw.arg] = kw.value.value
                        elif isinstance(kw.value, ast.Name):
                            kw_dict[kw.arg] = _NameArg(kw.value.id)
                    arg_value = (None, kw_dict) if kw_dict else self._BAD_ARGS
                else:
                    arg_value = self._BAD_ARGS
            elif not dec.args:
                arg_value = self._EMPTY_CALL
            elif len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant):
                arg_value = dec.args[0].value
            elif len(dec.args) == 1 and isinstance(dec.args[0], ast.Name):
                # Name arg like @error_return(StopIteration) -- store as _NameArg
                arg_value = _NameArg(dec.args[0].id)
            else:
                arg_value = self._BAD_ARGS

        # Bare name: @name or @name(arg)
        if isinstance(func_node, ast.Name):
            name = func_node.id
            # @staticmethod and @property are Python builtins, not resolved through imports
            if name == "staticmethod":
                return ("builtins", "staticmethod", arg_value)
            if name == "property":
                return ("builtins", "property", arg_value)
            resolved = self._resolve_type_name(name)
            if resolved:
                return (resolved[0], resolved[1], arg_value)
            return None

        # Qualified name: @mod.name or @mod.name(arg)
        if isinstance(func_node, ast.Attribute) and isinstance(func_node.value, ast.Name):
            resolved = self._resolve_qualified_type_name(func_node)
            if resolved:
                return (resolved[0], resolved[1], arg_value)
            return None

        return None

    @staticmethod
    def _decorator_local_name(dec: ast.expr) -> str | None:
        """Extract the local name used in the source for a decorator (for error messages)."""
        func_node = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            return f"{func_node.value.id}.{func_node.attr}" if isinstance(func_node.value, ast.Name) else None
        return None

    @staticmethod
    def _decorator_raw_name(dec: ast.expr) -> str | None:
        """Extract the bare function name from a decorator AST node.

        Returns just the identifier (e.g. 'builtin_type' from both
        @builtin_type(...) and @mod.builtin_type(...)). Used by pre-scan
        to detect stdlib builtin decorators before import resolution.
        """
        func_node = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            return func_node.attr
        return None

    def _require_decorator(self, dec: ast.expr, context: str) -> tuple[str, object]:
        """Resolve a decorator or raise a helpful error. Returns (qname, arg)."""
        resolved = self._resolve_decorator(dec)
        if resolved is None:
            local_name = self._decorator_local_name(dec) or "?"
            raise ParseError(f"Unknown decorator '{local_name}' on {context}", dec)
        return self._qualify(resolved), resolved[2]

    def _parse_type_param_default(self, dec: ast.expr) -> dict[str, str]:
        """Parse @type_param_default(T=DefaultInt) -> {"T": "tpy.extern.DefaultInt"}."""
        if not isinstance(dec, ast.Call) or not dec.keywords:
            raise ParseError("@type_param_default() requires keyword arguments, e.g. @type_param_default(T=DefaultInt)", dec)
        result: dict[str, str] = {}
        for kw in dec.keywords:
            if not isinstance(kw.value, ast.Name):
                raise ParseError(f"@type_param_default({kw.arg}=...) value must be a type name", dec)
            name = kw.value.id
            resolved = self._resolve_type_name(name)
            if resolved is None:
                raise ParseError(
                    f"@type_param_default({kw.arg}={name}): unknown type '{name}'", dec)
            result[kw.arg] = self._qualify(resolved)
        return result

    def _parse_readonly_arg(self, arg: object, dec: ast.expr) -> tuple[bool, bool]:
        """Parse @readonly validated arg -> (is_readonly, readonly_opt_out).

        arg should be the validated positional value from _validate_decorator_args
        (None for bare/@readonly(), bool for @readonly(True/False)).
        """
        if arg is None:
            return (True, False)
        if isinstance(arg, bool):
            return (arg, not arg)
        raise ParseError("@readonly() requires a bool argument", dec)

    def _schema_from_stub(self, func: TpyFunction) -> _DecoratorArgSchema | None:
        """Derive a _DecoratorArgSchema from a @builtin_decorator stub's signature.

        Single param -> positional arg. Additional params with defaults -> kwargs.
        Type mapping: bool->bool, str->str, type->_NameArg (type name reference).
        """
        if not func.params:
            return _DecoratorArgSchema()  # bare only

        def _map_type(ptype: TpyType) -> tuple[type, str] | None:
            if is_bool_type(ptype):
                return (bool, "bool")
            if is_str_type(ptype):
                return (str, "str")
            td = type_def_of(ptype)
            if td is not None and td.qname == qnames.TYPE:
                return (_NameArg, "type name")
            return None

        # First param -> positional
        _, ptype = func.params[0]
        match = _map_type(ptype)
        if match is None:
            return None
        pos_type, pos_desc = match
        has_default = func.defaults and func.defaults[0] is not None

        # Additional params -> kwargs
        kwargs: dict[str, type] | None = None
        for i in range(1, len(func.params)):
            pname, ptype = func.params[i]
            match = _map_type(ptype)
            if match is None:
                return None
            if kwargs is None:
                kwargs = {}
            kwargs[pname] = match[0]

        return _DecoratorArgSchema(pos_type=pos_type, pos_required=not has_default,
                                   pos_description=pos_desc, kwargs=kwargs)

    def _validate_decorator_args(
        self, qname: str, arg: object, dec: ast.expr,
    ) -> tuple[object, dict[str, object]]:
        """Validate decorator args against schema. Returns (positional, kwargs).

        positional is the validated positional arg value, or None if not provided
        (both bare @name and empty @name() normalize to None for optional args).
        kwargs is a dict of validated keyword arg values (empty if none).
        """
        # Explicit schemas (for non-stub decorators like auto_readonly,
        # staticmethod) take precedence; then schemas derived from stubs.
        schema = _DECORATOR_ARG_SCHEMAS.get(qname) or self._decorator_schemas.get(qname)
        if schema is None:
            return (arg, {})
        dec_name = self._decorator_local_name(dec) or qname.rsplit(".", 1)[-1]

        # Handle tuple form: (positional, {kwargs}) from @native("name", function=True)
        pos_arg = arg
        raw_kwargs: dict[str, object] = {}
        if isinstance(arg, tuple) and len(arg) == 2 and isinstance(arg[1], dict):
            pos_arg, raw_kwargs = arg

        # Validate positional arg
        _fallback_desc = {str: "string", bool: "bool", _NameArg: "type name"}
        desc = schema.pos_description or _fallback_desc.get(schema.pos_type, "valid")
        if schema.pos_type is None:
            if pos_arg is not None:
                raise ParseError(f"@{dec_name} does not take arguments", dec)
        elif schema.pos_required:
            if not isinstance(pos_arg, schema.pos_type):
                raise ParseError(f"@{dec_name}() requires a {desc} argument", dec)
        else:
            if pos_arg is not None and pos_arg is not self._EMPTY_CALL and not isinstance(pos_arg, schema.pos_type):
                raise ParseError(f"@{dec_name}() requires a {desc} argument", dec)
            if pos_arg is self._EMPTY_CALL:
                pos_arg = None

        # Validate keyword args
        if raw_kwargs:
            if schema.kwargs is None:
                raise ParseError(f"@{dec_name} does not accept keyword arguments", dec)
            for key, val in raw_kwargs.items():
                if key not in schema.kwargs:
                    raise ParseError(f"@{dec_name}() got unexpected keyword argument '{key}'", dec)
                expected_type = schema.kwargs[key]
                if not isinstance(val, expected_type):
                    raise ParseError(f"@{dec_name}({key}=...) expects {expected_type.__name__}", dec)

        validated_kwargs = raw_kwargs if schema.kwargs else {}
        return (pos_arg, validated_kwargs)

    def _extract_decorator_kwargs(self, dec: ast.expr, arg: object, class_name: str) -> dict[str, Any]:
        """Extract keyword arguments from a macro decorator call.

        Handles bare ``@name``, empty ``@name()``, and ``@name(k=v, ...)``.
        Positional arguments are rejected. Kwarg values must be literals.
        """
        if arg is None or arg is self._EMPTY_CALL:
            return {}
        # kwargs-only tuple from _resolve_decorator: (None, {k: v, ...})
        if isinstance(arg, tuple) and len(arg) == 2 and arg[0] is None and isinstance(arg[1], dict):
            return arg[1]
        if arg is not self._BAD_ARGS:
            dec_name = self._decorator_local_name(dec) or "?"
            raise ParseError(f"@{dec_name} does not take positional arguments", dec)
        if not isinstance(dec, ast.Call):
            return {}
        if dec.args:
            dec_name = self._decorator_local_name(dec) or "?"
            raise ParseError(f"@{dec_name} does not take positional arguments", dec)
        result: dict[str, Any] = {}
        for kw in dec.keywords:
            if kw.arg is None:
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(f"@{dec_name} does not support **kwargs", dec)
            try:
                result[kw.arg] = ast.literal_eval(kw.value)
            except (ValueError, TypeError):
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(
                    f"@{dec_name}: keyword '{kw.arg}' must be a literal value", dec)
        return result

    def _resolve_call_import(self, call: TpyExpr, node: ast.expr) -> None:
        """Resolve import origin for a call expression and set resolved_import.

        Handles both ``field(...)`` (TpyCall) and ``dataclasses.field(...)``
        (TpyMethodCall) forms.
        """
        if not isinstance(node, ast.Call):
            return
        func = node.func
        source = None
        if isinstance(func, ast.Name):
            source = self._resolve_type_name(func.id)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            source = self._resolve_qualified_type_name(func)
        if source is not None:
            call.resolved_import = source

    def _parse_class(self, node: ast.ClassDef) -> TpyRecord | TpyProtocol | TpyEnum:
        """Parse a class definition as a record, protocol, or enum."""
        if node.bases:
            # Check for IntEnum first: class P(IntEnum) or class P(int, Enum)
            has_int_enum = any(self._is_int_enum_base(base) for base in node.bases)
            if has_int_enum:
                if len(node.bases) != 1:
                    raise ParseError(
                        "IntEnum must be the only base class", node)
                # IntEnum without mixin defaults to Int32 (not BigInt)
                return self._parse_enum(node, is_int_enum=True, underlying_type_name="Int32")

            # Check for mixin pattern: class P(int, Enum) or class P(Int8, Enum)
            has_enum = any(self._is_enum_base(base) for base in node.bases)
            if has_enum:
                if len(node.bases) == 2:
                    # Two bases: one must be Enum, the other an int mixin
                    mixin_type = None
                    for base in node.bases:
                        if not self._is_enum_base(base):
                            mixin_type = self._resolve_int_mixin(base)
                            if mixin_type is None:
                                base_name = base.id if isinstance(base, ast.Name) else ast.unparse(base)
                                raise ParseError(
                                    f"Invalid enum mixin type '{base_name}'; "
                                    f"expected int, Int8..Int64, or UInt8..UInt64",
                                    node)
                    if mixin_type is not None:
                        return self._parse_enum(
                            node, is_int_enum=True, underlying_type_name=mixin_type)
                elif len(node.bases) > 2:
                    raise ParseError(
                        "Enum class must have at most 2 base classes (mixin + Enum)", node)
                return self._parse_enum(node)
            # Check if this is a Protocol definition (has Protocol as one of its bases)
            has_protocol = any(self._is_protocol_base(base) for base in node.bases)
            if has_protocol:
                return self._parse_protocol(node)

        # Check if this is a TypedDict definition
        is_typed_dict = bool(node.bases) and any(self._is_typed_dict_base(base) for base in node.bases)
        is_total_false = False
        if is_typed_dict:
            non_td_bases = [b for b in node.bases if not self._is_typed_dict_base(b)]
            if non_td_bases:
                raise ParseError("TypedDict cannot have additional base classes", node)
            if node.decorator_list:
                raise ParseError("Decorators are not supported on TypedDict", node)
            if hasattr(node, 'type_params') and node.type_params:
                raise ParseError("Type parameters are not supported on TypedDict", node)
            # Parse total=False keyword
            for kw in node.keywords:
                if kw.arg == "total":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                        is_total_false = True
                    elif isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        pass  # total=True is the default
                    else:
                        raise ParseError("total must be True or False", node)
                else:
                    self._warnings.append(ParseWarning(
                        f"Unknown class keyword argument '{kw.arg}' on TypedDict",
                        self._loc(node)))

        # Warn on class keyword arguments for non-TypedDict classes
        if not is_typed_dict and node.keywords:
            for kw in node.keywords:
                self._warnings.append(ParseWarning(
                    f"Class keyword argument '{kw.arg}' is not supported",
                    self._loc(node)))

        # Parse record decorators (@native, @nocopy, macro decorators)
        linkage = RecordLinkage.DEFAULT
        native_name: str | None = None
        is_nocopy = False
        builtin_type_key: str | None = None
        pending_macros: list[tuple[str, dict[str, Any]]] = []
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"class '{node.name}'")
            if qname in self._RECORD_LINKAGE_MAP:
                pos, kw = self._validate_decorator_args(qname, arg, dec)
                new_linkage = self._RECORD_LINKAGE_MAP[qname]
                # binding="C" overrides linkage to C variant
                binding = kw.get("binding", "")
                if binding == "C":
                    if new_linkage == RecordLinkage.NATIVE:
                        new_linkage = RecordLinkage.NATIVE_C
                elif binding and binding != "":
                    raise ParseError(
                        f"@{qname.rsplit('.', 1)[-1]}(binding=...) only supports binding=\"C\"", dec)
                if linkage != RecordLinkage.DEFAULT:
                    raise ParseError(
                        f"Class '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                linkage = new_linkage
                native_name = pos
            elif qname == qnames.NOCOPY:
                self._validate_decorator_args(qname, arg, dec)
                is_nocopy = True
            elif qname == qnames.BUILTIN_TYPE:
                pos, kw = self._validate_decorator_args(qname, arg, dec)
                builtin_type_key = pos
            else:
                # Treat as a macro decorator -- extract kwargs and store for later
                macro_kwargs = self._extract_decorator_kwargs(dec, arg, node.name)
                pending_macros.append((qname, macro_kwargs))

        # Extract type parameters FIRST so they're in scope when parsing bases
        # Python 3.12+ syntax: class Foo[T, U]:
        # Also extract bounds: class Foo[T: Comparable]: or class Foo[N: int]:
        type_params = []
        type_param_kinds: list[TypeParamKind] = []
        type_param_bounds: dict[str, 'TpyType | TypeRefNode'] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                    if tp.bound is not None:
                        # Check for N: int syntax (integer type parameter)
                        if isinstance(tp.bound, ast.Name) and tp.bound.id == 'int':
                            type_param_kinds.append(TypeParamKind.INT)
                        else:
                            # Protocol bound -- resolution and protocol-shape
                            # validation deferred to sema.
                            type_param_kinds.append(TypeParamKind.TYPE)
                            type_param_bounds[tp.name] = self._parse_type_ref(tp.bound)
                    else:
                        type_param_kinds.append(TypeParamKind.TYPE)
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Create a dict of type param names to kinds for scope during parsing
        type_param_scope = dict(zip(type_params, type_param_kinds)) if type_params else None
        # Store scope for use during method body parsing (expression parsing uses this)
        old_scope = self._type_param_scope
        self._type_param_scope = type_param_scope

        # Parse base classes/protocols for inheritance. Classification
        # into parent class vs protocol happens above via
        # `_is_protocol_base` (keyword-matching, robust to shadowing
        # detection). TypedDict marker base is filtered out.
        #
        # Bases emit as TypeRefNode; sema re-resolves them in
        # `_resolve_pending_type_refs` under the record's type-param
        # scope.  Class-body checks that could mask base-resolution
        # errors (stub-body validation, @native decorator validation,
        # field-type inference failure) all run in sema, so no
        # parse-time base resolution side-effect is needed.
        bases: 'list[TpyType | TypeRefNode]' = []
        for base in node.bases:
            if is_typed_dict and self._is_typed_dict_base(base):
                continue
            ref = self._parse_type_ref(base, type_param_scope)
            bases.append(ref)

        fields = []
        methods = []
        nested_records: list[TpyRecord] = []
        nested_enums: list[TpyEnum] = []
        property_names: set[str] = set()
        # Save and extend nested type scope so short names resolve inside the class body
        old_nested_scope = self._nested_type_scope
        self._nested_type_scope = dict(old_nested_scope)

        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                # Field declaration: name: Type = default
                if not isinstance(item.target, ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.target.id
                # Emit a TypeRefNode.  Sema's `_resolve_pending_type_refs`
                # pre-pass resolves it before any reader consumes fld.type.
                field_type = self._parse_type_ref(item.annotation, type_param_scope)
                default_val = None
                default_expr = None
                if item.value is not None:
                    default_expr = self._parse_expr(item.value)
                    # Resolve import origin for call expressions (e.g. field() or dataclasses.field())
                    if isinstance(default_expr, (TpyCall, TpyMethodCall)):
                        self._resolve_call_import(default_expr, item.value)
                    default_val = self._get_default_value(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val, default_expr=default_expr, loc=self._loc(item)))
                if is_typed_dict and default_expr is not None:
                    self._warnings.append(ParseWarning(
                        f"TypedDict field '{field_name}' has a default value which is "
                        f"ignored by CPython at runtime; consider using total=False "
                        f"for optional fields",
                        self._loc(item),
                    ))
            elif isinstance(item, ast.Assign):
                # Field with inferred type: name = Int32(0)
                if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.targets[0].id
                # Emit a marker; sema's field-resolution pass runs the
                # inferrer on FieldInfo.default_expr, raising only on
                # genuine failure.  Keeping inference out of the parser
                # avoids coupling parser to the full typesys surface.
                field_type: 'TpyType | TypeRefNode | TpyInferFromDefaultRef' = TpyInferFromDefaultRef(loc=self._loc(item))
                default_val = self._get_default_value(item.value)
                default_expr = self._parse_expr(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val,
                                        default_expr=default_expr,
                                        loc=self._loc(item)))
            elif isinstance(item, ast.FunctionDef):
                if is_typed_dict:
                    raise ParseError(f"Methods are not allowed on TypedDict '{node.name}'", item)
                parsed = self._parse_method(item, node.name, type_param_scope, property_names)
                if parsed.is_property_getter:
                    property_names.add(parsed.name)
                    # Dual overloads (const + mutable) for correct reference
                    # semantics; sema.method_expansion performs the actual
                    # clone. Registration prunes the mutable clone for
                    # value-type returns.
                    parsed.auto_readonly = True
                methods.append(parsed)
            elif isinstance(item, ast.Pass):
                pass
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and item.value.value is ...:
                pass  # Ellipsis for opaque native types
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                pass  # Docstring
            elif isinstance(item, ast.ClassDef):
                if is_typed_dict:
                    raise ParseError(f"Nested classes are not allowed in TypedDict '{node.name}'", item)
                if linkage != RecordLinkage.DEFAULT:
                    raise ParseError(f"Nested classes are not allowed in @{linkage.value} classes", item)
                nested = self._parse_class(item)
                if isinstance(nested, TpyProtocol):
                    raise ParseError("Protocols cannot be nested inside classes", item)
                elif isinstance(nested, TpyEnum):
                    if type_params:
                        raise ParseError(
                            f"Nested enums are not supported inside generic classes "
                            f"('{node.name}' has type parameters)", item)
                    # Register immediately with dotted name so forward references
                    # within the same class body work (e.g., kind: Container.Kind)
                    dotted_name = f"{node.name}.{nested.name}"
                    self.registry.register_enum_placeholder(
                        dotted_name, module=self._public_module())
                    self._nested_type_scope[nested.name] = dotted_name
                    nested_enums.append(nested)
                else:
                    if type_params:
                        raise ParseError(
                            f"Nested classes are not supported inside generic classes "
                            f"('{node.name}' has type parameters)", item)
                    # Register immediately with dotted name for forward references
                    dotted_name = f"{node.name}.{nested.name}"
                    self.registry.register_record(RecordInfo(
                        name=dotted_name,
                        fields=nested.fields,
                        has_init=nested.init_method is not None,
                        module=self._public_module(),
                    ))
                    self._nested_type_scope[nested.name] = dotted_name
                    nested_records.append(nested)
            else:
                raise ParseError(f"Unsupported construct in class '{node.name}'", item)

        # Auto-declare fields from self.f = param assignments in __init__.
        # Phase 1: only for non-native classes without bases (inheritance
        # needs parent field info to avoid shadowing, not available at parse time).
        if not bases and linkage == RecordLinkage.DEFAULT:
            init_method = None
            for m in methods:
                if m.name == "__init__":
                    init_method = m
                    break
            if init_method is not None:
                new_fields = self._auto_declare_fields_from_init(init_method, fields, property_names)
                fields.extend(new_fields)
                # Reorder fields to match __init__ assignment order so that
                # C++ struct layout matches the init list (avoids -Wreorder).
                fields = self._reorder_fields_by_init(init_method, fields)

        # Method-linkage validation (stubs allowed/required per class
        # linkage, @native decorator restrictions) runs at sema time in
        # `_validate_record_method_linkage` after base resolution, so
        # base-resolution errors (e.g. "'Protocol' requires: from typing
        # import Protocol" on a class whose Protocol base is shadowed)
        # fire first instead of being masked here.

        # Restore scopes
        self._type_param_scope = old_scope
        self._nested_type_scope = old_nested_scope
        return TpyRecord(name=node.name, fields=fields, methods=methods, type_params=type_params, type_param_kinds=type_param_kinds, type_param_bounds=type_param_bounds, bases=bases, linkage=linkage, native_name=native_name, is_nocopy=is_nocopy, builtin_type_key=builtin_type_key, pending_macros=pending_macros, nested_records=nested_records, nested_enums=nested_enums, is_typed_dict=is_typed_dict, is_total_false=is_total_false, loc=self._loc(node))

    def _auto_declare_fields_from_init(
        self,
        init_method: TpyFunction,
        existing_fields: list[FieldInfo],
        property_names: set[str] | None = None,
    ) -> list[FieldInfo]:
        """Auto-declare fields from top-level `self.f = param` in __init__.

        For CPython compatibility: fields can be created by assignment in
        __init__ without requiring class-level annotations. Only handles
        the case where the RHS is a parameter name (type taken from param).
        """
        param_types = {name: typ for name, typ in init_method.params}
        existing_names = {fld.name for fld in existing_fields}

        new_fields: list[FieldInfo] = []
        for stmt in init_method.body:
            if not isinstance(stmt, TpyAssign):
                continue
            target = stmt.target
            if not isinstance(target, TpyFieldAccess):
                continue
            if not isinstance(target.obj, TpyName) or target.obj.name != "self":
                continue
            field_name = target.field
            if field_name in existing_names:
                continue
            if property_names and field_name in property_names:
                continue
            value = stmt.value
            if not isinstance(value, TpyName):
                continue
            if value.name not in param_types:
                continue
            new_fields.append(FieldInfo(field_name, param_types[value.name], loc=stmt.loc))
            existing_names.add(field_name)

        return new_fields

    @staticmethod
    def _reorder_fields_by_init(
        init_method: TpyFunction,
        fields: list[FieldInfo],
    ) -> list[FieldInfo]:
        """Reorder fields to match __init__ body assignment order.

        C++ initializes members in struct declaration order regardless of
        init-list order. Matching the two avoids -Wreorder-ctor warnings.
        Fields not assigned in __init__ are appended at the end.
        """
        # Collect field assignment order from __init__ top-level statements
        init_order: list[str] = []
        for stmt in init_method.body:
            if not isinstance(stmt, TpyAssign):
                continue
            target = stmt.target
            if not isinstance(target, TpyFieldAccess):
                continue
            if not isinstance(target.obj, TpyName) or target.obj.name != "self":
                continue
            if target.field not in init_order:
                init_order.append(target.field)

        field_map = {f.name: f for f in fields}
        seen: set[str] = set()
        ordered: list[FieldInfo] = []
        for name in init_order:
            if name in field_map and name not in seen:
                ordered.append(field_map[name])
                seen.add(name)
        for f in fields:
            if f.name not in seen:
                ordered.append(f)
        return ordered

    @staticmethod
    def _prefix_nested_names(record: TpyRecord, parent_name: str) -> None:
        """Prefix immediate nested types with parent_name, then recurse."""
        for nr in record.nested_records:
            nr.name = f"{parent_name}.{nr.name}"
            Parser._prefix_nested_names(nr, nr.name)
        for ne in record.nested_enums:
            ne.name = f"{parent_name}.{ne.name}"

    def _register_nested_types(self, record: TpyRecord) -> None:
        """Register all nested records and enums in the parser registry."""
        for nr in record.nested_records:
            self.registry.register_record(RecordInfo(
                name=nr.name,
                fields=nr.fields,
                has_init=nr.init_method is not None,
                module=self._public_module(),
            ))
            self._register_nested_types(nr)
        for ne in record.nested_enums:
            self.registry.register_enum_placeholder(
                ne.name, module=self._public_module())

    def _parse_protocol(self, node: ast.ClassDef) -> TpyProtocol:
        """Parse a protocol definition."""
        is_dynamic = False
        cpp_concept: str | None = None
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"protocol '{node.name}'")
            pos, kw = self._validate_decorator_args(qname, arg, dec)
            if qname == qnames.DYNAMIC:
                is_dynamic = True
                continue
            if qname == qnames.NATIVE:
                if not isinstance(pos, str):
                    raise ParseError("@native on protocol requires a C++ concept name string argument", dec)
                # Store the raw form; sema's `register_protocol` applies
                # `ensure_qualified()` when copying into ProtocolInfo.
                cpp_concept = pos
                continue
            dec_name = self._decorator_local_name(dec) or "?"
            raise ParseError(
                f"Unsupported decorator '@{dec_name}' on protocol '{node.name}'. "
                f"Only @dynamic (from tpy) and @native (from tpy.extern) are allowed on protocols", dec)
        if is_dynamic and cpp_concept is not None:
            raise ParseError("@dynamic and @native cannot be combined on a protocol", node)

        # Extract type parameters from Python 3.12+ syntax: class Foo[T](Protocol):
        # Note: Protocols don't support INT type params (only TYPE).
        # Set scope before parsing bases so generic parents like `Iterable[T]`
        # can reference the protocol's own type params.
        type_params = []
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                else:
                    raise ParseError(f"Only simple type parameters supported in protocols, got {type(tp).__name__}", node)

        # Set type param scope for parsing parent protocols and method signatures
        # (all TYPE kind for protocols).
        old_scope = self._type_param_scope
        self._type_param_scope = {tp: TypeParamKind.TYPE for tp in type_params} if type_params else None

        # Extract parent protocols (excluding Protocol itself). Both bare
        # (`Sized`) and generic (`Iterable[T]`) parents are accepted. Sema
        # resolves the TypeRefNode entries to NominalType under the protocol's
        # type-param scope (see resolve_refs.py).
        parent_protocols: 'list[NominalType | TypeRefNode]' = []
        for base in node.bases:
            if self._is_protocol_base(base):
                continue
            ref = self._parse_type_ref(base, self._type_param_scope)
            if not isinstance(ref, TpyTypeRef):
                raise ParseError(
                    f"Protocol parents must be simple type references "
                    f"(`Name` or `Name[...]`), got {type(ref).__name__}",
                    base
                )
            parent_protocols.append(ref)

        methods = []
        fields = []

        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                # Parse @readonly decorator
                is_readonly = False
                readonly_opt_out = False
                for dec in item.decorator_list:
                    qname, arg = self._require_decorator(dec, f"protocol method '{item.name}'")
                    pos, kw = self._validate_decorator_args(qname, arg, dec)
                    if qname == qnames.READONLY:
                        is_readonly, readonly_opt_out = self._parse_readonly_arg(pos, dec)
                    else:
                        dec_name = self._decorator_local_name(dec) or "?"
                        raise ParseError(f"Unknown decorator '{dec_name}' on protocol method '{item.name}'", dec)

                # Parse method signature (body should be ... or pass)
                params = []
                for i, arg in enumerate(item.args.args):
                    if i == 0:
                        if arg.arg != "self":
                            raise ParseError(f"First parameter of protocol method '{item.name}' must be 'self'", item)
                        continue
                    if arg.annotation is None:
                        raise ParseError(f"Protocol method parameter '{arg.arg}' must have type annotation", item)
                    param_type = self._parse_type_ref(arg.annotation)
                    params.append((arg.arg, param_type))

                # return_type=None means no annotation; sema's
                # `_resolve_pending_type_refs` substitutes VOID.
                return_type: 'TpyType | TypeRefNode | None' = None
                if item.returns:
                    return_type = self._parse_type_ref(item.returns)

                methods.append(MethodSignature(
                    name=item.name,
                    params=params,
                    return_type=return_type,
                    is_readonly=is_readonly,
                    readonly_opt_out=readonly_opt_out,
                ))
            elif isinstance(item, ast.AnnAssign):
                # Field declaration: name: Type
                if not isinstance(item.target, ast.Name):
                    raise ParseError("Invalid field declaration in protocol", item)
                field_name = item.target.id
                # Emit as TypeRefNode; sema resolves under the protocol's
                # type-param scope.
                field_type = self._parse_type_ref(item.annotation)
                fields.append((field_name, field_type))
            elif isinstance(item, ast.Pass):
                pass
            elif isinstance(item, ast.Expr):
                # Allow docstrings (string literals) and ... (Ellipsis)
                if isinstance(item.value, ast.Constant):
                    pass  # Docstring
                elif isinstance(item.value, ast.Ellipsis):
                    pass  # Ellipsis at class level
                else:
                    raise ParseError(f"Unexpected expression in protocol '{node.name}'", item)
            else:
                raise ParseError(f"Unsupported construct in protocol '{node.name}': {type(item).__name__}", item)

        # Restore the scope
        self._type_param_scope = old_scope
        return TpyProtocol(name=node.name, methods=methods, fields=fields, type_params=type_params, parent_protocols=parent_protocols, is_dynamic=is_dynamic, cpp_concept=cpp_concept, loc=self._loc(node))

    def _parse_enum(
        self, node: ast.ClassDef,
        is_int_enum: bool = False,
        underlying_type_name: str | None = None,
    ) -> TpyEnum:
        """Parse an enum class definition."""
        if node.decorator_list:
            raise ParseError(f"Decorators are not supported on enum '{node.name}'", node)

        members: list[tuple[str, int, SourceLocation | None]] = []
        has_auto = False
        has_explicit = False
        auto_value = 1  # auto() starts at 1, matching CPython

        for stmt in node.body:
            # Skip pass and docstrings
            if isinstance(stmt, ast.Pass):
                continue
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                continue

            if not isinstance(stmt, ast.Assign):
                raise ParseError(
                    f"Enum body must contain only member assignments (name = value), "
                    f"got {type(stmt).__name__}",
                    stmt,
                )
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise ParseError("Enum member must be a simple name = value assignment", stmt)

            member_name = stmt.targets[0].id
            value_node = stmt.value

            # Check for auto() call
            if isinstance(value_node, ast.Call):
                if self._resolve_parser_keyword(value_node.func) == ("enum", "auto"):
                    if has_explicit:
                        raise ParseError(
                            "Mixed auto() and explicit values are not yet supported; "
                            "use all auto() or all explicit values",
                            stmt,
                        )
                    has_auto = True
                    members.append((member_name, auto_value, self._loc(stmt)))
                    auto_value += 1
                    continue
                raise ParseError(
                    "Enum member value must be an integer literal or auto()", stmt)

            # Integer literal (positive)
            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, int) and not isinstance(value_node.value, bool):
                if has_auto:
                    raise ParseError(
                        "Mixed auto() and explicit values are not yet supported; "
                        "use all auto() or all explicit values",
                        stmt,
                    )
                has_explicit = True
                members.append((member_name, value_node.value, self._loc(stmt)))
            # Negative integer: -N
            elif (isinstance(value_node, ast.UnaryOp) and isinstance(value_node.op, ast.USub)
                    and isinstance(value_node.operand, ast.Constant)
                    and isinstance(value_node.operand.value, int)):
                if has_auto:
                    raise ParseError(
                        "Mixed auto() and explicit values are not yet supported; "
                        "use all auto() or all explicit values",
                        stmt,
                    )
                has_explicit = True
                members.append((member_name, -value_node.operand.value, self._loc(stmt)))
            else:
                raise ParseError(
                    "Enum member value must be an integer literal or auto()", stmt)

        if not members:
            raise ParseError(f"Enum '{node.name}' must have at least one member", node)

        return TpyEnum(
            name=node.name, members=members,
            is_int_enum=is_int_enum, underlying_type_name=underlying_type_name,
            loc=self._loc(node),
        )

    _RECORD_LINKAGE_MAP: dict[str, RecordLinkage] = {
        qnames.NATIVE: RecordLinkage.NATIVE,
    }

    _METHOD_LINKAGE_MAP: dict[str, FunctionLinkage] = {
        qnames.NATIVE: FunctionLinkage.NATIVE,
    }

    def _parse_method(self, node: ast.FunctionDef, class_name: str, type_param_scope: dict[str, TypeParamKind] | None = None, property_names: set[str] | None = None) -> TpyFunction:
        """Parse a method definition."""
        # Check decorators (@staticmethod, @readonly, @native("cpp_name"), @override)
        is_staticmethod = False
        is_readonly = False
        readonly_opt_out = False
        is_pure = False
        is_inline = False
        is_override = False
        is_overload_stub = False
        auto_readonly = False
        auto_readonly_dec = None
        error_return: str | None = None
        method_linkage = FunctionLinkage.DEFAULT
        native_name: str | None = None
        native_function: bool = False
        native_preserves_refs: bool = False
        cpp_template: str | None = None
        is_property_getter = False
        is_property_setter = False
        property_setter_name: str | None = None
        for dec in node.decorator_list:
            # Detect @prop_name.setter / @prop_name.deleter before general resolution
            func_node_check = dec.func if isinstance(dec, ast.Call) else dec
            if (isinstance(func_node_check, ast.Attribute)
                    and isinstance(func_node_check.value, ast.Name)
                    and property_names
                    and func_node_check.value.id in property_names):
                if func_node_check.attr == "setter":
                    is_property_setter = True
                    property_setter_name = func_node_check.value.id
                    continue
                if func_node_check.attr == "deleter":
                    raise ParseError(f"@property deleter is not supported", dec)
            qname, arg = self._require_decorator(dec, f"method '{node.name}'")
            pos, kw = self._validate_decorator_args(qname, arg, dec)
            if qname == qnames.STATICMETHOD:
                is_staticmethod = True
            elif qname == qnames.PROPERTY:
                is_property_getter = True
            elif qname == qnames.PURE:
                is_pure = True
            elif qname == qnames.INLINE:
                is_inline = True
            elif qname == qnames.OVERRIDE:
                is_override = True
            elif qname == qnames.OVERLOAD:
                is_overload_stub = True
            elif qname == qnames.READONLY:
                is_readonly, readonly_opt_out = self._parse_readonly_arg(pos, dec)
            elif qname == qnames.AUTO_READONLY:
                auto_readonly = True
                auto_readonly_dec = dec
            elif qname == qnames.ERROR_RETURN:
                error_return = pos.name
            elif qname == qnames.CPP_TEMPLATE:
                cpp_template = pos
            elif qname == qnames.NATIVE_PRESERVES_REFS:
                native_preserves_refs = True
            elif qname in self._METHOD_LINKAGE_MAP:
                method_linkage = self._METHOD_LINKAGE_MAP[qname]
                if isinstance(pos, tuple):
                    raise ParseError(
                        f"@{qname.rsplit('.', 1)[-1]}() decorator kwargs not parsed "
                        f"(schema unavailable -- ensure _bootstrap._extern is imported "
                        f"before modules that use decorator kwargs)", dec)
                # binding="C" overrides method linkage
                binding = kw.get("binding", "")
                if binding == "C" and method_linkage == FunctionLinkage.NATIVE:
                    method_linkage = FunctionLinkage.NATIVE_C
                elif binding and binding != "" and binding != "C":
                    raise ParseError(
                        f"@{qname.rsplit('.', 1)[-1]}(binding=...) only supports binding=\"C\"", dec)
                native_name = pos
                native_function = kw.get("function", False)
            else:
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(f"Unknown decorator '{dec_name}' on method '{node.name}'", dec)
        if is_override and is_staticmethod:
            raise ParseError(f"@override cannot be combined with @staticmethod on method '{node.name}'", node)
        if is_property_getter:
            if is_staticmethod:
                raise ParseError(f"@property cannot be combined with @staticmethod on method '{node.name}'", node)
            # Getter: only self param, must have return type
            params_without_self = [a for a in node.args.args if a.arg != "self"]
            if params_without_self:
                raise ParseError(f"@property getter '{node.name}' must take only 'self' parameter", node)
            if node.returns is None:
                raise ParseError(f"@property getter '{node.name}' must have a return type annotation", node)
        if is_property_setter:
            if is_staticmethod:
                raise ParseError(f"@property setter cannot be combined with @staticmethod on method '{node.name}'", node)
            # Setter: self + one value param
            params_without_self = [a for a in node.args.args if a.arg != "self"]
            if len(params_without_self) != 1:
                raise ParseError(f"@property setter '{node.name}' must take exactly one value parameter (plus self)", node)
        if auto_readonly:
            if is_readonly:
                raise ParseError(f"@auto_readonly cannot be combined with @readonly on method '{node.name}'", auto_readonly_dec)
            if is_staticmethod:
                raise ParseError(f"@auto_readonly cannot be combined with @staticmethod on method '{node.name}'", auto_readonly_dec)
            if node.name in ("__init__", "__del__"):
                raise ParseError(f"@auto_readonly is not valid on '{node.name}'", auto_readonly_dec)

        # Extract method-level type parameters (e.g. def foo[T](self, x: T) -> T:)
        method_type_params: list[str] = []
        method_type_param_bounds: dict[str, 'TpyType | TypeRefNode'] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    method_type_params.append(tp.name)
                    if tp.bound is not None:
                        # Protocol bound -- resolution + validation deferred to sema.
                        method_type_param_bounds[tp.name] = self._parse_type_ref(tp.bound)
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Early @auto_readonly + method type params guard. Sema catches
        # the self-annotation / per-param paths, but running this at parse
        # time keeps the diagnostic pinned to the decorator's line rather
        # than getting shadowed by later parse-time checks (stub bodies,
        # body validation, etc.).
        if auto_readonly_dec is not None and method_type_params:
            raise ParseError(
                f"auto_readonly on methods with method-level type parameters is not yet supported ('{node.name}')",
                auto_readonly_dec,
            )

        # Merge class-level and method-level type param scopes
        if method_type_params:
            merged_scope = dict(type_param_scope) if type_param_scope else {}
            for tp_name in method_type_params:
                merged_scope[tp_name] = TypeParamKind.TYPE
            type_param_scope = merged_scope

        params = []
        has_self = not is_staticmethod
        self_annotation = None  # Resolved self type; consumed by sema.method_expansion.
        # Count non-self params for __exit__ stripping check
        n_non_self = len(node.args.args) - (1 if has_self else 0)
        args_iter = iter(enumerate(node.args.args))
        for i, arg in args_iter:
            if i == 0 and has_self:
                # Non-static methods must have 'self' as first parameter
                if arg.arg != "self":
                    raise ParseError(f"First parameter of method '{node.name}' must be 'self'", node)
                # Emit the self annotation as a TypeRefNode; sema
                # resolves it in `_resolve_pending_type_refs` and
                # `method_expansion.expand_methods` validates the
                # shape + derives flags.
                if arg.annotation is not None:
                    self_annotation = self._parse_type_ref(arg.annotation, type_param_scope)
                continue
            # __exit__ exception params (exc_type, exc_val, exc_tb) are stripped --
            # they are always None in TPy (no general exceptions). This allows
            # CPython-compatible signatures without requiring type annotations.
            if node.name == "__exit__" and n_non_self == 3 and has_self:
                continue
            if arg.annotation is None:
                raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
            param_type = self._parse_type_ref(arg.annotation, type_param_scope)
            params.append((arg.arg, param_type))

        # __exit__ must have exactly 3 params (exc_type, exc_val, exc_tb) to
        # match CPython's context manager protocol.
        if node.name == "__exit__" and has_self and n_non_self != 3:
            raise ParseError(
                f"__exit__ must have 3 parameters: "
                f"__exit__(self, exc_type, exc_val, exc_tb)",
                node,
            )

        # Parse *args parameter
        vararg_name = None
        vararg_type = None
        if node.args.vararg is not None:
            va = node.args.vararg
            if is_overload_stub:
                raise ParseError("*args is not supported on @overload stubs", node)
            if va.annotation is None:
                raise ParseError(
                    f"*{va.arg} must have a type annotation (element type)", node)
            vararg_name = va.arg
            vararg_type = self._parse_type_ref(va.annotation, type_param_scope)

        # Parse keyword-only parameters (after * or *args)
        keyword_only_start = None
        if node.args.kwonlyargs:
            keyword_only_start = len(params)
            for arg in node.args.kwonlyargs:
                if arg.annotation is None:
                    raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
                param_type = self._parse_type_ref(arg.annotation, type_param_scope)
                params.append((arg.arg, param_type))

        # **kwargs: Unpack[TypedDict]
        kwarg_name = None
        kwarg_type = None
        if node.args.kwarg is not None:
            kwarg_node = node.args.kwarg
            if kwarg_node.annotation is None:
                raise ParseError("**kwargs must have Unpack[TypedDict] annotation", node)
            kwarg_type = self._parse_unpack_annotation(kwarg_node.annotation, type_param_scope, node)
            kwarg_name = kwarg_node.arg

        # Parse default parameter values (skip_self for non-static methods)
        defaults = self._parse_param_defaults(node, params, skip_self=has_self,
                                              type_param_scope=type_param_scope,
                                              kw_defaults=node.args.kw_defaults,
                                              n_kwonly=len(node.args.kwonlyargs))

        # Return type: None means no annotation; sema substitutes VOID.
        # __init__ is kept at None here (no annotation by construction);
        # sema treats it the same way.
        return_type: 'TpyType | TypeRefNode | None' = None
        if node.name != "__init__" and node.returns:
            return_type = self._parse_type_ref(node.returns, type_param_scope)

        if cpp_template is not None:
            if not self._is_stub_body(node.body):
                raise ParseError(
                    f"@cpp_template method '{node.name}' must have `...` body", node)
        is_stub_body = self._is_stub_body(node.body)
        is_overload_stub_body = is_stub_body or self._is_pass_body(node.body)
        is_stub = (is_stub_body and not is_overload_stub) or cpp_template is not None
        if is_overload_stub:
            # @overload methods may be bodyless (`...` / `pass`, paired with a
            # trailing impl) or carry their own body (self-contained overload
            # variant -- sema validates that a group is all-bodied or all-bodyless).
            body = [] if is_overload_stub_body else self._parse_body(node.body)
        elif is_stub:
            body = []
        else:
            body = self._parse_body(node.body)

        # Detect generator methods (yield in body)
        is_generator = _body_contains_yield(body)
        if is_generator:
            if node.name in ("__init__", "__del__"):
                raise ParseError(f"'{node.name}' cannot be a generator method", node)
            if is_staticmethod:
                raise ParseError(f"@staticmethod method '{node.name}' cannot be a generator", node)
            _check_no_return_value_in_generator(body, node.name)

        # __next__ methods implicitly get @error_return(StopIteration)
        if node.name == "__next__" and error_return is None:
            error_return = "StopIteration"

        method = TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_method=True,
            is_staticmethod=is_staticmethod,
            is_property_getter=is_property_getter,
            is_property_setter=is_property_setter,
            property_name=property_setter_name,
            is_inline=is_inline,
            is_readonly=is_readonly,
            readonly_opt_out=readonly_opt_out,
            is_pure=is_pure,
            has_auto_readonly_decorator=auto_readonly_dec is not None,
            is_override=is_override,
            is_overload_stub=is_overload_stub,
            is_stub=is_overload_stub_body if is_overload_stub else is_stub,
            linkage=method_linkage,
            native_name=native_name,
            native_function=native_function,
            native_preserves_refs=native_preserves_refs,
            cpp_template=cpp_template,
            type_params=method_type_params,
            type_param_bounds=method_type_param_bounds,
            defaults=defaults,
            keyword_only_start=keyword_only_start,
            vararg_name=vararg_name,
            vararg_type=vararg_type,
            kwarg_name=kwarg_name,
            kwarg_type=kwarg_type,
            error_return=error_return,
            is_generator=is_generator,
            self_annotation=self_annotation,
            loc=self._loc(node)
        )
        return method

    _FUNCTION_LINKAGE_MAP: dict[str, FunctionLinkage] = {
        qnames.NATIVE: FunctionLinkage.NATIVE,
        qnames.EXPORT: FunctionLinkage.EXPORT_C,
    }

    # User-visible decorator names for error messages
    _LINKAGE_DISPLAY_NAMES: dict[FunctionLinkage, str] = {
        FunctionLinkage.NATIVE: "native",
        FunctionLinkage.NATIVE_C: 'native(binding="C")',
        FunctionLinkage.EXPORT_C: "export",
    }

    def _parse_function(self, node: ast.FunctionDef) -> TpyFunction:
        """Parse a function definition."""
        is_noalloc = False
        is_inline = False
        is_readonly = False
        readonly_opt_out = False
        is_pure = False
        is_overload_stub = False
        value_ptr_coercion = False
        error_return: str | None = None
        builtin_decorator_key: str | None = None
        builtin_function_key: str | None = None
        type_param_defaults: dict[str, str] = {}
        linkage = FunctionLinkage.DEFAULT
        native_name: str | None = None
        cpp_template: str | None = None
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"function '{node.name}'")
            if qname == qnames.TYPE_PARAM_DEFAULT:
                type_param_defaults = self._parse_type_param_default(dec)
                continue
            pos, kw = self._validate_decorator_args(qname, arg, dec)
            if qname == qnames.NOALLOC:
                is_noalloc = True
            elif qname == qnames.INLINE:
                is_inline = True
            elif qname == qnames.PURE:
                is_pure = True
            elif qname == qnames.READONLY:
                is_readonly, readonly_opt_out = self._parse_readonly_arg(pos, dec)
            elif qname == qnames.AUTO_READONLY:
                raise ParseError("@auto_readonly is only valid on methods, not free functions", dec)
            elif qname == qnames.OVERLOAD:
                is_overload_stub = True
            elif qname == qnames.ERROR_RETURN:
                error_return = pos.name
            elif qname == qnames.VALUE_PTR_COERCION:
                value_ptr_coercion = True
            elif qname == qnames.CPP_TEMPLATE:
                cpp_template = pos
            elif qname == qnames.BUILTIN_DECORATOR:
                builtin_decorator_key = pos
            elif qname == qnames.BUILTIN_FUNCTION:
                builtin_function_key = pos
            elif qname in self._FUNCTION_LINKAGE_MAP:
                new_linkage = self._FUNCTION_LINKAGE_MAP[qname]
                if linkage != FunctionLinkage.DEFAULT:
                    old_name = self._LINKAGE_DISPLAY_NAMES.get(linkage, linkage.value)
                    new_name = self._LINKAGE_DISPLAY_NAMES.get(new_linkage, new_linkage.value)
                    raise ParseError(
                        f"Function '{node.name}' cannot have both @{old_name} and @{new_name}", node)
                if kw.get("function"):
                    dec_name = self._decorator_local_name(dec)
                    raise ParseError(f"@{dec_name}(function=...) is only valid on methods, not free functions", dec)
                # binding="C" overrides linkage to C variant
                binding = kw.get("binding", "")
                if binding == "C":
                    if new_linkage == FunctionLinkage.NATIVE:
                        new_linkage = FunctionLinkage.NATIVE_C
                elif binding and binding != "":
                    raise ParseError(
                        f"@{qname.rsplit('.', 1)[-1]}(binding=...) only supports binding=\"C\"", dec)
                # @export requires binding="C"
                if qname == qnames.EXPORT and binding != "C":
                    raise ParseError(
                        f"@export requires binding=\"C\"", dec)
                linkage = new_linkage
                if isinstance(pos, tuple):
                    raise ParseError(
                        f"@{qname.rsplit('.', 1)[-1]}() decorator kwargs not parsed "
                        f"(schema unavailable -- ensure _bootstrap._extern is imported "
                        f"before modules that use decorator kwargs)", dec)
                native_name = pos
            else:
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(f"Unknown decorator '{dec_name}' on function '{node.name}'", dec)

        # Extract type parameters from Python 3.12+ syntax: def foo[T, U]():
        # Bounds: def foo[T: Comparable](): (protocol bound)
        # INT params: def foo[T, N: int](): (integer type parameter, e.g. for Array[T, N])
        type_params = []
        type_param_bounds: dict[str, 'TpyType | TypeRefNode'] = {}
        type_param_kinds: list[TypeParamKind] = []
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                    if tp.bound is not None:
                        if isinstance(tp.bound, ast.Name) and tp.bound.id == 'int':
                            type_param_kinds.append(TypeParamKind.INT)
                        else:
                            type_param_kinds.append(TypeParamKind.TYPE)
                            # Protocol bound -- resolution + validation deferred to sema.
                            type_param_bounds[tp.name] = self._parse_type_ref(tp.bound)
                    else:
                        type_param_kinds.append(TypeParamKind.TYPE)
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Set scope for parsing parameter and return types
        type_param_scope = (
            {tp: kind for tp, kind in zip(type_params, type_param_kinds)}
            if type_params else None
        )
        old_scope = self._type_param_scope
        self._type_param_scope = type_param_scope

        params = []
        if builtin_function_key is not None:
            # @builtin_function stubs: params are illustrative only,
            # type annotations not required (sema handles everything)
            for arg in node.args.args:
                # @builtin_function params are illustrative stubs; sema
                # handles the real types specially.  Missing annotations
                # emit a `TpyTypeRef("None")` placeholder that the
                # resolver maps to VOID.
                param_type: 'TpyType | TypeRefNode' = (
                    self._parse_type_ref(arg.annotation, type_param_scope)
                    if arg.annotation
                    else TpyTypeRef(name="None", loc=self._loc(arg))
                )
                params.append((arg.arg, param_type))
        else:
            for arg in node.args.args:
                if arg.annotation is None:
                    raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
                param_type = self._parse_type_ref(arg.annotation, type_param_scope)
                params.append((arg.arg, param_type))

        # Parse *args parameter
        vararg_name = None
        vararg_type = None
        if node.args.vararg is not None:
            va = node.args.vararg
            if builtin_function_key is None:
                if is_overload_stub:
                    raise ParseError("*args is not supported on @overload stubs", node)
                if va.annotation is None:
                    raise ParseError(
                        f"*{va.arg} must have a type annotation (element type)", node)
                vararg_name = va.arg
                vararg_type = self._parse_type_ref(va.annotation, type_param_scope)

        # Parse keyword-only parameters (after * or *args)
        keyword_only_start = None
        if node.args.kwonlyargs:
            keyword_only_start = len(params)
            for arg in node.args.kwonlyargs:
                if arg.annotation is None:
                    raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
                param_type = self._parse_type_ref(arg.annotation, type_param_scope)
                params.append((arg.arg, param_type))
        elif vararg_name is None and node.args.vararg is not None:
            # bare * separator with no kwonlyargs -- unusual but valid Python
            pass
        # If there's a bare * (no vararg name but kwonlyargs exist), keyword_only_start is set above.
        # If there's *args, kwonlyargs after it are also keyword-only (set above).

        # **kwargs: Unpack[TypedDict]
        kwarg_name = None
        kwarg_type = None
        if node.args.kwarg is not None and builtin_function_key is None:
            kwarg_node = node.args.kwarg
            if kwarg_node.annotation is None:
                raise ParseError("**kwargs must have Unpack[TypedDict] annotation", node)
            kwarg_type = self._parse_unpack_annotation(kwarg_node.annotation, type_param_scope, node)
            kwarg_name = kwarg_node.arg

        # Parse default parameter values
        defaults = self._parse_param_defaults(node, params, skip_self=False,
                                              type_param_scope=type_param_scope,
                                              kw_defaults=node.args.kw_defaults,
                                              n_kwonly=len(node.args.kwonlyargs))

        # None means no annotation; sema substitutes VOID.
        return_type: 'TpyType | TypeRefNode | None' = None
        if node.returns:
            return_type = self._parse_type_ref(node.returns, type_param_scope)

        # Validate body vs linkage
        is_stub_body = self._is_stub_body(node.body)
        is_overload_stub_body = is_stub_body or self._is_pass_body(node.body)
        is_stub = False

        if builtin_decorator_key is not None:
            if not self._is_stub_body(node.body):
                raise ParseError(
                    f"@builtin_decorator function '{node.name}' must have `...` body", node)
            is_stub = True
            body = []
        elif builtin_function_key is not None:
            is_stub = True
            body = []
        elif cpp_template is not None:
            if not self._is_stub_body(node.body):
                raise ParseError(
                    f"@cpp_template function '{node.name}' must have `...` body", node)
            is_stub = True
            body = []
        elif is_overload_stub:
            # @overload functions may be bodyless (`...` / `pass`, paired with a
            # trailing impl) or carry their own body (self-contained overload
            # variant -- sema validates that a group is all-bodied or all-bodyless).
            body = [] if is_overload_stub_body else self._parse_body(node.body)
        elif linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C):
            if not (self._is_stub_body(node.body)):
                display = self._LINKAGE_DISPLAY_NAMES.get(linkage, linkage.value)
                raise ParseError(
                    f"@{display} function '{node.name}' must have `...` body (it declares an external symbol)",
                    node)
            is_stub = True
            body = []
        elif linkage == FunctionLinkage.EXPORT_C:
            if is_stub_body:
                raise ParseError(
                    f"@export function '{node.name}' must have a body (it exports a TPy function)",
                    node)
            body = self._parse_body(node.body)
        else:
            body = self._parse_body(node.body)

        # Restore the scope
        self._type_param_scope = old_scope

        is_generator = _body_contains_yield(body)
        if is_generator:
            # Validate: no 'return value' inside generator
            _check_no_return_value_in_generator(body, node.name)

        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_noalloc=is_noalloc,
            is_inline=is_inline,
            is_readonly=is_readonly,
            readonly_opt_out=readonly_opt_out,
            is_pure=is_pure,
            is_overload_stub=is_overload_stub,
            linkage=linkage,
            native_name=native_name,
            cpp_template=cpp_template,
            is_stub=is_overload_stub_body if is_overload_stub else is_stub,
            value_ptr_coercion=value_ptr_coercion,
            type_params=type_params,
            type_param_kinds=type_param_kinds,
            type_param_bounds=type_param_bounds,
            type_param_defaults=type_param_defaults,
            defaults=defaults,
            keyword_only_start=keyword_only_start,
            vararg_name=vararg_name,
            vararg_type=vararg_type,
            kwarg_name=kwarg_name,
            kwarg_type=kwarg_type,
            error_return=error_return,
            builtin_decorator_key=builtin_decorator_key,
            builtin_function_key=builtin_function_key,
            is_generator=is_generator,
            loc=self._loc(node)
        )

    def _is_stub_body(self, body: list[ast.stmt]) -> bool:
        """Check if a function body is a stub (only `...`).

        Only Ellipsis marks a declaration-only stub. `pass` is a valid
        no-op body that should still generate a definition.
        """
        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
                return True
        return False

    @staticmethod
    def _is_pass_body(body: list[ast.stmt]) -> bool:
        """Check if a function body is only `pass`."""
        return len(body) == 1 and isinstance(body[0], ast.Pass)

    def _parse_type_annotation(self, node: ast.expr, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyType:
        """Parse a type annotation to a TpyType.

        Thin wrapper around the walker + resolver composition; kept for
        the parse-time sites that need a resolved type immediately
        (type-parameter bounds, FragmentParser).
        """
        if type_param_scope is None:
            type_param_scope = self._type_param_scope
        ref = self._parse_type_ref(node, type_param_scope)
        return self._resolve_type_ref_impl(ref, type_param_scope)

    # ------------------------------------------------------------------
    # Type-reference walker
    #
    # `_parse_type_ref` mirrors `_parse_type_annotation`'s structural walk
    # but returns a TypeRefNode instead of a TpyType. It resolves names
    # only enough to disambiguate structural wrappers (Ptr/Own/Optional/
    # Callable/Fn/Literal/tuple/...) from user generics; leaf names
    # (primitives, user records, enums, protocols, type parameters) stay
    # raw in `TpyTypeRef.name` and are resolved by a later resolver pass.
    #
    # Validation errors that require resolved types (e.g. "Unknown type",
    # "Qualified name with missing module import", Own-in-union, readonly
    # normalization) are deliberately not raised here -- they belong in
    # the resolver.  Only errors provable from syntax alone (malformed
    # Callable/Fn/Literal shape) are raised at this layer.
    # ------------------------------------------------------------------

    def _parse_type_ref(
        self, node: ast.expr,
        type_param_scope: dict[str, TypeParamKind] | None = None,
    ) -> ResolverInputNode:
        """Walk an ast.expr for a type annotation and produce a ResolverInputNode
        (one of the four walker outputs: TpyTypeRef, TpyUnionRef,
        TpyCallableRef, TpyLiteralRef). TpyInferFromDefaultRef is emitted
        only at a single field-declaration site, never by this walker."""
        if type_param_scope is None:
            type_param_scope = self._type_param_scope
        loc = self._loc(node)

        if isinstance(node, ast.Name):
            name = node.id
            # Eager nested-scope substitution so refs emitted inside a class
            # body survive to sema-time resolution -- _nested_type_scope is
            # parse-time-only state, empty by the time sema runs. E.g.
            # referencing `Kind` inside `class Message: class Kind: ...`
            # emits TpyTypeRef("Message.Kind", ...) directly.
            if name in self._nested_type_scope:
                return TpyTypeRef(self._nested_type_scope[name], (), loc)
            return TpyTypeRef(name, (), loc)

        if isinstance(node, ast.Subscript):
            return self._parse_subscript_type_ref(node, type_param_scope, loc)

        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            cur: ast.expr = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                parts.reverse()
                return TpyTypeRef(".".join(parts), (), loc)
            raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

        if isinstance(node, ast.Constant) and node.value is None:
            return TpyTypeRef("None", (), loc)

        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            arms = _collect_bitor_arms(node)
            members = tuple(self._parse_type_ref(arm, type_param_scope) for arm in arms)
            return TpyUnionRef(members, loc)

        raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

    def _parse_subscript_type_ref(
        self, node: ast.Subscript,
        type_param_scope: dict[str, TypeParamKind] | None,
        loc: SourceLocation | None,
    ) -> ResolverInputNode:
        """Handle ast.Subscript: disambiguate structural wrappers from generics."""
        if isinstance(node.value, ast.Name):
            resolved = self._resolve_type_name(node.value.id)
            raw_name: str | None = node.value.id
        elif isinstance(node.value, ast.Attribute):
            resolved = self._resolve_qualified_type_name(node.value)
            # Build full dotted name from the Attribute chain so 3+ level
            # (a.b.c.D[T]) nested-class references reach the resolver's
            # _resolve_dotted_class_name_str path. 2-level stays identical to
            # the original "value.attr" form.
            parts: list[str] = []
            cur: ast.expr = node.value
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                parts.reverse()
                raw_name = ".".join(parts)
            else:
                raw_name = None
        else:
            raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

        # Canonical structural-wrapper names use a `:` separator so they
        # cannot collide with any Python identifier-based name (including
        # raw dotted user source like "typing.Optional" written without
        # `import typing`). The walker emits the canonical form only when
        # the name actually resolved to the expected module+name pair;
        # unresolved names fall through to the generic path with the raw
        # source form, and the resolver's unresolved-name errors fire.
        if resolved:
            module, original = resolved
            if module == "tpy":
                if original in ("Ptr", "Own", "readonly", "auto_readonly", "auto_own"):
                    inner_ref = self._parse_type_ref(node.slice, type_param_scope)
                    return TpyTypeRef(f"tpy:{original}", (inner_ref,), loc)
                if original == "Fn":
                    return self._parse_callable_type_ref(node, "Fn", type_param_scope, loc)
            elif module == "builtins":
                if original == "tuple":
                    slices = _extract_subscript_slices(node)
                    elem_refs = tuple(
                        self._parse_type_ref(s, type_param_scope) for s in slices
                    )
                    return TpyTypeRef("builtins:tuple", elem_refs, loc)
            elif module == "typing":
                if original == "Optional":
                    inner_ref = self._parse_type_ref(node.slice, type_param_scope)
                    return TpyTypeRef("typing:Optional", (inner_ref,), loc)
                if original == "Final":
                    inner_ref = self._parse_type_ref(node.slice, type_param_scope)
                    return TpyTypeRef("typing:Final", (inner_ref,), loc)
                if original == "ClassVar":
                    inner_ref = self._parse_type_ref(node.slice, type_param_scope)
                    return TpyTypeRef("typing:ClassVar", (inner_ref,), loc)
                if original == "Callable":
                    return self._parse_callable_type_ref(node, "Callable", type_param_scope, loc)
                if original == "Literal":
                    return self._parse_literal_type_ref(node, loc)

        # Not a structural wrapper: generic nominal reference. Resolver handles
        # the nominal lookup. Keep the raw source name; if the container was
        # import-resolved we still emit the source form (not the resolved
        # canonical one) to keep this pass syntactic.
        name = raw_name if raw_name is not None else (resolved[1] if resolved else None)
        if name is None:
            raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

        slices = _extract_subscript_slices(node)
        arg_refs: list[ResolverInputNode | int] = []
        for s in slices:
            if (isinstance(s, ast.Constant) and isinstance(s.value, int)
                    and not isinstance(s.value, bool)):
                arg_refs.append(s.value)
            else:
                arg_refs.append(self._parse_type_ref(s, type_param_scope))
        return TpyTypeRef(name, tuple(arg_refs), loc)

    def _parse_callable_type_ref(
        self, node: ast.Subscript, kind: str,
        type_param_scope: dict[str, TypeParamKind] | None,
        loc: SourceLocation | None,
    ) -> TpyCallableRef:
        slices = _extract_subscript_slices(node)
        if len(slices) != 2:
            raise ParseError(
                f"{kind} requires exactly 2 arguments: {kind}[[ParamTypes...], ReturnType]",
                node,
            )
        param_list_node, return_node = slices
        if not isinstance(param_list_node, ast.List):
            raise ParseError(
                f"{kind} parameter types must be a list: {kind}[[Int32, str], bool]",
                node,
            )
        params = tuple(
            self._parse_type_ref(p, type_param_scope) for p in param_list_node.elts
        )
        return_type = self._parse_type_ref(return_node, type_param_scope)
        return TpyCallableRef(kind=kind, params=params, return_type=return_type, loc=loc)

    def _parse_literal_type_ref(
        self, node: ast.Subscript, loc: SourceLocation | None,
    ) -> TpyLiteralRef:
        slices = _extract_subscript_slices(node)
        if not slices:
            raise ParseError("Literal requires at least one argument", node)
        values: list[LiteralValue] = []
        tag: str | None = None
        for s in slices:
            if isinstance(s, ast.Constant) and isinstance(s.value, str):
                if tag is not None and tag != "str":
                    raise ParseError("Literal cannot mix value types", node)
                tag = "str"
                values.append(LiteralValue("str", s.value))
            elif isinstance(s, ast.Constant) and isinstance(s.value, bool):
                if tag is not None and tag != "bool":
                    raise ParseError("Literal cannot mix value types", node)
                tag = "bool"
                values.append(LiteralValue("bool", s.value))
            elif isinstance(s, ast.Constant) and isinstance(s.value, int):
                if tag is not None and tag != "int":
                    raise ParseError("Literal cannot mix value types", node)
                tag = "int"
                values.append(LiteralValue("int", s.value))
            elif (isinstance(s, ast.UnaryOp) and isinstance(s.op, ast.USub)
                  and isinstance(s.operand, ast.Constant)
                  and isinstance(s.operand.value, int)
                  and not isinstance(s.operand.value, bool)):
                if tag is not None and tag != "int":
                    raise ParseError("Literal cannot mix value types", node)
                tag = "int"
                values.append(LiteralValue("int", -s.operand.value))
            else:
                raise ParseError(
                    "Literal supports string, int, and bool arguments", node)
        return TpyLiteralRef(values=tuple(values), loc=loc)

    def _resolve_type_ref_impl(
        self, ref: ResolverInputNode,
        type_param_scope: dict[str, TypeParamKind] | None = None,
    ) -> TpyType:
        """Thin delegate to `TypeResolver.resolve`.  Retained for the
        walker-vs-resolver equivalence tests and for macro-fragment
        self-annotation resolution, both of which need a TpyType in
        hand synchronously.
        """
        return self._resolver.resolve(ref, type_param_scope)

    def _finalize_function_refs(
        self, func: 'TpyFunction',
        outer_scope: dict[str, TypeParamKind] | None = None,
    ) -> None:
        """Resolve TypeRefNodes in a TpyFunction's params / return_type /
        vararg_type in place, using the current parser state.

        `outer_scope` is the enclosing type-param scope at the call site
        (e.g. a nested def's enclosing function scope). The function's own
        `type_params` are merged on top before resolution, so a method's
        or nested def's own generic params resolve to TypeParamRef
        correctly. Pass this explicitly rather than relying on
        `self._type_param_scope` being in the right state -- callers
        typically invoke this after `_parse_function` has already
        restored the enclosing scope on the parser instance.

        Used by non-top-level callers of `_parse_function` (nested defs,
        macro fragment parsing, @builtin_decorator stubs) that need
        TpyType immediately, without waiting for sema's
        `_resolve_pending_type_refs` pre-pass. Top-level functions leave
        refs in place for the sema pre-pass to resolve.
        """
        scope: dict[str, TypeParamKind] = dict(outer_scope or {})
        if func.type_params:
            kinds = func.type_param_kinds or []
            for i, name in enumerate(func.type_params):
                scope[name] = kinds[i] if i < len(kinds) else TypeParamKind.TYPE
        resolve_scope = scope or None

        ref_types = (TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef)
        new_params: list = []
        for name, t in func.params:
            if isinstance(t, ref_types):
                t = self._resolve_type_ref_impl(t, resolve_scope)
            new_params.append((name, t))
        func.params = new_params
        if func.return_type is None:
            # "No annotation" sentinel -- route through the resolver as
            # `TpyTypeRef("None")` so the same VOID singleton is
            # substituted without a direct typesys import.  The deferred
            # (sema-side) branch lives in
            # `_resolve_pending_type_refs._resolve_return_type`; both
            # paths converge on VOID.
            func.return_type = self._resolve_type_ref_impl(
                TpyTypeRef(name="None"), resolve_scope)
        elif isinstance(func.return_type, ref_types):
            func.return_type = self._resolve_type_ref_impl(
                func.return_type, resolve_scope)
        if isinstance(func.vararg_type, ref_types):
            func.vararg_type = self._resolve_type_ref_impl(
                func.vararg_type, resolve_scope)

    def _parse_type_args_from_subscript(self, node: ast.Subscript) -> 'tuple[TpyType | TypeRefNode | None, ...]':
        """Extract type arguments from a subscript for generic function calls like first[Int32](x).

        Raises ParseError if any element is structurally not a valid
        type (e.g. integer literal at a type-args site).  Name-resolution
        errors do not fire at parse time; elements emit as TypeRefNode
        and sema resolves in the pre-pass body walker.
        """
        slices = _extract_subscript_slices(node)

        # Parse each type argument - raise error if any is structurally invalid.
        type_args: 'list[TpyType | TypeRefNode | None]' = []
        for s in slices:
            # _ wildcard: infer this type argument
            if isinstance(s, ast.Name) and s.id == '_':
                type_args.append(None)
                continue
            # Integer constants are not valid type arguments
            if isinstance(s, ast.Constant) and isinstance(s.value, int):
                raise ParseError(f"Integer '{s.value}' is not a valid type argument", s)
            type_args.append(self._parse_type_ref(s))
        return tuple(type_args)

    def _parse_comprehension_generator(self, gen: ast.comprehension) -> TpyComprehensionGenerator:
        """Parse a single comprehension generator clause."""
        iterable = self._parse_expr(gen.iter)
        conditions = [self._parse_expr(c) for c in gen.ifs]
        if isinstance(gen.target, ast.Name):
            return TpyComprehensionGenerator(
                var=gen.target.id, iterable=iterable,
                conditions=conditions, unpack_vars=None)
        elif isinstance(gen.target, ast.Tuple):
            for elt in gen.target.elts:
                if not isinstance(elt, ast.Name):
                    raise ParseError(
                        "Comprehension unpacking targets must be simple variables", gen.target)
            unpack_vars: list[str | None] = [
                None if elt.id == "_" else elt.id  # type: ignore[union-attr]
                for elt in gen.target.elts
            ]
            return TpyComprehensionGenerator(
                var="__comp_tup", iterable=iterable,
                conditions=conditions, unpack_vars=unpack_vars)
        else:
            raise ParseError("Unsupported comprehension target", gen.target)

    def _try_parse_type_args(self, node: ast.Subscript) -> 'tuple[tuple[TpyType | TypeRefNode | None, ...], str | None]':
        """Try to parse type args from a subscript, capturing parse errors.

        Returns (type_args, parse_error).  On success parse_error is
        None.  On failure type_args is empty and parse_error holds the
        message.  Elements may be TypeRefNode pre-sema.
        """
        try:
            return self._parse_type_args_from_subscript(node), None
        except ParseError as e:
            return (), e.message

    def _parse_body(self, nodes: list[ast.stmt]) -> list[TpyStmt]:
        """Parse a list of statements, flattening any multi-statement expansions."""
        result: list[TpyStmt] = []
        for node in nodes:
            stmt = self._parse_stmt(node)
            if isinstance(stmt, list):
                result.extend(stmt)
            else:
                result.append(stmt)
        return result

    def _parse_stmt(self, node: ast.stmt) -> 'TpyStmt | list[TpyStmt]':
        """Parse a statement."""
        loc = self._loc(node)

        if isinstance(node, ast.AnnAssign):
            # Annotated assignment: x: T = expr
            if not isinstance(node.target, ast.Name):
                raise ParseError("Invalid assignment target", node)
            # Emit a TypeRefNode; sema's `_analyze_var_decl` resolves
            # it via `TypeOperations.resolve_type_ref` and writes the
            # resolved type back into stmt.type, so downstream code
            # sees TpyType.
            var_type_ref = self._parse_type_ref(node.annotation)
            init_expr = self._parse_expr(node.value) if node.value else None
            return TpyVarDecl(node.target.id, var_type_ref, init_expr, loc=loc)

        elif isinstance(node, ast.Assign):
            # Simple assignment: x = expr or x.field = expr
            if len(node.targets) > 1:
                return self._parse_multi_assign(node, loc)
            if isinstance(node.targets[0], ast.Tuple):
                elts = node.targets[0].elts
                if not elts:
                    raise ParseError("Empty tuple unpacking", node)
                targets: list[str | None] = []
                for elt in elts:
                    if not isinstance(elt, ast.Name):
                        raise ParseError(
                            "Tuple unpacking targets must be simple variable names", node)
                    targets.append(None if elt.id == "_" else elt.id)
                value = self._parse_expr(node.value)
                return TpyTupleUnpack(targets=targets, value=value, loc=loc)
            target = self._parse_expr(node.targets[0])
            value = self._parse_expr(node.value)
            # Check if this is a variable declaration (unannotated)
            if isinstance(target, TpyName):
                return TpyVarDecl(target.name, None, value, loc=loc)
            return TpyAssign(target, value, loc=loc)

        elif isinstance(node, ast.AugAssign):
            # Augmented assignment: x += expr
            target = self._parse_expr(node.target)
            value = self._parse_expr(node.value)
            op = self._binop_to_str(node.op)
            return TpyAugAssign(target, op, value, loc=loc)

        elif isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Yield):
                if node.value.value is None:
                    raise ParseError("'yield' must have a value in TurboPython", node)
                value = self._parse_expr(node.value.value)
                return TpyYield(value, loc=loc)
            return TpyExprStmt(self._parse_expr(node.value), loc=loc)

        elif isinstance(node, ast.Return):
            value = self._parse_expr(node.value) if node.value else None
            return TpyReturn(value, loc=loc)

        elif isinstance(node, ast.Assert):
            cond = self._parse_expr(node.test)
            msg = self._parse_expr(node.msg) if node.msg else None
            return TpyAssert(cond, msg, loc=loc)

        elif isinstance(node, ast.If):
            cond = self._parse_expr(node.test)
            then_body = self._parse_body(node.body)
            else_body = self._parse_body(node.orelse)
            return TpyIf(cond, then_body, else_body, loc=loc)

        elif isinstance(node, ast.While):
            cond = self._parse_expr(node.test)
            body = self._parse_body(node.body)
            orelse = self._parse_body(node.orelse)
            return TpyWhile(cond, body, orelse=orelse, loc=loc)

        elif isinstance(node, ast.For):
            orelse = self._parse_body(node.orelse)
            if isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if not isinstance(elt, ast.Name):
                        raise ParseError(
                            "For loop unpacking targets must be simple variables", node
                        )
                targets: list[str | None] = [
                    None if elt.id == "_" else elt.id  # type: ignore[union-attr]
                    for elt in node.target.elts
                ]
                synth_var = f"__for_tup_{self._for_unpack_counter}"
                self._for_unpack_counter += 1
                iterable = self._parse_expr(node.iter)
                body = self._parse_body(node.body)
                unpack = TpyTupleUnpack(
                    targets=targets,
                    value=TpyName(synth_var, loc=loc),
                    loc=loc,
                )
                return TpyForEach(synth_var, iterable, [unpack] + body, orelse=orelse, loc=loc, is_tuple_unpack=True)
            if not isinstance(node.target, ast.Name):
                raise ParseError("For loop target must be a simple variable", node)
            var = node.target.id
            body = self._parse_body(node.body)
            iterable = self._parse_expr(node.iter)
            return TpyForEach(var, iterable, body, orelse=orelse, loc=loc)

        elif isinstance(node, ast.Pass):
            return TpyPassStmt(loc=loc)

        elif isinstance(node, ast.Break):
            return TpyBreak(loc=loc)

        elif isinstance(node, ast.Continue):
            return TpyContinue(loc=loc)

        elif isinstance(node, ast.Global):
            return TpyGlobal(node.names, loc=loc)

        elif isinstance(node, ast.Raise):
            return self._parse_raise(node, loc)

        elif isinstance(node, ast.Delete):
            return self._parse_delete(node, loc)

        elif isinstance(node, ast.Match):
            return self._parse_match(node, loc)

        elif isinstance(node, ast.Try):
            return self._parse_try(node, loc)

        elif isinstance(node, ast.With):
            return self._parse_with(node, loc)

        elif isinstance(node, ast.Nonlocal):
            return TpyNonlocal(node.names, loc=loc)

        elif isinstance(node, ast.FunctionDef):
            return self._parse_nested_def(node, loc)

        else:
            raise ParseError(f"Unsupported statement: {type(node).__name__}", node)

    def _parse_raise(self, node: ast.Raise, loc: SourceLocation | None) -> TpyStmt:
        """Parse a raise statement: raise E, raise E(args), or bare raise."""
        exc = node.exc
        if exc is None:
            # Bare raise (re-raise) -- validated by sema to be inside except block
            return TpyRaise(loc=loc)
        # raise Name
        if isinstance(exc, ast.Name):
            name = exc.id
            return TpyRaise(exception_type=name, loc=loc)
        # raise Name() or raise Name(args...)
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            name = exc.func.id
            if exc.keywords:
                raise ParseError(f"'raise {name}' does not accept keyword arguments", node)
            args = [self._parse_expr(a) for a in exc.args]
            return TpyRaise(exception_type=name, args=args, is_call_form=True, loc=loc)
        # raise <expr> -- general expression (e.g. raise obj.make_err(), raise errors[i])
        return TpyRaise(raise_expr=self._parse_expr(exc), loc=loc)

    def _parse_try(self, node: ast.Try, loc: SourceLocation | None) -> TpyStmt:
        """Parse a try/except/else/finally statement."""
        if not node.handlers and not node.finalbody:
            raise ParseError("'try' requires at least one 'except' or 'finally' clause", node)
        handlers: list[TpyExceptHandler] = []
        for h in node.handlers:
            h_loc = self._loc(h) if hasattr(h, 'lineno') else loc
            if h.type is None:
                # Bare except: -- must be last handler (Python enforces this)
                exception_type = None
            else:
                exception_type = _attr_chain_to_dotted(h.type)
                if exception_type is None:
                    raise ParseError(
                        "'except' requires a simple or dotted name "
                        "(e.g. 'except MyError' or 'except pkg.MyError')", h)
            handlers.append(TpyExceptHandler(
                exception_type=exception_type, binding=h.name,
                body=self._parse_body(h.body), loc=h_loc))
        try_body = self._parse_body(node.body)
        else_body = self._parse_body(node.orelse)
        finally_body = self._parse_body(node.finalbody)
        return TpyTry(
            try_body=try_body,
            handlers=handlers,
            else_body=else_body,
            finally_body=finally_body,
            loc=loc,
        )

    def _parse_with(self, node: ast.With, loc: SourceLocation | None) -> TpyWith:
        """Parse a with statement."""
        items: list[TpyWithItem] = []
        for item in node.items:
            context_expr = self._parse_expr(item.context_expr)
            target: str | None = None
            if item.optional_vars is not None:
                if not isinstance(item.optional_vars, ast.Name):
                    raise ParseError(
                        "'with ... as' target must be a simple variable", node
                    )
                target = item.optional_vars.id
            item_loc = self._loc(item.context_expr)
            items.append(TpyWithItem(context_expr, target, loc=item_loc))
        body = self._parse_body(node.body)
        return TpyWith(items, body, loc=loc)

    def _parse_nested_def(self, node: ast.FunctionDef, loc: SourceLocation | None) -> TpyNestedDef:
        """Parse a nested function definition inside a function body."""
        if node.decorator_list:
            raise ParseError(
                f"Decorators are not supported on nested functions", node)
        if hasattr(node, 'type_params') and node.type_params:
            raise ParseError(
                f"Type parameters are not supported on nested functions", node)
        func = self._parse_function(node)
        # Nested defs live inside a function body, so sema's top-level
        # _resolve_pending_type_refs pre-pass doesn't see them. Resolve
        # refs immediately, passing the enclosing function's type-param
        # scope so outer generic params still resolve inside the nested
        # body. _parse_function restored self._type_param_scope to the
        # enclosing value before returning.
        self._finalize_function_refs(func, outer_scope=self._type_param_scope)
        return TpyNestedDef(func=func, loc=loc)

    def _parse_multi_assign(self, node: ast.Assign,
                            loc: SourceLocation | None) -> list[TpyStmt]:
        """Desugar multi-target assignment: a = b = c = expr.

        Uses a name target as anchor so the value is evaluated exactly once.
        All other targets reference the anchor. If no name target exists,
        a synthetic temp is introduced.
        """
        for t in node.targets:
            if isinstance(t, ast.Tuple):
                raise ParseError(
                    "Tuple unpacking not supported in multiple assignment", node)
        value_expr = self._parse_expr(node.value)
        # Find rightmost Name target to use as anchor
        anchor: ast.Name | None = None
        for t in reversed(node.targets):
            if isinstance(t, ast.Name):
                anchor = t
                break
        stmts: list[TpyStmt] = []
        if anchor is not None:
            anchor_name = anchor.id
            stmts.append(TpyVarDecl(anchor_name, None, value_expr, loc=loc))
        else:
            # No name target -- introduce synthetic temp
            anchor_name = f"__ma_{self._multi_assign_counter}"
            self._multi_assign_counter += 1
            stmts.append(TpyVarDecl(anchor_name, None, value_expr, loc=loc))
        # Assign anchor to remaining targets (left-to-right, no source comment)
        for t in node.targets:
            if t is anchor:
                continue
            target = self._parse_expr(t)
            ref = TpyName(anchor_name, loc=loc)
            if isinstance(target, TpyName):
                stmts.append(TpyVarDecl(target.name, None, ref, loc=None))
            else:
                stmts.append(TpyAssign(target, ref, loc=None))
        return stmts

    def _parse_delete(self, node: ast.Delete, loc: SourceLocation | None) -> TpyStmt:
        """Parse a del statement. Supports subscript and variable targets."""
        subscripts: list[TpySubscript] = []
        names: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                obj = self._parse_expr(target.value)
                index = self._parse_expr(target.slice)
                subscripts.append(TpySubscript(obj, index, loc=loc))
            elif isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Attribute):
                raise ParseError("'del' on attributes is not supported", node)
            else:
                raise ParseError(f"Unsupported del target: {type(target).__name__}", node)
        if subscripts and names:
            raise ParseError("Cannot mix variable and subscript targets in a single 'del' statement", node)
        if names:
            return TpyDelVar(names, loc=loc)
        return TpyDelItem(subscripts, loc=loc)

    def _parse_match(self, node: ast.Match, loc: SourceLocation | None) -> TpyMatch:
        """Parse a match/case statement."""
        subject = self._parse_expr(node.subject)
        cases: list[TpyMatchCase] = []
        for case in node.cases:
            pattern = self._parse_pattern(case.pattern)
            guard = self._parse_expr(case.guard) if case.guard else None
            body = self._parse_body(case.body)
            # match_case nodes lack lineno; use the pattern's location instead
            cases.append(TpyMatchCase(pattern, guard, body, loc=self._loc(case.pattern)))
        return TpyMatch(subject, cases, loc=loc)

    def _parse_pattern(self, node: ast.pattern) -> TpyPattern:
        """Parse a match/case pattern."""
        loc = self._loc(node)

        if isinstance(node, ast.MatchAs):
            if node.pattern is None and node.name is None:
                return TpyWildcardPattern(loc=loc)
            if node.pattern is None and node.name is not None:
                return TpyCapturePattern(node.name, loc=loc)
            if node.pattern is not None and node.name is not None:
                inner = self._parse_pattern(node.pattern)
                return TpyAsPattern(inner, node.name, loc=loc)

        elif isinstance(node, ast.MatchClass):
            cls = self._parse_expr(node.cls)
            positional = [self._parse_pattern(p) for p in node.patterns]
            keywords = [
                (attr, self._parse_pattern(pat))
                for attr, pat in zip(node.kwd_attrs, node.kwd_patterns)
            ]
            return TpyClassPattern(cls, positional, keywords, loc=loc)

        elif isinstance(node, ast.MatchValue):
            if isinstance(node.value, ast.Constant):
                return TpyLiteralPattern(node.value.value, loc=loc)
            if isinstance(node.value, ast.Attribute):
                expr = self._parse_expr(node.value)
                return TpyValuePattern(expr, loc=loc)
            raise ParseError("Unsupported match value pattern", node)

        elif isinstance(node, ast.MatchSingleton):
            return TpyLiteralPattern(node.value, loc=loc)

        elif isinstance(node, ast.MatchOr):
            patterns = [self._parse_pattern(p) for p in node.patterns]
            return TpyOrPattern(patterns, loc=loc)

        raise ParseError(f"Unsupported pattern: {type(node).__name__}", node)

    def _parse_expr(self, node: ast.expr) -> TpyExpr:
        """Parse an expression."""
        loc = self._loc(node)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return TpyBoolLiteral(node.value, loc=loc)
            elif isinstance(node.value, int):
                return TpyIntLiteral(node.value, loc=loc)
            elif isinstance(node.value, float):
                return TpyFloatLiteral(node.value, loc=loc)
            elif isinstance(node.value, str):
                return TpyStrLiteral(node.value, loc=loc)
            elif isinstance(node.value, bytes):
                return TpyBytesLiteral(node.value, loc=loc)
            elif node.value is None:
                return TpyNoneLiteral(loc=loc)
            else:
                raise ParseError(f"Unsupported literal type: {type(node.value).__name__}", node)

        elif isinstance(node, ast.Name):
            if node.id in self.FORBIDDEN_CONSTRUCTS:
                raise ParseError(f"'{node.id}' is not allowed in TurboPython", node)
            return TpyName(node.id, loc=loc)

        elif isinstance(node, ast.BinOp):
            # Handle list repetition: [x, y, ...] * N -> TpyListRepeat
            if isinstance(node.op, ast.Mult) and isinstance(node.left, ast.List):
                elements = [self._parse_expr(e) for e in node.left.elts]
                # Empty list: [] * N -> [] (collapse to empty array literal)
                if not elements:
                    return TpyArrayLiteral([], loc=loc)
                count = self._parse_expr(node.right)
                return TpyListRepeat(elements, count, loc=loc)
            left = self._parse_expr(node.left)
            right = self._parse_expr(node.right)
            op = self._binop_to_str(node.op)
            return TpyBinOp(left, op, right, loc=loc)

        elif isinstance(node, ast.Compare):
            left = self._parse_expr(node.left)
            if len(node.ops) == 1:
                right = self._parse_expr(node.comparators[0])
                op = self._cmpop_to_str(node.ops[0])
                return TpyBinOp(left, op, right, loc=loc)
            # Chained comparison: a < b < c
            ops = []
            for ast_op in node.ops:
                op = self._cmpop_to_str(ast_op)
                if op in ("is", "is not", "in", "not in"):
                    raise ParseError(
                        f"'{op}' cannot be used in chained comparisons", node)
                ops.append(op)
            comparators = [self._parse_expr(c) for c in node.comparators]
            return TpyChainedCompare(left, ops, comparators, loc=loc)

        elif isinstance(node, ast.UnaryOp):
            operand = self._parse_expr(node.operand)
            op = self._unaryop_to_str(node.op)
            return TpyUnaryOp(op, operand, loc=loc)

        elif isinstance(node, ast.BoolOp):
            # Handle 'and' / 'or' - chain as binary ops
            op = "&&" if isinstance(node.op, ast.And) else "||"
            result = self._parse_expr(node.values[0])
            for val in node.values[1:]:
                result = TpyBinOp(result, op, self._parse_expr(val), loc=loc)
            return result

        elif isinstance(node, ast.Call):
            args = []
            for a in node.args:
                if isinstance(a, ast.Starred):
                    args.append(TpyStarUnpack(
                        expr=self._parse_expr(a.value), loc=self._loc(a)))
                else:
                    args.append(self._parse_expr(a))
            kwargs = {}
            double_star_unpack = None

            if node.keywords:
                for kw in node.keywords:
                    if kw.arg is None:
                        # **expr unpacking
                        if double_star_unpack is not None:
                            raise ParseError("Only one **expr unpacking allowed per call", node)
                        if kwargs:
                            raise ParseError("**expr unpacking cannot be mixed with keyword arguments", node)
                        double_star_unpack = self._parse_expr(kw.value)
                    else:
                        if double_star_unpack is not None:
                            raise ParseError("**expr unpacking cannot be mixed with keyword arguments", node)
                        if kw.arg in kwargs:
                            raise ParseError(f"Keyword argument '{kw.arg}' repeated", node)
                        kwargs[kw.arg] = self._parse_expr(kw.value)

            if isinstance(node.func, ast.Name):
                call = TpyCall(TpyName(node.func.id, loc=loc), args, kwargs=kwargs,
                               double_star_unpack=double_star_unpack, loc=loc)
                # Set resolved_import so downstream passes (e.g. the
                # builder-trace expander) can identify imported callables.
                self._resolve_call_import(call, node)
                return call
            elif isinstance(node.func, ast.Attribute):
                # ClassName[TypeArgs].method(args) -- static call with explicit class type args
                if (isinstance(node.func.value, ast.Subscript)
                        and isinstance(node.func.value.value, ast.Name)
                        and self._could_be_type(node.func.value.value.id)):
                    name = node.func.value.value.id
                    type_args, type_args_parse_error = self._try_parse_type_args(node.func.value)
                    if type_args or type_args_parse_error:
                        return TpyMethodCall(
                            TpyName(name, loc=loc), node.func.attr, args,
                            kwargs=kwargs, double_star_unpack=double_star_unpack,
                            type_args=type_args, type_args_parse_error=type_args_parse_error,
                            loc=loc,
                        )
                    # No type args and no error: fall through (e.g., variable[index].method())
                obj = self._parse_expr(node.func.value)
                mcall = TpyMethodCall(obj, node.func.attr, args, kwargs=kwargs,
                                      double_star_unpack=double_star_unpack, loc=loc)
                # Set resolved_import for qualified module calls
                # (``mod.func()``) so downstream passes -- e.g. the
                # builder-trace expander -- can identify imported
                # callables without re-walking the import table.
                self._resolve_call_import(mcall, node)
                return mcall
            elif isinstance(node.func, ast.Subscript):
                # Could be generic type instantiation (Stack[Int32]()) or generic function call (First[Int32](x))
                # Parse both call_type and type_args - sema decides which applies based on whether
                # the name is a record or a function
                if isinstance(node.func.value, ast.Name):
                    name = node.func.value.id
                    # Try to extract type_args for potential generic function call
                    # (for type instantiations like Array[Int32, 8], non-type args are valid
                    # so parse error is stored and sema decides whether to report it)
                    type_args, type_args_parse_error = self._try_parse_type_args(node.func)
                    # Try to parse as a type annotation ref (for type
                    # instantiation like ArrayList[Int32]()). Sema
                    # decides whether to use call_type or type_args
                    # based on whether the name resolves to a type or a
                    # function. Emits TypeRefNode; sema resolves in the
                    # pre-pass body walker. try/except catches
                    # structural parse errors (Callable/Fn/Literal
                    # shape); name-resolution errors defer to sema,
                    # which catches them per-call and falls back to
                    # type_args / subscript_callee just like the
                    # pre-flip behaviour.
                    call_type = None
                    if self._could_be_type(name):
                        try:
                            call_type = self._parse_type_ref(node.func)
                        except ParseError:
                            pass
                    # Try to parse the subscript as an expression so sema can
                    # fall back to expression callee when the name turns out to
                    # be a variable, not a function/type (e.g., fns[0](args),
                    # Handlers[MyType](args)). May fail for known generic types
                    # (list[T], Array[T,N], etc. can't be used as values).
                    # Skip slices -- they can't produce a callable value.
                    subscript_callee = None
                    if not isinstance(node.func.slice, ast.Slice):
                        try:
                            subscript_callee = self._parse_expr(node.func)
                        except ParseError:
                            pass
                    return TpyCall(TpyName(name, loc=loc), args, call_type=call_type, type_args=type_args,
                                   type_args_parse_error=type_args_parse_error,
                                   subscript_callee=subscript_callee, kwargs=kwargs,
                                   double_star_unpack=double_star_unpack, loc=loc)
                elif isinstance(node.func.value, ast.Attribute):
                    # module.func[T](args) -- method call with explicit type args
                    obj = self._parse_expr(node.func.value.value)
                    method = node.func.value.attr
                    type_args, type_args_parse_error = self._try_parse_type_args(node.func)
                    return TpyMethodCall(obj, method, args, kwargs=kwargs,
                                         double_star_unpack=double_star_unpack,
                                         type_args=type_args,
                                         type_args_parse_error=type_args_parse_error, loc=loc)
                # Expression callee with subscript: expr[i](args)
                expr_func = self._parse_expr(node.func)
                return TpyCall(expr_func, args, kwargs=kwargs,
                               double_star_unpack=double_star_unpack, loc=loc)
            else:
                # Expression callee: f()(x), (lambda: fn)()(), etc.
                expr_func = self._parse_expr(node.func)
                return TpyCall(expr_func, args, kwargs=kwargs,
                               double_star_unpack=double_star_unpack, loc=loc)

        elif isinstance(node, ast.Attribute):
            obj = self._parse_expr(node.value)
            return TpyFieldAccess(obj, node.attr, loc=loc)

        elif isinstance(node, ast.List):
            elements = [self._parse_expr(elt) for elt in node.elts]
            return TpyArrayLiteral(elements=elements, loc=loc)

        elif isinstance(node, ast.ListComp):
            if len(node.generators) != 1:
                raise ParseError("Nested comprehensions not yet supported", node)
            gen = node.generators[0]
            if gen.is_async:
                raise ParseError("Async comprehensions not yet supported", node)
            generator = self._parse_comprehension_generator(gen)
            element_expr = self._parse_expr(node.elt)
            return TpyListComprehension(element_expr, generator, loc=loc)

        elif isinstance(node, ast.DictComp):
            if len(node.generators) != 1:
                raise ParseError("Nested comprehensions not yet supported", node)
            gen = node.generators[0]
            if gen.is_async:
                raise ParseError("Async comprehensions not yet supported", node)
            generator = self._parse_comprehension_generator(gen)
            key_expr = self._parse_expr(node.key)
            value_expr = self._parse_expr(node.value)
            return TpyDictComprehension(key_expr, value_expr, generator, loc=loc)

        elif isinstance(node, ast.Dict):
            if any(k is None for k in node.keys):
                raise ParseError("Dict unpacking (**) is not supported", node)
            keys = [self._parse_expr(k) for k in node.keys]
            values = [self._parse_expr(v) for v in node.values]
            return TpyDictLiteral(keys=keys, values=values, loc=loc)

        elif isinstance(node, ast.SetComp):
            if len(node.generators) != 1:
                raise ParseError("Nested comprehensions not yet supported", node)
            gen = node.generators[0]
            if gen.is_async:
                raise ParseError("Async comprehensions not yet supported", node)
            generator = self._parse_comprehension_generator(gen)
            element_expr = self._parse_expr(node.elt)
            return TpySetComprehension(element_expr, generator, loc=loc)

        elif isinstance(node, ast.GeneratorExp):
            if len(node.generators) != 1:
                raise ParseError("Nested comprehensions not yet supported", node)
            gen = node.generators[0]
            if gen.is_async:
                raise ParseError("Async comprehensions not yet supported", node)
            generator = self._parse_comprehension_generator(gen)
            element_expr = self._parse_expr(node.elt)
            return TpyGeneratorExpression(element_expr, generator, loc=loc)

        elif isinstance(node, ast.Set):
            elements = [self._parse_expr(e) for e in node.elts]
            return TpySetLiteral(elements=elements, loc=loc)

        elif isinstance(node, ast.Subscript):
            # Subscript can be indexing (values[i]) or type annotation (Array[T, N])
            # If the value is a name that's a known generic type, it's a type annotation context
            # Otherwise, it's indexing
            if isinstance(node.value, ast.Name):
                name = node.value.id
                # Type constructors that only exist in type annotations
                if name in ("Own", "Fn"):
                    raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
                # Module-defined generic types
                if find_factory_by_simple_name(name) is not None:
                    raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
                # Generic types from imported builtin submodules (tpy.mem, etc.)
                if import_src := self._imports.get_import_source(name):
                    if find_factory_in_module(import_src[1], import_src[0]) is not None:
                        raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
            obj = self._parse_expr(node.value)
            if isinstance(node.slice, ast.Slice):
                sl = node.slice
                lower = self._parse_expr(sl.lower) if sl.lower is not None else None
                upper = self._parse_expr(sl.upper) if sl.upper is not None else None
                step = self._parse_expr(sl.step) if sl.step is not None else None
                index = TpySlice(lower=lower, upper=upper, step=step, loc=loc)
            else:
                index = self._parse_expr(node.slice)
            return TpySubscript(obj=obj, index=index, loc=loc)

        elif isinstance(node, ast.Tuple):
            if not node.elts:
                raise ParseError("Empty tuple literal is not supported", node)
            elements = [self._parse_expr(elt) for elt in node.elts]
            return TpyTupleLiteral(elements=elements, loc=loc)

        elif isinstance(node, ast.JoinedStr):
            return self._parse_fstring(node, loc)

        elif isinstance(node, ast.IfExp):
            condition = self._parse_expr(node.test)
            then_expr = self._parse_expr(node.body)
            else_expr = self._parse_expr(node.orelse)
            return TpyIfExpr(condition=condition, then_expr=then_expr,
                             else_expr=else_expr, loc=loc)

        elif isinstance(node, ast.NamedExpr):
            target = node.target.id
            value = self._parse_expr(node.value)
            return TpyNamedExpr(target=target, value=value, loc=loc)

        elif isinstance(node, ast.Lambda):
            if node.args.vararg or node.args.kwarg:
                raise ParseError("*args and **kwargs are not supported in lambda", node)
            if node.args.kwonlyargs or node.args.posonlyargs:
                raise ParseError(
                    "Keyword-only and positional-only parameters are not supported in lambda", node)
            if any(d is not None for d in node.args.defaults) or node.args.kw_defaults:
                raise ParseError("Default arguments are not supported in lambda", node)
            for arg in node.args.args:
                if arg.annotation is not None:
                    raise ParseError(
                        "Type annotations on lambda parameters are not supported; "
                        "types are inferred from context", node)
            param_names = [arg.arg for arg in node.args.args]
            body = self._parse_expr(node.body)
            return TpyLambda(param_names=param_names, body=body, loc=loc)

        elif isinstance(node, ast.Yield):
            raise ParseError(
                "'yield' is only allowed as a statement, not in expression context", node)

        elif isinstance(node, ast.YieldFrom):
            raise ParseError(
                "'yield from' is not yet supported in TurboPython", node)

        else:
            raise ParseError(f"Unsupported expression: {type(node).__name__}", node)

    def _parse_fstring(self, node: ast.JoinedStr, loc: SourceLocation) -> TpyFString:
        """Parse an f-string (ast.JoinedStr) into a TpyFString node."""
        parts: list[str | TpyFStringValue] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue):
                conv = val.conversion
                if conv == FSTRING_CONV_ASCII:
                    raise ParseError("f-string !a conversion is not supported", node)
                expr = self._parse_expr(val.value)
                fmt_spec: str | None = None
                if val.format_spec is not None:
                    # format_spec is a JoinedStr; only constant specs are supported
                    spec_parts = []
                    for sp in val.format_spec.values:
                        if isinstance(sp, ast.Constant) and isinstance(sp.value, str):
                            spec_parts.append(sp.value)
                        else:
                            raise ParseError(
                                "Expressions inside f-string format specs are not supported", node
                            )
                    fmt_spec = "".join(spec_parts)
                    err = _validate_fstring_format_spec(fmt_spec)
                    if err is not None:
                        raise ParseError(f"Unsupported f-string format spec: {err}", node)
                parts.append(TpyFStringValue(expr=expr, conversion=conv, format_spec=fmt_spec))
            else:
                raise ParseError(f"Unsupported f-string part: {type(val).__name__}", node)
        return TpyFString(parts=parts, loc=loc)

    def _binop_to_str(self, op: ast.operator) -> str:
        """Convert binary operator to string."""
        result = _BINOP_TO_STR.get(type(op))
        if result is None:
            raise ParseError(f"Unsupported binary operator: {type(op).__name__}")
        return result

    def _cmpop_to_str(self, op: ast.cmpop) -> str:
        """Convert comparison operator to string."""
        result = _CMPOP_TO_STR.get(type(op))
        if result is None:
            raise ParseError(f"Unsupported comparison operator: {type(op).__name__}")
        return result

    def _unaryop_to_str(self, op: ast.unaryop) -> str:
        """Convert unary operator to string."""
        result = _UNARYOP_TO_STR.get(type(op))
        if result is None:
            raise ParseError(f"Unsupported unary operator: {type(op).__name__}")
        return result

    def _validate_const_default(self, expr: TpyExpr, node: ast.expr) -> None:
        """Validate that a default value expression is a compile-time constant."""
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                             TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral,
                             TpyTypeParamConstruct)):
            return
        # Bare name reference: must resolve to a module-level Final[T] constant.
        # Sema validates the binding (parser doesn't see globals or imports yet).
        if isinstance(expr, TpyName):
            return
        if isinstance(expr, TpyUnaryOp) and expr.op == "-":
            if isinstance(expr.operand, (TpyIntLiteral, TpyFloatLiteral)):
                return
        # Int32(5) etc. -- a fixed-int constructor wrapping a literal
        if isinstance(expr, TpyCall) and expr.func_name in _FIXED_INT_NAMES:
            if not expr.args:
                return  # Int32() -> 0
            if len(expr.args) == 1:
                self._validate_const_default(expr.args[0], node)
                return
        raise ParseError(
            f"Default parameter value must be a constant expression "
            f"(literal, None, fixed-int constructor like Int32(5), "
            f"or a Final[T] module constant)", node)

    def _parse_param_defaults(self, node: ast.FunctionDef, params: list,
                              skip_self: bool = False,
                              type_param_scope: dict | None = None,
                              kw_defaults: list | None = None,
                              n_kwonly: int = 0,
                              ) -> list['TpyExpr | None']:
        """Parse default values from a function definition.

        Returns a list aligned with params: None for params without defaults.
        Python's ast.arguments.defaults is right-aligned with args, so we
        left-pad with None. kw_defaults is 1:1 aligned with kwonlyargs.
        """
        n_positional = len(params) - n_kwonly
        ast_defaults = node.args.defaults

        # In methods, self is skipped from params but still counted in node.args.args
        num_ast_args = len(node.args.args)
        # defaults are right-aligned with the full args list
        num_no_default = num_ast_args - len(ast_defaults) if ast_defaults else num_ast_args

        defaults: list[TpyExpr | None] = []
        param_offset = 1 if skip_self else 0  # skip self in index mapping
        for i in range(n_positional):
            ast_idx = i + param_offset  # index into node.args.args
            default_idx = ast_idx - num_no_default
            if ast_defaults and default_idx >= 0 and default_idx < len(ast_defaults):
                expr = self._parse_expr(ast_defaults[default_idx])
                # Detect T() where T is a type parameter
                if (isinstance(expr, TpyCall) and not expr.args and not expr.kwargs
                        and type_param_scope and expr.func_name in type_param_scope):
                    expr = TpyTypeParamConstruct(expr.func_name, loc=expr.loc)
                self._validate_const_default(expr, ast_defaults[default_idx])
                defaults.append(expr)
            else:
                defaults.append(None)

        # Append keyword-only defaults (1:1 aligned with kwonlyargs)
        if kw_defaults and n_kwonly > 0:
            for kw_default in kw_defaults:
                if kw_default is not None:
                    expr = self._parse_expr(kw_default)
                    if (isinstance(expr, TpyCall) and not expr.args and not expr.kwargs
                            and type_param_scope and expr.func_name in type_param_scope):
                        expr = TpyTypeParamConstruct(expr.func_name, loc=expr.loc)
                    self._validate_const_default(expr, kw_default)
                    defaults.append(expr)
                else:
                    defaults.append(None)
        elif n_kwonly > 0:
            defaults.extend([None] * n_kwonly)

        return defaults

    def _get_default_value(self, node: ast.expr) -> str:
        """Convert a field default value AST node to a C++ literal string (for FieldInfo.default_value)."""
        if isinstance(node, ast.Constant):
            val = node.value
            if val is None:
                return "std::nullopt"
            if isinstance(val, bool):
                return "true" if val else "false"
            elif isinstance(val, str):
                escaped = val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                return f'"{escaped}"'
            return str(val)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self._get_default_value(node.operand)
            return f"-{inner}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # Int32(x) just becomes x in C++
            if node.func.id in _FIXED_INT_NAMES:
                if not node.args:
                    return "0"
                return self._get_default_value(node.args[0])
            args = ", ".join(str(self._get_default_value(a)) for a in node.args)
            return f"{node.func.id}({args})"
        return "0"

# ---------------------------------------------------------------------------
# FragmentParser -- lightweight parser for macro source fragments
# ---------------------------------------------------------------------------

class FragmentParser(Parser):
    """Parser for macro-generated source fragments (quote / add_method_from_source).

    Differs from Parser in two ways:
    - Resolves tpy/typing exports without explicit imports
    - Returns NominalType for unresolved type names instead of raising
    """

    def _resolve_type_name(self, local_name: str) -> tuple[str, str] | None:
        result = super()._resolve_type_name(local_name)
        if result:
            return result
        if local_name in get_tpy_exports():
            return ("tpy", local_name)
        if local_name in get_typing_exports():
            return ("typing", local_name)
        return None

    def _raise_unresolved_import_error(
        self, raw_name: str, node: ast.expr | None = None,
        *, loc: SourceLocation | None = None,
    ) -> None:
        # Lenient: unresolved imports are not errors in fragments. Override
        # signature matches the base shape so TypeResolver's loc-based call
        # path (`parser._raise_unresolved_import_error(name, loc=ref.loc)`)
        # is compatible when a fragment's resolve hits an unresolvable name.
        pass

    def _parse_type_annotation(
        self, node: ast.expr, type_param_scope: dict | None = None,
    ) -> TpyType:
        # Walker-then-lenient-resolver.  The lenient fallback
        # (constructing a NominalType placeholder for unresolved names)
        # lives in `TypeResolver.resolve_lenient`, keeping NominalType
        # construction out of the parser.
        if type_param_scope is None:
            type_param_scope = self._type_param_scope
        ref = self._parse_type_ref(node, type_param_scope)
        return self._resolver.resolve_lenient(ref, type_param_scope)

    @classmethod
    def parse_fragment(
        cls,
        source: str,
        kind: Literal["function", "statements", "expression"] = "function",
    ) -> 'TpyFunction | list[TpyStmt] | TpyExpr':
        """Parse a TPy source fragment without full module context.

        Used by macro quote/add_method_from_source APIs. Unresolved type names
        become NominalType(name) -- sema resolves them later.
        """
        source = textwrap.dedent(source).strip()
        parser = cls()
        parser.source_lines = source.splitlines()
        tree = ast.parse(source)

        if kind == "function":
            funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
            if len(funcs) != 1:
                raise ParseError(
                    f"quote_fun: expected exactly 1 function definition, "
                    f"got {len(funcs)}", tree)
            func_node = funcs[0]
            # Detect method: first param named 'self'.
            args = func_node.args.args
            has_self = bool(args and args[0].arg == "self")
            if has_self:
                # Route through `_parse_method` so method-specific state
                # (self_annotation, has_auto_readonly_decorator, @property
                # flags) gets populated -- otherwise sema's expand_methods
                # sees a bare TpyFunction and skips expansion.
                # `class_name` is only used in error messages; `<macro>`
                # is a synthetic placeholder.
                # `property_names=None` means @x.setter decorators fall
                # through to the general decorator handler and surface as
                # an "Unknown decorator" error, matching the prior free-
                # function routing.
                func = parser._parse_method(
                    func_node, class_name="<macro>",
                    type_param_scope=None, property_names=None)
            else:
                func = parser._parse_function(func_node)
            # Macro fragment output is consumed before sema's pre-pass runs,
            # so resolve refs immediately -- matches _parse_nested_def.
            # Fragments have no enclosing type-param scope; the function's
            # own type_params are merged in by _finalize_function_refs.
            parser._finalize_function_refs(func, outer_scope=None)
            if has_self and isinstance(
                    func.self_annotation, (TpyTypeRef, TpyUnionRef,
                                           TpyCallableRef, TpyLiteralRef)):
                # _finalize_function_refs doesn't touch self_annotation
                # (non-fragment call sites feed through
                # `_resolve_pending_type_refs` per-record). Resolve it here
                # under the method's own type-param scope so expand_methods
                # sees TpyType.
                scope: dict[str, TypeParamKind] = {}
                if func.type_params:
                    kinds = func.type_param_kinds or []
                    for i, name in enumerate(func.type_params):
                        scope[name] = kinds[i] if i < len(kinds) else TypeParamKind.TYPE
                func.self_annotation = parser._resolve_type_ref_impl(
                    func.self_annotation, scope or None)
            return func
        elif kind == "statements":
            return parser._parse_body(tree.body)
        elif kind == "expression":
            if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
                raise ParseError(
                    "quote_expr: expected a single expression", tree)
            return parser._parse_expr(tree.body[0].value)
        else:
            raise ValueError(f"Unknown fragment kind: {kind!r}")
