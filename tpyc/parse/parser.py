"""
TurboPython Parser.

Uses CPython's ast module to parse TurboPython source code.
Validates that only allowed constructs are used.
"""

from __future__ import annotations
import ast
from typing import NoReturn, Optional

from ..typesys import (
    TpyType, NamedType, PtrType, OwnType, ReadonlyType, FinalType,
    TypeParamRef, OptionalType, VoidType, make_union, EnumType, TupleType,
    INT32, VOID, STR, STRING, STRVIEW, CHAR, BOOL, FLOAT, BIGINT, SELF, FieldInfo, RecordInfo, TypeRegistry,
    MethodSignature, ProtocolInfo, TypeParamKind,
    INT8, INT16, INT64, UINT8, UINT16, UINT32, UINT64, ALL_FIXED_INTS,
)
from ..modules import lookup_generic_type, lookup_generic_type_in_module, lookup_protocol as lookup_builtin_protocol, BuiltinTypeDef
from .nodes import (
    ParseError, SourceLocation, ParseWarning, RecordLinkage, FunctionLinkage,
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_ASCII,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyTypeParamConstruct, TpyCall, TpyMethodCall,
    TpyFieldAccess, TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral, TpyListRepeat, TpySlice, TpySubscript, TpyCoerce,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyExprStmt, TpyReturn,
    TpyAssert, TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue,
    TpyPassStmt, TpyGlobal, TpyRaiseStopIteration,
    RelativeImportKey, TpyImport, TpyFunction, TpyRecord, TpyProtocol, TpyEnum, TpyModule,
)
from .imports import (
    ImportProcessor, SPECIAL_MODULES,
    PYTHON_BUILTINS, TYPING_NAMES, TPY_TYPE_NAMES, TPY_TYPES,
)

# Map of fixed-int type names to their singleton instances
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
    ast.USub: "-", ast.Not: "!", ast.Invert: "~",
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


def _collect_bitor_arms(node: ast.BinOp) -> list[ast.expr]:
    """Flatten a left-recursive chain of A | B | C into [A, B, C]."""
    arms: list[ast.expr] = []
    if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.BitOr):
        arms.extend(_collect_bitor_arms(node.left))
    else:
        arms.append(node.left)
    arms.append(node.right)
    return arms


