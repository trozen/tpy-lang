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
from typing import Any, NoReturn, Optional

from ..typesys import (
    TpyType, NamedType, PtrType, OwnType, ReadonlyType, AutoReadonlyType, AutoOwnType, FinalType, SelfType,
    strip_auto_readonly, apply_auto_readonly, has_auto_readonly, strip_auto_own, apply_auto_own, ensure_qualified,
    TypeParamRef, OptionalType, VoidType, make_union, EnumType, TupleType, FnType, CallableType,
    INT32, VOID, STR, STRING, STRVIEW, CHAR, BYTES, BYTEARRAY, BYTESVIEW, BOOL, FLOAT, FLOAT32, BIGINT, SELF, SLICE, FieldInfo, RecordInfo, TypeRegistry,
    FunctionInfo, MethodSignature, ProtocolInfo, TypeParamKind, BoolType, StrType,
    ALL_FIXED_INTS, public_module_name,
)
from ..modules import lookup_generic_type, lookup_generic_type_in_module, BuiltinTypeDef
from .nodes import (
    ParseError, SourceLocation, ParseWarning, RecordLinkage, FunctionLinkage,
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_ASCII,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct, TpyCall, TpyMethodCall,
    TpyFieldAccess, TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyComprehensionGenerator, TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr, TpyLambda,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyExprStmt, TpyReturn, TpyYield,
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

# Map of fixed-int type names to their singleton instances (used for expression inference)
_FIXED_INT_MAP: dict[str, TpyType] = {str(t): t for t in ALL_FIXED_INTS}


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


# Requires whitespace after `tpy:` to avoid matching C++ namespace comments (# tpy::Foo)
_DIRECTIVE_LINE_RE = re.compile(r'^#\s*tpy:\s+(\w.+)$')

# Schema: (positional arg types, allowed keyword arg types)
# Keys are the known directive names; unknown names produce a warning.
_DIRECTIVE_SPECS: dict[str, tuple[list[type], dict[str, type]]] = {
    "native_module":    ([], {"forward": bool}),
    "macro_module":     ([], {}),
    "include":          ([str], {}),
    "link":             ([str], {"platform": str}),
    "cpp_namespace":    ([str], {}),
    "cpp_include_path": ([str], {}),
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
    includes: list[str] = []
    link_libs: list[tuple[str, str | None]] = []
    native_module = False
    native_module_forward = False
    cpp_namespace: str | None = None
    cpp_include_path: str | None = None
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
            if kwargs.get("forward"):
                native_module_forward = True
        elif name == "include":
            includes.append(args[0])
        elif name == "link":
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
        elif name == "cpp_include_path":
            cpp_include_path = args[0]

    return ModuleDirectives(includes=includes, link_libs=link_libs, native_module=native_module,
                            native_module_forward=native_module_forward,
                            cpp_namespace=cpp_namespace, cpp_include_path=cpp_include_path), warnings


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
        # Schemas derived from @builtin_decorator stubs (populated by compiler
        # from previously-parsed modules, or from same-file definitions)
        self._decorator_schemas: dict[str, _DecoratorArgSchema] = dict(decorator_schemas) if decorator_schemas else {}
        self._star_import_resolver = star_import_resolver

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

    def _resolve_type_name(self, local_name: str) -> tuple[str, str] | None:
        """Resolve annotation name -> (module, original_name) or None.

        Checks explicit imports, then Python builtins, then local @builtin_type
        definitions.
        """
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

    def _resolve_qualified_type_name(self, node: ast.Attribute) -> tuple[str, str] | None:
        """Resolve module.Name -> (module, name) or None.

        Only resolves if the module was bare-imported (import X or import X as Y).
        'from X import ...' does NOT put the module name in scope.
        """
        if not isinstance(node.value, ast.Name):
            return None
        local_module = node.value.id
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

    def _resolve_primitive_type(self, module: str, original: str, node: ast.expr) -> TpyType | None:
        """Resolve a (module, original_name) pair to a primitive type.

        Returns the type if it's a directly-mapped primitive (int -> BIGINT, etc.),
        or None if it should fall through to registry lookups.
        Raises ParseError for names that can't be used as types (Protocol).
        """
        if module == "builtins":
            if original == "int": return BIGINT
            elif original == "float": return FLOAT
            elif original == "bool": return BOOL
            elif original == "str": return STR
            elif original == "bytes": return BYTES
            elif original == "bytearray": return BYTEARRAY
            elif original == "slice": return SLICE
            elif original == "None": return VOID
            elif original == "type": return NamedType("type", _module_qname=qnames.TYPE)
            elif original == "tuple":
                raise ParseError("tuple requires type arguments: tuple[T1, T2, ...]", node)
        elif module == "tpy":
            if (fixed_int := _FIXED_INT_MAP.get(original)) is not None:
                return fixed_int
            elif original == "Char":
                return CHAR
            elif original == "Float32":
                return FLOAT32
            elif original == "String":
                return STRING
            elif original == "StrView":
                return STRVIEW
            elif original == "BytesView":
                return BYTESVIEW
        elif module == "typing":
            if original == "Self":
                return SELF
            elif original == "Protocol":
                raise ParseError("'Protocol' cannot be used as a type annotation", node)
        return None

    def _resolve_registered_type(self, name: str, node: ast.expr, *, resolved: bool = False) -> TpyType | None:
        """Look up a name in the type registry (protocols, aliases, records).

        Raises ParseError for generic protocols used without type arguments,
        or for completely unknown names.
        """
        if (user_protocol := self.registry.get_protocol(name)) is not None:
            if user_protocol.type_params:
                raise ParseError(
                    f"Generic protocol '{name}' requires type arguments: "
                    f"{name}[{', '.join(user_protocol.type_params)}]",
                    node
                )
            return NamedType(name, is_protocol=True)
        elif (enum_type := self.registry.get_enum(name)) is not None:
            return enum_type
        elif (alias := self.registry.get_type_alias(name)) is not None:
            return alias
        elif not resolved:
            self._raise_unresolved_import_error(name, node)
        if self.registry.is_known_type(name) or name[0].isupper():
            return NamedType(name)
        return None

    def _raise_unresolved_import_error(self, raw_name: str, node: ast.expr) -> None:
        """Raise a helpful error for unresolved type names with import hints."""
        if raw_name in get_typing_exports():
            raise ParseError(f"'{raw_name}' requires: from typing import {raw_name}", node)
        # In stdlib _core modules, unresolved uppercase names may be forward
        # references to types defined later in the same file. Let them through.
        mod = self._imports._module_name
        if mod and "._" in mod and raw_name[0].isupper():
            if public_module_name(mod) in _IMPLICIT_MODULES:
                return
        if raw_name in get_tpy_exports():
            raise ParseError(f"'{raw_name}' requires: from tpy import {raw_name}", node)

    def _raise_unresolved_qualified_error(self, node: ast.expr) -> None:
        """Raise error for qualified names where the module wasn't imported."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mod = node.value.id
            canonical = self._reverse_module_aliases.get(mod, mod)
            qualified = f"{mod}.{node.attr}"
            if canonical in _IMPLICIT_MODULES:
                raise ParseError(f"'{qualified}' requires: import {canonical}", node)

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
              is_package_init: bool = False) -> TpyModule:
        """Parse TurboPython source code into a TpyModule."""
        self.source_lines = source.splitlines()
        self._warnings = []
        self._imports = ImportProcessor(self._warn, module_name=module_name,
                                        is_package_init=is_package_init,
                                        star_import_resolver=self._star_import_resolver)
        self._module_aliases = {}
        self._bare_module_imports = set()
        self._reverse_module_aliases = {}
        tree = ast.parse(source)
        module = self._parse_module(tree)
        directives, directive_warnings = _scan_directives(self.source_lines)
        module.directives = directives
        module.parse_warnings.extend(directive_warnings)
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

    def _is_type_alias_assign(self, node: ast.Assign) -> bool:
        """Check if an assignment is an old-style type alias (e.g., Shape = Circle | Rect).

        Triggers when ALL arms are Names/None AND at least one arm is a
        confirmed type (registered or builtin). Pure forward-ref aliases
        (all arms capitalized but none yet registered) also match.
        """
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return False
        if not (isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.BitOr)):
            return False
        arms = _collect_bitor_arms(node.value)
        has_confirmed_type = False
        all_capitalized = True
        for arm in arms:
            if isinstance(arm, ast.Constant) and arm.value is None:
                has_confirmed_type = True
                continue
            if not isinstance(arm, ast.Name):
                return False
            if self._is_type_name(arm.id):
                has_confirmed_type = True
            elif not arm.id[0].isupper():
                all_capitalized = False
        return has_confirmed_type or all_capitalized

    def _register_type_alias(
        self, name: str, type_node: ast.expr,
        type_aliases: dict[str, tuple[TpyType, SourceLocation | None]]
    ) -> None:
        """Parse a type annotation node and register as a type alias."""
        alias_type = self._parse_type_annotation(type_node)
        self.registry.register_type_alias(name, alias_type)
        loc = SourceLocation(line=type_node.lineno) if hasattr(type_node, 'lineno') else None
        type_aliases[name] = (alias_type, loc)

    def _parse_module(self, tree: ast.Module) -> TpyModule:
        """Parse a module."""
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
                if source and source[0] in _IMPLICIT_MODULES and not has_builtin_type:
                    self._warn(f"class '{node.name}' shadows import from '{source[0]}'", node)
                result = self._parse_class(node)
                if isinstance(result, TpyProtocol):
                    protocols.append(result)
                    # Register the protocol type
                    self.registry.register_protocol(ProtocolInfo(
                        name=result.name,
                        methods=result.methods,
                        type_params=result.type_params,
                    ))
                elif isinstance(result, TpyEnum):
                    enums.append(result)
                    # Register the enum type so it can be used in type annotations
                    enum_type = EnumType(
                        name=result.name,
                        members=tuple(m for m, _, _ in result.members),
                        member_values=tuple((m, v) for m, v, _ in result.members),
                    )
                    self.registry.register_enum(enum_type)
                else:
                    records.append(result)
                    # Register the record type
                    self.registry.register_record(RecordInfo(
                        name=result.name,
                        fields=result.fields,
                        has_init=result.init_method is not None or result.is_dataclass,
                        builtin_type_key=result.builtin_type_key,
                    ))
                    # Fix up method return types that reference the class by
                    # name but were parsed before the class was registered
                    if result.builtin_type_key:
                        for method in result.methods:
                            rt = method.return_type
                            if (isinstance(rt, NamedType) and rt.name == result.name
                                    and not rt._module_qname):
                                method.return_type = NamedType(
                                    rt.name, rt.type_args, rt.is_protocol,
                                    result.builtin_type_key, rt.is_dynamic_protocol)
            elif isinstance(node, ast.FunctionDef):
                seen_non_import = True
                func = self._parse_function(node)
                functions.append(func)
                if func.builtin_decorator_key:
                    # Register for decorator resolution (like @builtin_type for records)
                    self.registry.register_function(FunctionInfo(
                        name=func.name, params=[], return_type=VOID,
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
                top_level_stmts.append(self._parse_stmt(node))

        return TpyModule(records=records, functions=functions, protocols=protocols, enums=enums, top_level_stmts=top_level_stmts, source_lines=self.source_lines, imports=imports, tpy_star_import=self._imports.tpy_star_import, star_imports=self._imports.star_imports, user_module_imports=user_module_imports, module_aliases=module_aliases, bare_module_imports=bare_module_imports, type_aliases=type_aliases, parse_warnings=self._warnings)

    def _is_protocol_base(self, base: ast.expr) -> bool:
        """Check if a base class expression refers to typing.Protocol."""
        if isinstance(base, ast.Name):
            resolved = self._resolve_type_name(base.id)
            return resolved == ("typing", "Protocol")
        elif isinstance(base, ast.Attribute):
            resolved = self._resolve_qualified_type_name(base)
            return resolved == ("typing", "Protocol")
        return False

    def _is_enum_base(self, base: ast.expr) -> bool:
        """Check if a base class expression refers to enum.Enum."""
        if isinstance(base, ast.Name):
            resolved = self._resolve_type_name(base.id)
            return resolved == ("enum", "Enum")
        elif isinstance(base, ast.Attribute):
            resolved = self._resolve_qualified_type_name(base)
            return resolved == ("enum", "Enum")
        return False

    def _is_int_enum_base(self, base: ast.expr) -> bool:
        """Check if a base class expression refers to enum.IntEnum."""
        if isinstance(base, ast.Name):
            resolved = self._resolve_type_name(base.id)
            return resolved == ("enum", "IntEnum")
        elif isinstance(base, ast.Attribute):
            resolved = self._resolve_qualified_type_name(base)
            return resolved == ("enum", "IntEnum")
        return False

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
            # @staticmethod is a Python builtin, not resolved through imports
            if name == "staticmethod":
                return ("builtins", "staticmethod", arg_value)
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

        _type_map: dict[type, tuple[type, str]] = {
            BoolType: (bool, "bool"), StrType: (str, "str"),
        }
        def _map_type(ptype: TpyType) -> tuple[type, str] | None:
            match = _type_map.get(type(ptype))
            if match:
                return match
            if isinstance(ptype, NamedType) and ptype.qualified_name() == qnames.TYPE:
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

        # Parse record decorators (@native, @native_c, @nocopy, macro decorators)
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
        type_param_bounds: dict[str, TpyType] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                    if tp.bound is not None:
                        # Check for N: int syntax (integer type parameter)
                        if isinstance(tp.bound, ast.Name) and tp.bound.id == 'int':
                            type_param_kinds.append(TypeParamKind.INT)
                        else:
                            # Protocol bound -- full validation deferred to sema
                            # (cross-module protocols aren't in parser registry)
                            type_param_kinds.append(TypeParamKind.TYPE)
                            bound_type = self._parse_type_annotation(tp.bound)
                            if not isinstance(bound_type, NamedType):
                                raise ParseError(f"Type parameter bound must be a protocol or 'int', got {bound_type}", tp)
                            type_param_bounds[tp.name] = bound_type
                    else:
                        type_param_kinds.append(TypeParamKind.TYPE)
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Create a dict of type param names to kinds for scope during parsing
        type_param_scope = dict(zip(type_params, type_param_kinds)) if type_params else None
        # Store scope for use during method body parsing (expression parsing uses this)
        old_scope = self._type_param_scope
        self._type_param_scope = type_param_scope

        # Parse base classes/protocols for inheritance (with type params in scope)
        # Classification into parent class vs protocol is deferred to sema
        bases: list[TpyType] = []
        for base in node.bases:
            base_type = self._parse_type_annotation(base, type_param_scope)
            bases.append(base_type)

        fields = []
        methods = []

        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                # Field declaration: name: Type = default
                if not isinstance(item.target, ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.target.id
                field_type = self._parse_type_annotation(item.annotation, type_param_scope)
                default_val = None
                default_expr = None
                if item.value is not None:
                    default_expr = self._parse_expr(item.value)
                    # Resolve import origin for call expressions (e.g. field() or dataclasses.field())
                    if isinstance(default_expr, (TpyCall, TpyMethodCall)):
                        self._resolve_call_import(default_expr, item.value)
                    default_val = self._get_default_value(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val, default_expr=default_expr, loc=self._loc(item)))
            elif isinstance(item, ast.Assign):
                # Field with inferred type: name = Int32(0)
                if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.targets[0].id
                field_type = self._infer_type_from_expr(item.value)
                if field_type is None:
                    raise ParseError(f"Cannot infer type for field '{field_name}'", item)
                default_val = self._get_default_value(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val, loc=self._loc(item)))
            elif isinstance(item, ast.FunctionDef):
                parsed = self._parse_method(item, node.name, type_param_scope)
                if parsed.auto_readonly:
                    methods.extend(self._clone_auto_readonly(parsed))
                elif parsed.auto_own:
                    methods.extend(self._clone_auto_own(parsed))
                else:
                    methods.append(parsed)
            elif isinstance(item, ast.Pass):
                pass
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and item.value.value is ...:
                pass  # Ellipsis for opaque native types
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                pass  # Docstring
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
                new_fields = self._auto_declare_fields_from_init(init_method, fields)
                fields.extend(new_fields)
                # Reorder fields to match __init__ assignment order so that
                # C++ struct layout matches the init list (avoids -Wreorder).
                fields = self._reorder_fields_by_init(init_method, fields)

        # Validate method constraints based on class linkage
        for method in methods:
            if linkage != RecordLinkage.DEFAULT:
                # Native class: methods must be stubs
                if not method.is_stub:
                    raise ParseError(
                        f"Methods on @{linkage.value} classes must have '...' body (stub declaration)", node)
            else:
                # Non-native class: stubs and @native decorators are not allowed
                # (@overload stubs are exempt -- they declare overload signatures)
                if method.is_stub and not method.is_overload_stub:
                    raise ParseError(
                        f"Method '{method.name}' cannot have '...' body on a regular class "
                        f"(only allowed on @native/@native_c classes)", node)
                if method.native_name is not None:
                    raise ParseError(
                        f"@native(\"...\") decorator on method '{method.name}' is only allowed "
                        f"on @native/@native_c classes", node)

        # Restore the scope
        self._type_param_scope = old_scope
        return TpyRecord(name=node.name, fields=fields, methods=methods, type_params=type_params, type_param_kinds=type_param_kinds, type_param_bounds=type_param_bounds, bases=bases, linkage=linkage, native_name=native_name, is_nocopy=is_nocopy, builtin_type_key=builtin_type_key, pending_macros=pending_macros, loc=self._loc(node))

    def _auto_declare_fields_from_init(
        self,
        init_method: TpyFunction,
        existing_fields: list[FieldInfo],
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
                cpp_concept = ensure_qualified(pos)
                continue
            dec_name = self._decorator_local_name(dec) or "?"
            raise ParseError(
                f"Unsupported decorator '@{dec_name}' on protocol '{node.name}'. "
                f"Only @dynamic (from tpy) and @native (from tpy.extern) are allowed on protocols", dec)
        if is_dynamic and cpp_concept is not None:
            raise ParseError("@dynamic and @native cannot be combined on a protocol", node)

        # Extract parent protocols (excluding Protocol itself)
        parent_protocols = []
        for base in node.bases:
            if self._is_protocol_base(base):
                continue
            if isinstance(base, ast.Name):
                parent_protocols.append(base.id)
            elif isinstance(base, ast.Subscript):
                # Generic parent protocols like Parent[T] are not yet supported
                base_name = None
                if isinstance(base.value, ast.Name):
                    base_name = base.value.id
                elif isinstance(base.value, ast.Attribute) and isinstance(base.value.value, ast.Name):
                    base_name = f"{base.value.value.id}.{base.value.attr}"
                if base_name:
                    raise ParseError(
                        f"Generic parent protocols are not yet supported: {base_name}[...]. "
                        f"Use non-generic parent protocols instead.",
                        base
                    )

        # Extract type parameters from Python 3.12+ syntax: class Foo[T](Protocol):
        # Note: Protocols don't support INT type params (only TYPE)
        type_params = []
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                else:
                    raise ParseError(f"Only simple type parameters supported in protocols, got {type(tp).__name__}", node)

        # Set type param scope for parsing method signatures (all TYPE kind for protocols)
        old_scope = self._type_param_scope
        self._type_param_scope = {tp: TypeParamKind.TYPE for tp in type_params} if type_params else None

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
                    param_type = self._parse_type_annotation(arg.annotation)
                    params.append((arg.arg, param_type))

                return_type = VOID
                if item.returns:
                    return_type = self._parse_type_annotation(item.returns)

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
                field_type = self._parse_type_annotation(item.annotation)
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
                if isinstance(value_node.func, ast.Name):
                    resolved = self._resolve_type_name(value_node.func.id)
                    if resolved == ("enum", "auto"):
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
        qnames.NATIVE_C: RecordLinkage.NATIVE_C,
    }

    _METHOD_LINKAGE_MAP: dict[str, FunctionLinkage] = {
        qnames.NATIVE: FunctionLinkage.NATIVE,
        qnames.NATIVE_C: FunctionLinkage.NATIVE_C,
    }

    def _parse_method(self, node: ast.FunctionDef, class_name: str, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyFunction:
        """Parse a method definition."""
        # Check decorators (@staticmethod, @readonly, @native("cpp_name"), @override)
        is_staticmethod = False
        is_readonly = False
        readonly_opt_out = False
        is_pure = False
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
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"method '{node.name}'")
            pos, kw = self._validate_decorator_args(qname, arg, dec)
            if qname == qnames.STATICMETHOD:
                is_staticmethod = True
            elif qname == qnames.PURE:
                is_pure = True
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
                native_name = pos
                native_function = kw.get("function", False)
            else:
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(f"Unknown decorator '{dec_name}' on method '{node.name}'", dec)
        if is_override and is_staticmethod:
            raise ParseError(f"@override cannot be combined with @staticmethod on method '{node.name}'", node)
        if auto_readonly:
            if is_readonly:
                raise ParseError(f"@auto_readonly cannot be combined with @readonly on method '{node.name}'", auto_readonly_dec)
            if is_staticmethod:
                raise ParseError(f"@auto_readonly cannot be combined with @staticmethod on method '{node.name}'", auto_readonly_dec)
            if node.name in ("__init__", "__del__"):
                raise ParseError(f"@auto_readonly is not valid on '{node.name}'", auto_readonly_dec)

        # Extract method-level type parameters (e.g. def foo[T](self, x: T) -> T:)
        method_type_params: list[str] = []
        method_type_param_bounds: dict[str, TpyType] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    method_type_params.append(tp.name)
                    if tp.bound is not None:
                        bound_type = self._parse_type_annotation(tp.bound)
                        if not isinstance(bound_type, NamedType):
                            raise ParseError(f"Type parameter bound must be a protocol or 'int', got {bound_type}", tp)
                        method_type_param_bounds[tp.name] = bound_type
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        if auto_readonly and method_type_params:
            raise ParseError(
                f"auto_readonly on methods with method-level type parameters is not yet supported ('{node.name}')",
                auto_readonly_dec or node,
            )

        # Merge class-level and method-level type param scopes
        if method_type_params:
            merged_scope = dict(type_param_scope) if type_param_scope else {}
            for tp_name in method_type_params:
                merged_scope[tp_name] = TypeParamKind.TYPE
            type_param_scope = merged_scope

        params = []
        has_self = not is_staticmethod
        is_consuming = False
        auto_own = False
        # Count non-self params for __exit__ stripping check
        n_non_self = len(node.args.args) - (1 if has_self else 0)
        args_iter = iter(enumerate(node.args.args))
        for i, arg in args_iter:
            if i == 0 and has_self:
                # Non-static methods must have 'self' as first parameter
                if arg.arg != "self":
                    raise ParseError(f"First parameter of method '{node.name}' must be 'self'", node)
                # Check for self: Own[Self] or self: auto_own[Self] annotation
                if arg.annotation is not None:
                    self_ann = self._parse_type_annotation(arg.annotation, type_param_scope)
                    if isinstance(self_ann, OwnType) and isinstance(self_ann.wrapped, SelfType):
                        if node.name in ("__init__", "__del__"):
                            raise ParseError(
                                f"Own[Self] is not allowed on '{node.name}'",
                                node,
                            )
                        if is_readonly:
                            raise ParseError(
                                f"Own[Self] cannot be combined with @readonly on method '{node.name}'",
                                node,
                            )
                        is_consuming = True
                    elif isinstance(self_ann, AutoOwnType) and isinstance(self_ann.wrapped, SelfType):
                        if node.name in ("__init__", "__del__"):
                            raise ParseError(
                                f"auto_own[Self] is not allowed on '{node.name}'",
                                node,
                            )
                        if is_readonly:
                            raise ParseError(
                                f"auto_own[Self] cannot be combined with @readonly on method '{node.name}'",
                                node,
                            )
                        auto_own = True
                    elif isinstance(self_ann, AutoReadonlyType) and isinstance(self_ann.wrapped, SelfType):
                        if node.name in ("__init__", "__del__"):
                            raise ParseError(
                                f"auto_readonly[Self] is not allowed on '{node.name}'",
                                node,
                            )
                        if is_readonly:
                            raise ParseError(
                                f"auto_readonly[Self] cannot be combined with @readonly on method '{node.name}'",
                                node,
                            )
                        if auto_readonly_dec is not None:
                            raise ParseError(
                                f"'self: auto_readonly[Self]' cannot be combined with the "
                                f"@auto_readonly decorator on method '{node.name}'",
                                node,
                            )
                        auto_readonly = True
                    else:
                        raise ParseError(
                            f"Only 'Own[Self]', 'auto_own[Self]', or 'auto_readonly[Self]' "
                            f"is allowed as a type annotation for 'self', "
                            f"got '{self_ann}'",
                            node,
                        )
                continue
            # __exit__ exception params (exc_type, exc_val, exc_tb) are stripped --
            # they are always None in TPy (no general exceptions). This allows
            # CPython-compatible signatures without requiring type annotations.
            if node.name == "__exit__" and n_non_self == 3 and has_self:
                continue
            if arg.annotation is None:
                raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
            param_type = self._parse_type_annotation(arg.annotation, type_param_scope)
            params.append((arg.arg, param_type))

        # __exit__ must have exactly 3 params (exc_type, exc_val, exc_tb) to
        # match CPython's context manager protocol.
        if node.name == "__exit__" and has_self and n_non_self != 3:
            raise ParseError(
                f"__exit__ must have 3 parameters: "
                f"__exit__(self, exc_type, exc_val, exc_tb)",
                node,
            )

        # @auto_readonly decorator: wrap all eligible params with AutoReadonlyType.
        # This unifies with the per-param annotation path -- the decorator is just
        # sugar for annotating every non-value, non-already-readonly param.
        if auto_readonly and auto_readonly_dec is not None:
            params = [
                (n, AutoReadonlyType(t) if (not t.is_value_type()
                     and not isinstance(t, (AutoReadonlyType, ReadonlyType)))
                 else t)
                for n, t in params
            ]

        # Detect per-param auto_readonly[T] (from explicit annotations, not decorator).
        if not auto_readonly:
            for _, ptype in params:
                if has_auto_readonly(ptype):
                    auto_readonly = True
                    break

        # Re-check type-param guard for annotation/per-param path (the earlier
        # guard at decorator time only fires when auto_readonly_dec is set).
        if auto_readonly and auto_readonly_dec is None and method_type_params:
            raise ParseError(
                f"auto_readonly on methods with method-level type parameters "
                f"is not yet supported ('{node.name}')",
                node,
            )

        # Parse default parameter values (skip_self for non-static methods)
        defaults = self._parse_param_defaults(node, params, skip_self=has_self,
                                              type_param_scope=type_param_scope)

        # Get return type (default to Void for __init__)
        return_type = VOID
        if node.name != "__init__" and node.returns:
            return_type = self._parse_type_annotation(node.returns, type_param_scope)

        if cpp_template is not None:
            if not self._is_stub_body(node.body):
                raise ParseError(
                    f"@cpp_template method '{node.name}' must have `...` body", node)
        is_stub_body = self._is_stub_body(node.body)
        is_overload_stub_body = is_stub_body or self._is_pass_body(node.body)
        is_stub = (is_stub_body and not is_overload_stub) or cpp_template is not None
        if is_overload_stub:
            if not is_overload_stub_body:
                raise ParseError(f"@overload method '{node.name}' must have `...` or `pass` body", node)
            body = []
        elif is_stub:
            body = []
        else:
            body = [self._parse_stmt(stmt) for stmt in node.body]

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
            is_consuming=is_consuming,
            is_readonly=is_readonly,
            readonly_opt_out=readonly_opt_out,
            is_pure=is_pure,
            auto_readonly=auto_readonly,
            auto_own=auto_own,
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
            error_return=error_return,
            is_generator=is_generator,
            loc=self._loc(node)
        )
        return method

    def _clone_auto_readonly(self, method: TpyFunction) -> list[TpyFunction]:
        """Expand an auto_readonly method into two ordinary overloads.

        Returns [mutable_overload, const_overload].
        - Mutable: params/return stripped of auto_readonly, is_readonly=False
        - Const:   params/return with auto_readonly -> readonly, is_readonly=True

        auto_readonly[T] nodes in params and return type specify where readonly
        is applied in the const overload. The @auto_readonly decorator wraps all
        eligible params before this runs, so both decorator and per-param
        annotations go through the same code path.
        """
        mutable_params = [(n, strip_auto_readonly(t)) for n, t in method.params]
        const_params = [(n, apply_auto_readonly(t)) for n, t in method.params]
        mutable_return = strip_auto_readonly(method.return_type)
        const_return = apply_auto_readonly(method.return_type)
        mutable = dataclasses.replace(
            method,
            params=mutable_params,
            return_type=mutable_return,
            is_readonly=False,
            auto_readonly=False,
            auto_readonly_params_resolved=True,
            is_auto_readonly_mutable_clone=True,
        )
        const = dataclasses.replace(
            method,
            params=const_params,
            return_type=const_return,
            body=copy.deepcopy(method.body),
            is_readonly=True,
            auto_readonly=False,
            auto_readonly_params_resolved=True,
            defaults=copy.deepcopy(method.defaults),
        )
        return [mutable, const]

    def _clone_auto_own(self, method: TpyFunction) -> list[TpyFunction]:
        """Expand a self: auto_own[Self] method into two ordinary overloads.

        Returns [borrowing_overload, consuming_overload].
        - Borrowing: is_consuming=False, auto_own=False, return_type=strip_auto_own(original)
        - Consuming: is_consuming=True,  auto_own=False, return_type=apply_auto_own(original)

        auto_own[T] nodes in the return type specify where Own is applied
        in the consuming overload. Parts without auto_own[T] are unchanged.
        """
        borrowing_return = strip_auto_own(method.return_type)
        consuming_return = apply_auto_own(method.return_type)
        borrowing = dataclasses.replace(
            method,
            return_type=borrowing_return,
            is_consuming=False,
            auto_own=False,
            is_auto_own_borrowing_clone=True,
        )
        consuming = dataclasses.replace(
            method,
            return_type=consuming_return,
            body=copy.deepcopy(method.body),
            is_consuming=True,
            auto_own=False,
            defaults=copy.deepcopy(method.defaults),
        )
        return [borrowing, consuming]

    _FUNCTION_LINKAGE_MAP: dict[str, FunctionLinkage] = {
        qnames.NATIVE: FunctionLinkage.NATIVE,
        qnames.NATIVE_C: FunctionLinkage.NATIVE_C,
        qnames.EXTERN_C: FunctionLinkage.EXTERN_C,
    }

    def _parse_function(self, node: ast.FunctionDef) -> TpyFunction:
        """Parse a function definition."""
        is_noalloc = False
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
                    raise ParseError(
                        f"Function '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                if kw.get("function"):
                    dec_name = self._decorator_local_name(dec)
                    raise ParseError(f"@{dec_name}(function=...) is only valid on methods, not free functions", dec)
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
        type_param_bounds: dict[str, TpyType] = {}
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
                            bound_type = self._parse_type_annotation(tp.bound)
                            if not isinstance(bound_type, NamedType):
                                raise ParseError(f"Type parameter bound must be a protocol or 'int', got {bound_type}", tp)
                            type_param_bounds[tp.name] = bound_type
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
                param_type = (self._parse_type_annotation(arg.annotation, type_param_scope)
                              if arg.annotation else VOID)
                params.append((arg.arg, param_type))
        else:
            for arg in node.args.args:
                if arg.annotation is None:
                    raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
                param_type = self._parse_type_annotation(arg.annotation, type_param_scope)
                params.append((arg.arg, param_type))

        # Parse default parameter values
        defaults = self._parse_param_defaults(node, params, skip_self=False,
                                              type_param_scope=type_param_scope)

        return_type = VOID
        if node.returns:
            return_type = self._parse_type_annotation(node.returns, type_param_scope)

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
            if not is_overload_stub_body:
                raise ParseError(f"@overload function '{node.name}' must have `...` or `pass` body", node)
            body = []
        elif linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C):
            if not (self._is_stub_body(node.body)):
                raise ParseError(
                    f"@{linkage.value} function '{node.name}' must have `...` body (it declares an external symbol)",
                    node)
            is_stub = True
            body = []
        elif linkage == FunctionLinkage.EXTERN_C:
            if is_stub_body:
                raise ParseError(
                    f"@extern_c function '{node.name}' must have a body (it exports a TPy function)",
                    node)
            body = [self._parse_stmt(stmt) for stmt in node.body]
        else:
            body = [self._parse_stmt(stmt) for stmt in node.body]

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
            type_param_bounds=type_param_bounds,
            type_param_defaults=type_param_defaults,
            defaults=defaults,
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
        """Parse a type annotation.

        Args:
            node: The AST node representing the type annotation.
            type_param_scope: Dict of type parameter names to their kinds currently in scope.
                              Falls back to self._type_param_scope if not provided.
        """
        # Use instance variable as fallback for type parameter scope
        if type_param_scope is None:
            type_param_scope = self._type_param_scope
        if isinstance(node, ast.Name):
            name = node.id
            # Check if this is a type parameter reference
            if type_param_scope and name in type_param_scope:
                kind = type_param_scope[name]
                return TypeParamRef(name, kind=kind)

            resolved = self._resolve_type_name(name)

            if resolved:
                primitive = self._resolve_primitive_type(*resolved, node)
                if primitive is not None:
                    return primitive

            # Registry lookups (user protocols, type aliases, builtin protocols, user records)
            resolved_name = resolved[1] if resolved else name
            registered = self._resolve_registered_type(resolved_name, node, resolved=bool(resolved))
            if registered is not None:
                # Set _module_qname for @builtin_type records so cross-module
                # lookups work (e.g. open() returning TextIO). Only for types
                # with builtin_type_key -- regular records and aliases don't
                # need this and would break if mis-tagged. The `resolved`
                # guard ensures this only fires for names that went through
                # _resolve_type_name (imports or builtins exports), not for
                # unresolved forward references.
                if (resolved and isinstance(registered, NamedType)
                        and not registered.is_protocol and not registered._module_qname):
                    btk = self.registry.get_builtin_type_key(registered.name)
                    if btk:
                        registered = NamedType(registered.name, registered.type_args,
                                               registered.is_protocol, btk,
                                               registered.is_dynamic_protocol)
                return registered
            raise ParseError(f"Unknown type: {name}", node)

        elif isinstance(node, ast.Subscript):
            # Resolve container name (supports both Name and Attribute forms)
            if isinstance(node.value, ast.Name):
                resolved = self._resolve_type_name(node.value.id)
                raw_name = node.value.id
            elif isinstance(node.value, ast.Attribute):
                resolved = self._resolve_qualified_type_name(node.value)
                raw_name = (f"{node.value.value.id}.{node.value.attr}"
                            if isinstance(node.value.value, ast.Name) else None)
            else:
                raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

            if resolved:
                module, original = resolved
                if module == "tpy":
                    if original == "Ptr":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        # Ptr[readonly[T]] -> is_readonly=True
                        if isinstance(inner, ReadonlyType):
                            return PtrType(inner.wrapped, is_readonly=True)
                        return PtrType(inner)
                    elif original == "Own":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return OwnType(inner)
                    elif original == "readonly":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return ReadonlyType(inner)
                    elif original == "auto_readonly":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return AutoReadonlyType(inner)
                    elif original == "auto_own":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return AutoOwnType(inner)
                    elif original == "Fn":
                        slices = _extract_subscript_slices(node)
                        if len(slices) != 2:
                            raise ParseError(
                                "Fn requires exactly 2 arguments: Fn[[ParamTypes...], ReturnType]", node)
                        param_list_node, return_node = slices
                        if not isinstance(param_list_node, ast.List):
                            raise ParseError(
                                "Fn parameter types must be a list: Fn[[Int32, str], bool]", node)
                        param_types = tuple(
                            self._parse_type_annotation(p, type_param_scope)
                            for p in param_list_node.elts
                        )
                        return_type = self._parse_type_annotation(return_node, type_param_scope)
                        return FnType(param_types, return_type)
                elif module == "builtins":
                    if original == "tuple":
                        return self._parse_tuple_type(node, type_param_scope)
                elif module == "typing":
                    if original == "Optional":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return OptionalType(inner)
                    elif original == "Final":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return FinalType(inner)
                    elif original == "Callable":
                        slices = _extract_subscript_slices(node)
                        if len(slices) != 2:
                            raise ParseError(
                                "Callable requires exactly 2 arguments: Callable[[ParamTypes...], ReturnType]", node)
                        param_list_node, return_node = slices
                        if not isinstance(param_list_node, ast.List):
                            raise ParseError(
                                "Callable parameter types must be a list: Callable[[Int32, str], bool]", node)
                        param_types = tuple(
                            self._parse_type_annotation(p, type_param_scope)
                            for p in param_list_node.elts
                        )
                        return_type = self._parse_type_annotation(return_node, type_param_scope)
                        return CallableType(param_types, return_type)
            else:
                # Qualified name with missing module import
                if isinstance(node.value, ast.Attribute):
                    self._raise_unresolved_qualified_error(node.value)

            # Use original name for registry lookups when resolved
            resolved_container = resolved[1] if resolved else raw_name

            if resolved_container:
                # Module-defined generic types (list, Array, Span, etc.)
                if lookup := lookup_generic_type(resolved_container):
                    # tpy generic types require explicit import
                    if not resolved and lookup.qualified_name.startswith("tpy."):
                        self._raise_unresolved_import_error(raw_name, node)
                    return self._parse_generic_type(node, resolved_container, lookup.type_def, type_param_scope)

                # Generic types from imported modules (user modules, builtin submodules, etc.)
                if resolved:
                    source_module, original_name = resolved
                    if lookup := lookup_generic_type_in_module(original_name, source_module):
                        return self._parse_generic_type(node, resolved_container, lookup.type_def, type_param_scope)
                elif not resolved and raw_name:
                    if import_source := self._imports.get_import_source(raw_name):
                        source_module, original_name = import_source
                        if lookup := lookup_generic_type_in_module(original_name, source_module):
                            return self._parse_generic_type(node, resolved_container, lookup.type_def, type_param_scope)

                # User-defined generic protocols (e.g., Container[Int32])
                if user_protocol := self.registry.get_protocol(resolved_container):
                    if user_protocol.type_params:
                        type_args = self._parse_protocol_type_args(node, resolved_container, user_protocol.type_params, type_param_scope)
                        return NamedType(resolved_container, type_args, is_protocol=True)

                # User-defined generic records (e.g., Stack[Int32])
                if not resolved and raw_name:
                    self._raise_unresolved_import_error(raw_name, node)
                if self.registry.get_record(resolved_container) is not None or resolved_container[0].isupper():
                    type_args = self._parse_record_type_args(node, resolved_container, type_param_scope)
                    return NamedType(resolved_container, type_args)

            raise ParseError(f"Unknown generic type: {raw_name}", node)

        elif isinstance(node, ast.Attribute):
            resolved = self._resolve_qualified_type_name(node)
            if resolved:
                primitive = self._resolve_primitive_type(*resolved, node)
                if primitive is not None:
                    return primitive
                # Registry lookups for qualified names
                registered = self._resolve_registered_type(resolved[1], node, resolved=True)
                if registered is not None:
                    return registered
            # Not resolved -- check if module exists but wasn't imported
            self._raise_unresolved_qualified_error(node)
            qualified = f"{node.value.id}.{node.attr}" if isinstance(node.value, ast.Name) else ast.dump(node)
            raise ParseError(f"Unsupported qualified type: {qualified}", node)

        elif isinstance(node, ast.Constant) and node.value is None:
            return VOID

        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            arms = _collect_bitor_arms(node)
            parsed = [self._parse_type_annotation(arm, type_param_scope) for arm in arms]

            # Own[T] cannot appear as a union member in multi-type unions.
            # Own[T] | None is fine (collapses to Optional[Own[T]]), but
            # Cat | Own[Dog] is ambiguous (mixed ownership in one variant).
            non_none = [t for t in parsed if not isinstance(t, VoidType)]
            if len(non_none) > 1:
                for t in non_none:
                    if isinstance(t, OwnType):
                        unwrapped = [p.wrapped if isinstance(p, OwnType) else p for p in non_none]
                        raise ParseError(
                            f"Own[{t.wrapped}] cannot be a union member. "
                            f"Use Own[...] around the whole union instead: Own[{' | '.join(str(u) for u in unwrapped)}]",
                            node)

            # Handle readonly normalization:
            # readonly[A] | readonly[B] -> readonly[A | B]
            # readonly[A] | B -> error (mixed readonly)
            readonly_count = sum(1 for t in parsed if isinstance(t, ReadonlyType))
            non_none_count = sum(1 for t in parsed if not isinstance(t, VoidType))
            if readonly_count > 0 and readonly_count < non_none_count:
                raise ParseError("Cannot mix readonly and non-readonly types in a union", node)
            if readonly_count > 0:
                unwrapped = [
                    t.wrapped if isinstance(t, ReadonlyType) else t
                    for t in parsed
                ]
                return ReadonlyType(make_union(*unwrapped))

            return make_union(*parsed)

        raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

    def _parse_protocol_type_args(self, node: ast.Subscript, name: str,
                                    type_params: list[str], type_param_scope: dict[str, TypeParamKind] | None = None) -> tuple[TpyType, ...]:
        """Parse type arguments for a generic protocol like Sequence[Int32]."""
        expected_count = len(type_params)
        slices = _extract_subscript_slices(node)

        if len(slices) != expected_count:
            raise ParseError(f"{name} requires exactly {expected_count} type parameters", node)

        type_args = tuple(self._parse_type_annotation(s, type_param_scope) for s in slices)
        return type_args

    def _parse_record_type_args(self, node: ast.Subscript, name: str, type_param_scope: dict[str, TypeParamKind] | None = None) -> tuple[TpyType | int, ...]:
        """Parse type arguments for a user-defined generic record like Stack[Int32] or Matrix[Int32, 8].

        For records with integer type parameters, integer literals are allowed in type argument positions.
        The validation of which positions accept integers is done in sema (since the record info
        may not be registered yet during parsing).
        """
        slices = _extract_subscript_slices(node)

        # Parse each type argument (allowing integer literals)
        type_args: list[TpyType | int] = []
        for s in slices:
            # Check for integer literals
            if isinstance(s, ast.Constant) and isinstance(s.value, int):
                type_args.append(s.value)
            # Check for type parameter references that are INT kind (forward as TypeParamRef)
            elif isinstance(s, ast.Name) and type_param_scope and s.id in type_param_scope:
                kind = type_param_scope[s.id]
                type_args.append(TypeParamRef(s.id, kind=kind))
            else:
                type_args.append(self._parse_type_annotation(s, type_param_scope))
        return tuple(type_args)

    def _parse_type_args_from_subscript(self, node: ast.Subscript) -> tuple[TpyType, ...]:
        """Extract type arguments from a subscript for generic function calls like first[Int32](x).

        Raises ParseError if any element is not a valid type. The caller should catch
        this for cases where non-type arguments are valid (e.g., Array[Int32, 8]).
        """
        slices = _extract_subscript_slices(node)

        # Parse each type argument - raise error if any fails
        type_args: list[TpyType | None] = []
        for s in slices:
            # _ wildcard: infer this type argument
            if isinstance(s, ast.Name) and s.id == '_':
                type_args.append(None)
                continue
            # Integer constants are not valid type arguments
            if isinstance(s, ast.Constant) and isinstance(s.value, int):
                raise ParseError(f"Integer '{s.value}' is not a valid type argument", s)
            # Variable names that aren't types
            if isinstance(s, ast.Name) and not self.registry.is_known_type(s.id) and s.id[0].islower():
                raise ParseError(f"'{s.id}' is not a valid type", s)
            type_args.append(self._parse_type_annotation(s))
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

    def _try_parse_type_args(self, node: ast.Subscript) -> tuple[tuple[TpyType, ...], str | None]:
        """Try to parse type args from a subscript, capturing parse errors.

        Returns (type_args, parse_error). On success parse_error is None.
        On failure type_args is empty and parse_error holds the message.
        """
        try:
            return self._parse_type_args_from_subscript(node), None
        except ParseError as e:
            return (), e.message

    def _parse_tuple_type(self, node: ast.Subscript, type_param_scope: dict[str, TypeParamKind] | None = None) -> TupleType:
        """Parse tuple[T1, T2, ...] type annotation."""
        slices = _extract_subscript_slices(node)
        if not slices:
            raise ParseError("tuple requires at least one type argument: tuple[T1, T2, ...]", node)
        element_types = tuple(
            self._parse_type_annotation(s, type_param_scope) for s in slices
        )
        return TupleType(element_types)

    def _parse_generic_type(self, node: ast.Subscript, name: str, type_def: BuiltinTypeDef, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyType:
        """Parse a module-defined generic type using its metadata."""
        param_kinds = type_def.param_kinds
        expected_count = len(param_kinds)
        slices = _extract_subscript_slices(node)

        if len(slices) != expected_count:
            raise ParseError(f"{name} requires exactly {expected_count} type parameters", node)

        # Parse each parameter according to its kind
        parsed_args: list[TpyType | int] = []
        for i, (slice_node, kind) in enumerate(zip(slices, param_kinds)):
            if kind == TypeParamKind.TYPE:
                parsed_args.append(self._parse_type_annotation(slice_node, type_param_scope))
            elif kind == TypeParamKind.INT:
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int):
                    parsed_args.append(slice_node.value)
                elif isinstance(slice_node, ast.Name) and type_param_scope and slice_node.id in type_param_scope:
                    # Allow forwarded INT type params (e.g., Array[T, N] where N: int)
                    param_name = slice_node.id
                    param_kind = type_param_scope[param_name]
                    if param_kind == TypeParamKind.INT:
                        parsed_args.append(TypeParamRef(param_name, kind=TypeParamKind.INT))
                    else:
                        raise ParseError(f"{name} parameter {i + 1} requires an integer, got type parameter '{param_name}'", node)
                else:
                    raise ParseError(f"{name} parameter {i + 1} must be an integer literal or int type parameter", node)

        assert type_def.type_factory is not None
        try:
            return type_def.type_factory(*parsed_args)
        except ParseError:
            raise
        except Exception as e:
            raise ParseError(f"Failed to construct type {name}: {e}", node) from e

    def _parse_stmt(self, node: ast.stmt) -> TpyStmt:
        """Parse a statement."""
        loc = self._loc(node)

        if isinstance(node, ast.AnnAssign):
            # Annotated assignment: x: T = expr
            if not isinstance(node.target, ast.Name):
                raise ParseError("Invalid assignment target", node)
            var_type = self._parse_type_annotation(node.annotation)
            init_expr = self._parse_expr(node.value) if node.value else None
            return TpyVarDecl(node.target.id, var_type, init_expr, loc=loc)

        elif isinstance(node, ast.Assign):
            # Simple assignment: x = expr or x.field = expr
            if len(node.targets) != 1:
                raise ParseError("Multiple assignment targets not supported", node)
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
            then_body = [self._parse_stmt(s) for s in node.body]
            else_body = [self._parse_stmt(s) for s in node.orelse]
            return TpyIf(cond, then_body, else_body, loc=loc)

        elif isinstance(node, ast.While):
            cond = self._parse_expr(node.test)
            body = [self._parse_stmt(s) for s in node.body]
            orelse = [self._parse_stmt(s) for s in node.orelse]
            return TpyWhile(cond, body, orelse=orelse, loc=loc)

        elif isinstance(node, ast.For):
            orelse = [self._parse_stmt(s) for s in node.orelse]
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
                body = [self._parse_stmt(s) for s in node.body]
                unpack = TpyTupleUnpack(
                    targets=targets,
                    value=TpyName(synth_var, loc=loc),
                    loc=loc,
                )
                return TpyForEach(synth_var, iterable, [unpack] + body, orelse=orelse, loc=loc, is_tuple_unpack=True)
            if not isinstance(node.target, ast.Name):
                raise ParseError("For loop target must be a simple variable", node)
            var = node.target.id
            body = [self._parse_stmt(s) for s in node.body]
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
                handlers.append(TpyExceptHandler(
                    exception_type=None, binding=h.name,
                    body=[self._parse_stmt(s) for s in h.body], loc=h_loc))
            elif isinstance(h.type, ast.Name):
                handlers.append(TpyExceptHandler(
                    exception_type=h.type.id, binding=h.name,
                    body=[self._parse_stmt(s) for s in h.body], loc=h_loc))
            else:
                raise ParseError("'except' requires a simple name (e.g. 'except MyError')", node)
        try_body = [self._parse_stmt(s) for s in node.body]
        else_body = [self._parse_stmt(s) for s in node.orelse]
        finally_body = [self._parse_stmt(s) for s in node.finalbody]
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
        body = [self._parse_stmt(s) for s in node.body]
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
        return TpyNestedDef(func=func, loc=loc)

    def _parse_delete(self, node: ast.Delete, loc: SourceLocation | None) -> TpyStmt:
        """Parse a del statement. Only subscript targets are supported."""
        subscripts: list[TpySubscript] = []
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                obj = self._parse_expr(target.value)
                index = self._parse_expr(target.slice)
                subscripts.append(TpySubscript(obj, index, loc=loc))
            elif isinstance(target, ast.Name):
                raise ParseError("'del' on variables is not supported", node)
            elif isinstance(target, ast.Attribute):
                raise ParseError("'del' on attributes is not supported", node)
            else:
                raise ParseError(f"Unsupported del target: {type(target).__name__}", node)
        return TpyDelItem(subscripts, loc=loc)

    def _parse_match(self, node: ast.Match, loc: SourceLocation | None) -> TpyMatch:
        """Parse a match/case statement."""
        subject = self._parse_expr(node.subject)
        cases: list[TpyMatchCase] = []
        for case in node.cases:
            pattern = self._parse_pattern(case.pattern)
            guard = self._parse_expr(case.guard) if case.guard else None
            body = [self._parse_stmt(s) for s in case.body]
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
            args = [self._parse_expr(a) for a in node.args]
            kwargs = {}

            if node.keywords:
                for kw in node.keywords:
                    if kw.arg is None:
                        raise ParseError("**kwargs unpacking not supported", node)
                    if kw.arg in kwargs:
                        raise ParseError(f"Keyword argument '{kw.arg}' repeated", node)
                    kwargs[kw.arg] = self._parse_expr(kw.value)

            if isinstance(node.func, ast.Name):
                return TpyCall(TpyName(node.func.id, loc=loc), args, kwargs=kwargs, loc=loc)
            elif isinstance(node.func, ast.Attribute):
                # ClassName[TypeArgs].method(args) -- static call with explicit class type args
                if (isinstance(node.func.value, ast.Subscript)
                        and isinstance(node.func.value.value, ast.Name)
                        and node.func.value.value.id[0].isupper()):
                    name = node.func.value.value.id
                    type_args, type_args_parse_error = self._try_parse_type_args(node.func.value)
                    if type_args or type_args_parse_error:
                        return TpyMethodCall(
                            TpyName(name, loc=loc), node.func.attr, args,
                            kwargs=kwargs,
                            type_args=type_args, type_args_parse_error=type_args_parse_error,
                            loc=loc,
                        )
                    # No type args and no error: fall through (e.g., variable[index].method())
                obj = self._parse_expr(node.func.value)
                return TpyMethodCall(obj, node.func.attr, args, kwargs=kwargs, loc=loc)
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
                    # If name looks like a type (starts with uppercase or is registered), also parse as call_type
                    call_type = None
                    if name[0].isupper() or self.registry.is_known_type(name):
                        try:
                            call_type = self._parse_type_annotation(node.func)
                        except ParseError:
                            # call_type parsing failed - if type_args also failed, sema will report
                            # the type_args_parse_error; otherwise it's a function call
                            pass
                    # When type args fail, also parse the subscript as an expression
                    # so sema can fall back to expression callee (e.g., fns[0](args))
                    subscript_callee = None
                    if type_args_parse_error and not type_args and call_type is None:
                        subscript_callee = self._parse_expr(node.func)
                    return TpyCall(TpyName(name, loc=loc), args, call_type=call_type, type_args=type_args,
                                   type_args_parse_error=type_args_parse_error,
                                   subscript_callee=subscript_callee, kwargs=kwargs, loc=loc)
                elif isinstance(node.func.value, ast.Attribute):
                    # module.func[T](args) -- method call with explicit type args
                    obj = self._parse_expr(node.func.value.value)
                    method = node.func.value.attr
                    type_args, type_args_parse_error = self._try_parse_type_args(node.func)
                    return TpyMethodCall(obj, method, args, kwargs=kwargs,
                                         type_args=type_args,
                                         type_args_parse_error=type_args_parse_error, loc=loc)
                # Expression callee with subscript: expr[i](args)
                expr_func = self._parse_expr(node.func)
                return TpyCall(expr_func, args, kwargs=kwargs, loc=loc)
            else:
                # Expression callee: f()(x), (lambda: fn)()(), etc.
                expr_func = self._parse_expr(node.func)
                return TpyCall(expr_func, args, kwargs=kwargs, loc=loc)

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
                from tpyc.modules import lookup_generic_type as _lookup_generic_type
                if _lookup_generic_type(name) is not None:
                    raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
                # Generic types from imported builtin submodules (tpy.mem, etc.)
                if import_src := self._imports.get_import_source(name):
                    if lookup_generic_type_in_module(import_src[1], import_src[0]) is not None:
                        raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
            obj = self._parse_expr(node.value)
            if isinstance(node.slice, ast.Slice):
                sl = node.slice
                if sl.step is not None:
                    raise ParseError("Slice step is not yet supported", node)
                lower = self._parse_expr(sl.lower) if sl.lower is not None else None
                upper = self._parse_expr(sl.upper) if sl.upper is not None else None
                index = TpySlice(lower=lower, upper=upper, loc=loc)
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
                             TpyStrLiteral, TpyNoneLiteral, TpyTypeParamConstruct)):
            return
        if isinstance(expr, TpyUnaryOp) and expr.op == "-":
            if isinstance(expr.operand, (TpyIntLiteral, TpyFloatLiteral)):
                return
        # Int32(5) etc. -- a fixed-int constructor wrapping a literal
        if isinstance(expr, TpyCall) and expr.func_name in _FIXED_INT_MAP:
            if not expr.args:
                return  # Int32() -> 0
            if len(expr.args) == 1:
                self._validate_const_default(expr.args[0], node)
                return
        raise ParseError(
            f"Default parameter value must be a constant expression "
            f"(literal, None, or fixed-int constructor like Int32(5))", node)

    def _parse_param_defaults(self, node: ast.FunctionDef, params: list,
                              skip_self: bool = False,
                              type_param_scope: dict | None = None,
                              ) -> list['TpyExpr | None']:
        """Parse default values from a function definition.

        Returns a list aligned with params: None for params without defaults.
        Python's ast.arguments.defaults is right-aligned with args, so we
        left-pad with None.
        """
        ast_defaults = node.args.defaults
        if not ast_defaults:
            return [None] * len(params)

        # In methods, self is skipped from params but still counted in node.args.args
        num_ast_args = len(node.args.args)
        # defaults are right-aligned with the full args list
        num_no_default = num_ast_args - len(ast_defaults)

        defaults: list[TpyExpr | None] = []
        param_offset = 1 if skip_self else 0  # skip self in index mapping
        for i in range(len(params)):
            ast_idx = i + param_offset  # index into node.args.args
            default_idx = ast_idx - num_no_default
            if default_idx >= 0 and default_idx < len(ast_defaults):
                expr = self._parse_expr(ast_defaults[default_idx])
                # Detect T() where T is a type parameter
                if (isinstance(expr, TpyCall) and not expr.args and not expr.kwargs
                        and type_param_scope and expr.func_name in type_param_scope):
                    expr = TpyTypeParamConstruct(expr.func_name, loc=expr.loc)
                self._validate_const_default(expr, ast_defaults[default_idx])
                defaults.append(expr)
            else:
                defaults.append(None)
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
            if node.func.id in _FIXED_INT_MAP:
                if not node.args:
                    return "0"
                return self._get_default_value(node.args[0])
            args = ", ".join(str(self._get_default_value(a)) for a in node.args)
            return f"{node.func.id}({args})"
        return "0"

    def _infer_type_from_expr(self, node: ast.expr) -> Optional[TpyType]:
        """Infer type from an expression (for field declarations without annotations)."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return BIGINT
            elif isinstance(node.value, float):
                return FLOAT
            elif isinstance(node.value, str):
                return STR
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            type_name = node.func.id
            if (fixed_int := _FIXED_INT_MAP.get(type_name)) is not None:
                return fixed_int
            if type_name == "int":
                return BIGINT
            if type_name == "float":
                return FLOAT
            # Check if it's a known record type
            record_info = self.registry.get_record(type_name)
            if record_info:
                return NamedType(type_name)
        return None