class Parser:
    """Parser for TurboPython source code."""

    FORBIDDEN_CONSTRUCTS = {
        "set",
        "try", "with", "async", "await",
        "lambda", "yield", "nonlocal",
    }

    def __init__(self):
        self.registry = TypeRegistry()
        self.source_lines: list[str] = []
        self._type_param_scope: dict[str, TypeParamKind] | None = None
        self._warnings: list[ParseWarning] = []
        self._imports = ImportProcessor(self._warn)
        self._module_aliases: dict[str, str] = {}
        self._bare_module_imports: set[str] = set()
        self._reverse_module_aliases: dict[str, str] = {}
        self._for_unpack_counter: int = 0

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

    def _resolve_type_name(self, local_name: str) -> tuple[str, str] | None:
        """Resolve annotation name -> (module, original_name) or None.

        Checks imports first, then tpy star import, then Python builtins.
        """
        source = self._imports.get_import_source(local_name)
        if source:
            return source

        if self._imports.tpy_star_import and local_name in TPY_TYPE_NAMES:
            return ("tpy", local_name)

        if local_name in PYTHON_BUILTINS:
            return ("builtins", local_name)

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
        if canonical not in SPECIAL_MODULES:
            return None
        # Verify the module was bare-imported (imports[canonical] is None means
        # whole-module import; the key being absent means no import at all)
        imports = self._imports.imports
        if imports is None or canonical not in imports or imports[canonical] is not None:
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
            elif original == "None": return VOID
            elif original == "tuple":
                raise ParseError("tuple requires type arguments: tuple[T1, T2, ...]", node)
        elif module == "tpy":
            if (fixed_int := _FIXED_INT_MAP.get(original)) is not None:
                return fixed_int
            elif original == "Char":
                return CHAR
            elif original == "String":
                return STRING
            elif original == "StrView":
                return STRVIEW
        elif module == "typing":
            if original == "Self":
                return SELF
            elif original == "Protocol":
                raise ParseError("'Protocol' cannot be used as a type annotation", node)
        return None

    def _resolve_registered_type(self, name: str, node: ast.expr) -> TpyType | None:
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
        elif (protocol_def := lookup_builtin_protocol(name)) is not None:
            if protocol_def.type_params:
                raise ParseError(
                    f"Generic protocol '{name}' requires type arguments: "
                    f"{name}[{', '.join(protocol_def.type_params)}]",
                    node
                )
            return NamedType(name, is_protocol=True)
        elif self.registry.is_known_type(name) or name[0].isupper():
            return NamedType(name)
        return None

    def _raise_unresolved_import_error(self, raw_name: str, node: ast.expr) -> None:
        """Raise a helpful error for unresolved type names with import hints."""
        if raw_name in TYPING_NAMES:
            raise ParseError(f"'{raw_name}' requires: from typing import {raw_name}", node)
        if raw_name in TPY_TYPES:
            raise ParseError(
                f"'{raw_name}' is not defined. Did you mean: from tpy import {raw_name}",
                node
            )

    def _raise_unresolved_qualified_error(self, node: ast.expr) -> None:
        """Raise error for qualified names where the module wasn't imported."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mod = node.value.id
            canonical = self._reverse_module_aliases.get(mod, mod)
            qualified = f"{mod}.{node.attr}"
            if canonical in SPECIAL_MODULES:
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

    def parse(self, source: str) -> TpyModule:
        """Parse TurboPython source code into a TpyModule."""
        self.source_lines = source.splitlines()
        self._warnings = []
        self._imports = ImportProcessor(self._warn)
        self._module_aliases = {}
        self._bare_module_imports = set()
        self._reverse_module_aliases = {}
        tree = ast.parse(source)
        return self._parse_module(tree)

    # Names that _parse_type_annotation resolves directly (not through registry)
    _BUILTIN_TYPE_NAMES = frozenset({
        "int", "float", "bool", "str", "None", "tuple",
    })

    def _is_type_name(self, name: str) -> bool:
        """Check if a name is recognizable as a type by _parse_type_annotation."""
        resolved = self._resolve_type_name(name)
        if resolved:
            original = resolved[1]
            if original in self._BUILTIN_TYPE_NAMES or original in _FIXED_INT_MAP:
                return True
            if original == "Char" or original == "Self":
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
                # Warn if class shadows an imported special name
                source = self._imports.get_import_source(node.name)
                if source and source[0] in SPECIAL_MODULES:
                    self._warn(f"class '{node.name}' shadows import from '{source[0]}'", node)
                result = self._parse_class(node)
                if isinstance(result, TpyProtocol):
                    protocols.append(result)
                    # Register the protocol type
                    self.registry.register_protocol(ProtocolInfo(
                        name=result.name,
                        methods=result.methods,
                        type_params=result.type_params
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
                        has_init=result.init_method is not None
                    ))
            elif isinstance(node, ast.FunctionDef):
                seen_non_import = True
                func = self._parse_function(node)
                functions.append(func)
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

        return TpyModule(records=records, functions=functions, protocols=protocols, enums=enums, top_level_stmts=top_level_stmts, source_lines=self.source_lines, imports=imports, user_module_imports=user_module_imports, module_aliases=module_aliases, bare_module_imports=bare_module_imports, type_aliases=type_aliases, parse_warnings=self._warnings)

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
                arg_value = self._BAD_ARGS
            elif not dec.args:
                arg_value = self._EMPTY_CALL
            elif len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant):
                arg_value = dec.args[0].value
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
        return f"{resolved[0]}.{resolved[1]}", resolved[2]

    def _parse_readonly_arg(self, arg: object, dec: ast.expr) -> tuple[bool, bool]:
        """Parse @readonly arg value -> (is_readonly, readonly_opt_out)."""
        if arg is None:
            return (True, False)
        if isinstance(arg, bool):
            return (arg, not arg)
        raise ParseError("@readonly() requires a single bool argument (True or False)", dec)

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

        # Parse record decorators (@native, @native_c, @nocopy)
        linkage = RecordLinkage.DEFAULT
        native_name: str | None = None
        is_nocopy = False
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"class '{node.name}'")
            if qname in self._RECORD_LINKAGE_MAP:
                new_linkage = self._RECORD_LINKAGE_MAP[qname]
                if linkage != RecordLinkage.DEFAULT:
                    raise ParseError(
                        f"Class '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                linkage = new_linkage
                if isinstance(arg, str):
                    native_name = arg
                elif arg is not None:
                    dec_name = self._decorator_local_name(dec)
                    raise ParseError(f"@{dec_name}() requires a single string argument", dec)
            elif qname == "tpy.nocopy":
                if arg is not None:
                    raise ParseError("@nocopy does not take arguments", dec)
                is_nocopy = True
            else:
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(f"Unknown decorator '{dec_name}' on class '{node.name}'", dec)

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
                            # Regular protocol bound
                            type_param_kinds.append(TypeParamKind.TYPE)
                            bound_type = self._parse_type_annotation(tp.bound)
                            if not isinstance(bound_type, NamedType) or not bound_type.is_protocol:
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
                if item.value is not None:
                    default_val = self._get_default_value(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val, loc=self._loc(item)))
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
                methods.append(self._parse_method(item, node.name, type_param_scope))
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
                if method.is_stub:
                    raise ParseError(
                        f"Method '{method.name}' cannot have '...' body on a regular class "
                        f"(only allowed on @native/@native_c classes)", node)
                if method.native_name is not None:
                    raise ParseError(
                        f"@native(\"...\") decorator on method '{method.name}' is only allowed "
                        f"on @native/@native_c classes", node)

        # Restore the scope
        self._type_param_scope = old_scope
        return TpyRecord(name=node.name, fields=fields, methods=methods, type_params=type_params, type_param_kinds=type_param_kinds, type_param_bounds=type_param_bounds, bases=bases, linkage=linkage, native_name=native_name, is_nocopy=is_nocopy, loc=self._loc(node))

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
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"protocol '{node.name}'")
            if qname == "tpy.dynamic":
                if arg is not None:
                    raise ParseError("@dynamic does not take arguments", dec)
                is_dynamic = True
                continue
            dec_name = self._decorator_local_name(dec) or "?"
            raise ParseError(
                f"Unsupported decorator '@{dec_name}' on protocol '{node.name}'. "
                f"Only @dynamic (from tpy) is allowed on protocols", dec)

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
                    if qname == "tpy.readonly":
                        is_readonly, readonly_opt_out = self._parse_readonly_arg(arg, dec)
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
        return TpyProtocol(name=node.name, methods=methods, fields=fields, type_params=type_params, parent_protocols=parent_protocols, is_dynamic=is_dynamic, loc=self._loc(node))

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
        "tpy.extern.native": RecordLinkage.NATIVE,
        "tpy.extern.native_c": RecordLinkage.NATIVE_C,
    }

    _METHOD_LINKAGE_MAP: dict[str, FunctionLinkage] = {
        "tpy.extern.native": FunctionLinkage.NATIVE,
        "tpy.extern.native_c": FunctionLinkage.NATIVE_C,
    }

    def _parse_method(self, node: ast.FunctionDef, class_name: str, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyFunction:
        """Parse a method definition."""
        # Check decorators (@staticmethod, @readonly, @native("cpp_name"))
        is_staticmethod = False
        is_readonly = False
        readonly_opt_out = False
        method_linkage = FunctionLinkage.DEFAULT
        native_name: str | None = None
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"method '{node.name}'")
            if qname == "builtins.staticmethod":
                is_staticmethod = True
            elif qname == "tpy.readonly":
                is_readonly, readonly_opt_out = self._parse_readonly_arg(arg, dec)
            elif qname in self._METHOD_LINKAGE_MAP:
                method_linkage = self._METHOD_LINKAGE_MAP[qname]
                if isinstance(arg, str):
                    native_name = arg
                elif arg is not None:
                    dec_name = self._decorator_local_name(dec)
                    raise ParseError(f"@{dec_name}() on method requires a single string argument", dec)
            else:
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(f"Unknown decorator '{dec_name}' on method '{node.name}'", dec)

        # Extract method-level type parameters (e.g. def foo[T](self, x: T) -> T:)
        method_type_params: list[str] = []
        method_type_param_bounds: dict[str, TpyType] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    method_type_params.append(tp.name)
                    if tp.bound is not None:
                        bound_type = self._parse_type_annotation(tp.bound)
                        if not isinstance(bound_type, NamedType) or not bound_type.is_protocol:
                            raise ParseError(f"Type parameter bound must be a protocol, got {bound_type}", tp)
                        method_type_param_bounds[tp.name] = bound_type
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Merge class-level and method-level type param scopes
        if method_type_params:
            merged_scope = dict(type_param_scope) if type_param_scope else {}
            for tp_name in method_type_params:
                merged_scope[tp_name] = TypeParamKind.TYPE
            type_param_scope = merged_scope

        params = []
        has_self = not is_staticmethod
        args_iter = iter(enumerate(node.args.args))
        for i, arg in args_iter:
            if i == 0 and has_self:
                # Non-static methods must have 'self' as first parameter
                if arg.arg != "self":
                    raise ParseError(f"First parameter of method '{node.name}' must be 'self'", node)
                continue
            if arg.annotation is None:
                raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
            param_type = self._parse_type_annotation(arg.annotation, type_param_scope)
            params.append((arg.arg, param_type))

        # Parse default parameter values (skip_self for non-static methods)
        defaults = self._parse_param_defaults(node, params, skip_self=has_self,
                                              type_param_scope=type_param_scope)

        # Get return type (default to Void for __init__)
        return_type = VOID
        if node.name != "__init__" and node.returns:
            return_type = self._parse_type_annotation(node.returns, type_param_scope)

        is_stub_body = self._is_stub_body(node.body)
        is_stub = is_stub_body
        if is_stub:
            body = []
        else:
            body = [self._parse_stmt(stmt) for stmt in node.body]

        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_method=True,
            is_staticmethod=is_staticmethod,
            is_readonly=is_readonly,
            readonly_opt_out=readonly_opt_out,
            is_stub=is_stub,
            linkage=method_linkage,
            native_name=native_name,
            type_params=method_type_params,
            type_param_bounds=method_type_param_bounds,
            defaults=defaults,
            loc=self._loc(node)
        )

    _FUNCTION_LINKAGE_MAP: dict[str, FunctionLinkage] = {
        "tpy.extern.native": FunctionLinkage.NATIVE,
        "tpy.extern.native_c": FunctionLinkage.NATIVE_C,
        "tpy.extern.extern_c": FunctionLinkage.EXTERN_C,
    }

    def _parse_function(self, node: ast.FunctionDef) -> TpyFunction:
        """Parse a function definition."""
        is_noalloc = False
        is_readonly = False
        readonly_opt_out = False
        linkage = FunctionLinkage.DEFAULT
        native_name: str | None = None
        for dec in node.decorator_list:
            qname, arg = self._require_decorator(dec, f"function '{node.name}'")
            if qname == "tpy.noalloc":
                if arg is not None:
                    raise ParseError("@noalloc does not take arguments", dec)
                is_noalloc = True
            elif qname == "tpy.readonly":
                is_readonly, readonly_opt_out = self._parse_readonly_arg(arg, dec)
            elif qname in self._FUNCTION_LINKAGE_MAP:
                new_linkage = self._FUNCTION_LINKAGE_MAP[qname]
                if linkage != FunctionLinkage.DEFAULT:
                    raise ParseError(
                        f"Function '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                linkage = new_linkage
                if isinstance(arg, str):
                    native_name = arg
                elif arg is not None:
                    dec_name = self._decorator_local_name(dec)
                    raise ParseError(f"@{dec_name}() requires a single string argument", dec)
            else:
                dec_name = self._decorator_local_name(dec) or "?"
                raise ParseError(f"Unknown decorator '{dec_name}' on function '{node.name}'", dec)

        # Extract type parameters from Python 3.12+ syntax: def foo[T, U]():
        # Also extract bounds: def foo[T: Comparable]():
        # Note: Functions don't currently support INT type params (only TYPE)
        type_params = []
        type_param_bounds: dict[str, TpyType] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                    if tp.bound is not None:
                        bound_type = self._parse_type_annotation(tp.bound)
                        if not isinstance(bound_type, NamedType) or not bound_type.is_protocol:
                            raise ParseError(f"Type parameter bound must be a protocol, got {bound_type}", tp)
                        type_param_bounds[tp.name] = bound_type
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Set scope for parsing parameter and return types (all TYPE kind for functions)
        type_param_scope = {tp: TypeParamKind.TYPE for tp in type_params} if type_params else None
        old_scope = self._type_param_scope
        self._type_param_scope = type_param_scope

        params = []
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
        is_stub = False

        if linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C):
            if not is_stub_body:
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

        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_noalloc=is_noalloc,
            is_readonly=is_readonly,
            readonly_opt_out=readonly_opt_out,
            linkage=linkage,
            native_name=native_name,
            is_stub=is_stub,
            type_params=type_params,
            type_param_bounds=type_param_bounds,
            defaults=defaults,
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
            else:
                self._raise_unresolved_import_error(name, node)

            # Registry lookups (user protocols, type aliases, builtin protocols, user records)
            resolved_name = resolved[1] if resolved else name
            registered = self._resolve_registered_type(resolved_name, node)
            if registered is not None:
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
                        # Ptr[readonly[T]] normalizes to ReadOnlyPtr[T]
                        if isinstance(inner, ReadonlyType):
                            return PtrType(inner.wrapped, is_const=True)
                        return PtrType(inner)
                    elif original == "ReadOnlyPtr":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return PtrType(inner, is_const=True)
                    elif original == "Own":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return OwnType(inner)
                    elif original == "readonly":
                        inner = self._parse_type_annotation(node.slice, type_param_scope)
                        return ReadonlyType(inner)
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
            else:
                if raw_name:
                    self._raise_unresolved_import_error(raw_name, node)
                # Qualified name with missing module import
                if isinstance(node.value, ast.Attribute):
                    self._raise_unresolved_qualified_error(node.value)

            # Use original name for registry lookups when resolved
            resolved_container = resolved[1] if resolved else raw_name

            if resolved_container:
                # Generic protocols (e.g., Sequence[Int32])
                if protocol_def := lookup_builtin_protocol(resolved_container):
                    if protocol_def.type_params:
                        type_args = self._parse_protocol_type_args(node, resolved_container, protocol_def.type_params, type_param_scope)
                        return NamedType(resolved_container, type_args, is_protocol=True)

                # Module-defined generic types (list, Array, Span, etc.)
                if lookup := lookup_generic_type(resolved_container):
                    return self._parse_generic_type(node, resolved_container, lookup.type_def, type_param_scope)

                # Generic types from explicitly imported builtin submodules (tpy.mem, etc.)
                if resolved and resolved[0] not in SPECIAL_MODULES:
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
                registered = self._resolve_registered_type(resolved[1], node)
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
        this for cases where non-type arguments are valid (e.g., StaticList[Int32, 8]).
        """
        slices = _extract_subscript_slices(node)

        # Parse each type argument - raise error if any fails
        type_args = []
        for s in slices:
            # Integer constants are not valid type arguments
            if isinstance(s, ast.Constant) and isinstance(s.value, int):
                raise ParseError(f"Integer '{s.value}' is not a valid type argument", s)
            # Variable names that aren't types
            if isinstance(s, ast.Name) and not self.registry.is_known_type(s.id) and s.id[0].islower():
                raise ParseError(f"'{s.id}' is not a valid type", s)
            type_args.append(self._parse_type_annotation(s))
        return tuple(type_args)

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
        for i, et in enumerate(element_types):
            if isinstance(et, (TypeParamRef, OwnType)):
                continue
            if not et.is_value_type():
                raise ParseError(
                    f"Tuple element {i} has type {et} which is a reference type; "
                    f"use Own[{et}] to store by value",
                    slices[i],
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
            return TpyWhile(cond, body, loc=loc)

        elif isinstance(node, ast.For):
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
                return TpyForEach(synth_var, iterable, [unpack] + body, loc=loc)
            if not isinstance(node.target, ast.Name):
                raise ParseError("For loop target must be a simple variable", node)
            var = node.target.id
            body = [self._parse_stmt(s) for s in node.body]
            iterable = self._parse_expr(node.iter)
            return TpyForEach(var, iterable, body, loc=loc)

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

        else:
            raise ParseError(f"Unsupported statement: {type(node).__name__}", node)

    def _parse_raise(self, node: ast.Raise, loc: SourceLocation | None) -> TpyStmt:
        """Parse a raise statement. Only `raise StopIteration` is supported."""
        exc = node.exc
        if exc is None:
            raise ParseError("'raise' requires an exception; only 'raise StopIteration' is supported", node)
        # raise StopIteration
        if isinstance(exc, ast.Name) and exc.id == "StopIteration":
            return TpyRaiseStopIteration(loc=loc)
        # raise StopIteration()
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "StopIteration":
            if exc.args or exc.keywords:
                raise ParseError("'raise StopIteration' does not accept arguments", node)
            return TpyRaiseStopIteration(loc=loc)
        raise ParseError("Only 'raise StopIteration' is supported", node)

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
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise ParseError("Chained comparisons not supported", node)
            left = self._parse_expr(node.left)
            right = self._parse_expr(node.comparators[0])
            op = self._cmpop_to_str(node.ops[0])
            return TpyBinOp(left, op, right, loc=loc)

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
                return TpyCall(node.func.id, args, kwargs=kwargs, loc=loc)
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
                    # (for type instantiations like StaticList[Int32, 8], non-type args are valid
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
                    return TpyCall(name, args, call_type=call_type, type_args=type_args,
                                   type_args_parse_error=type_args_parse_error, kwargs=kwargs, loc=loc)
                elif isinstance(node.func.value, ast.Attribute):
                    # module.func[T](args) -- method call with explicit type args
                    obj = self._parse_expr(node.func.value.value)
                    method = node.func.value.attr
                    type_args, type_args_parse_error = self._try_parse_type_args(node.func)
                    return TpyMethodCall(obj, method, args, kwargs=kwargs,
                                         type_args=type_args,
                                         type_args_parse_error=type_args_parse_error, loc=loc)
                raise ParseError("Unsupported generic call target", node)
            else:
                raise ParseError("Unsupported call target", node)

        elif isinstance(node, ast.Attribute):
            obj = self._parse_expr(node.value)
            return TpyFieldAccess(obj, node.attr, loc=loc)

        elif isinstance(node, ast.List):
            elements = [self._parse_expr(elt) for elt in node.elts]
            return TpyArrayLiteral(elements=elements, loc=loc)

        elif isinstance(node, ast.Dict):
            if any(k is None for k in node.keys):
                raise ParseError("Dict unpacking (**) is not supported", node)
            keys = [self._parse_expr(k) for k in node.keys]
            values = [self._parse_expr(v) for v in node.values]
            return TpyDictLiteral(keys=keys, values=values, loc=loc)

        elif isinstance(node, ast.Subscript):
            # Subscript can be indexing (values[i]) or type annotation (Array[T, N])
            # If the value is a name that's a known generic type, it's a type annotation context
            # Otherwise, it's indexing
            if isinstance(node.value, ast.Name):
                name = node.value.id
                # Fundamental generic types (not in module system)
                if name == "Own":
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
        if isinstance(expr, TpyCall) and expr.func in _FIXED_INT_MAP:
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
                        and type_param_scope and expr.func in type_param_scope):
                    expr = TpyTypeParamConstruct(expr.func, loc=expr.loc)
                self._validate_const_default(expr, ast_defaults[default_idx])
                defaults.append(expr)
            else:
                defaults.append(None)
        return defaults

    def _get_default_value(self, node: ast.expr) -> str:
        """Convert a field default value AST node to a C++ literal string (for FieldInfo.default_value)."""
        if isinstance(node, ast.Constant):
            val = node.value
            if isinstance(val, bool):
                return "true" if val else "false"
            elif isinstance(val, str):
                escaped = val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                return f'"{escaped}"'
            return str(val)
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
