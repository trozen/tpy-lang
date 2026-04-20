"""
TypeResolver -- name-to-TpyType resolution for parser-emitted TypeRefNodes.

Split out from parser.py in Phase F.3d so parser doesn't depend on typesys
type classes / primitive singletons. Parser constructs one TypeResolver
instance in __init__ and attaches it to TpyModule.resolver at the end of
each parse() call; sema's type_ops.resolve_type_ref delegates here.
Parse-time callers (@builtin_decorator stubs, nested defs, macro
fragments) also go through this object.

The resolver reads parser state (registry, imports, local_defs, ...) via a
back-reference so container growth during parse -- and re-assignment of
per-parse containers at the top of each parse() -- is visible on each
resolve() call.
"""
from __future__ import annotations
import ast
from typing import TYPE_CHECKING

from .typesys import (
    TpyType, NominalType, PtrType, OwnType, ReadonlyType, AutoReadonlyType,
    AutoOwnType, FinalType, OptionalType, VoidType, UnionType, TupleType,
    CallableType, make_union, make_fn_type,
    TypeParamRef, TypeParamKind, LiteralType,
    INT32, VOID, STR, STRING, STRVIEW, CHAR, BYTES, BYTEARRAY, BYTESVIEW,
    BOOL, FLOAT, FLOAT32, BIGINT, SELF, BASIC_SLICE, SLICE,
    ALL_FIXED_INTS,
)
from . import qnames
from .modules import lookup_generic_type, lookup_generic_type_in_module, BuiltinTypeDef
from .modules.type_resolution import get_type_factory_param_kinds
from .parse.nodes import (
    ParseError, SourceLocation,
    TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef, ResolverInputNode,
)
from .parse.imports import _IMPLICIT_MODULES

if TYPE_CHECKING:
    from .parse.parser import Parser


# Map of fixed-int type names to their singleton instances. Also used by
# parser-side code (_get_default_value, _validate_const_default,
# _infer_type_from_expr) via re-export from parser.py.
_FIXED_INT_MAP: dict[str, TpyType] = {str(t): t for t in ALL_FIXED_INTS}


class TypeResolver:
    """Resolves TypeRefNode -> TpyType using live parser state.

    Constructed once per parse() with a back-reference to the Parser; reads
    registry, imports, local_defs, module_class_names, nested_type_scope,
    module_type_alias_names, bare_module_imports, reverse_module_aliases from
    the parser instance each call so growth during parse is visible.

    Two parser-owned helpers (`_resolve_type_name`,
    `_raise_unresolved_import_error`) are kept on Parser so FragmentParser can
    override them for lenient macro-fragment parsing; this resolver calls into
    them via the parser back-reference.
    """

    def __init__(self, parser: 'Parser'):
        self._parser = parser
        # Active alias-body context: set by sema's alias pass while resolving
        # the RHS of `type X = ...`; consulted by `_resolve_registered_type`
        # so same-body self-references (e.g. `list[X]` inside X's RHS)
        # produce a NominalType(name) placeholder rather than an "Unknown
        # type" error.  Recursive calls inside resolve() inherit this naturally.
        self._pending_alias: str | None = None

    @property
    def registry(self):
        """Expose the parser's TypeRegistry so sema can register resolved
        aliases / read bookkeeping without reaching through `_parser`."""
        return self._parser.registry

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def resolve(
        self, ref: ResolverInputNode,
        type_param_scope: dict[str, TypeParamKind] | None = None,
        *, pending_alias: str | None = None,
    ) -> TpyType:
        """Resolve a parser-walker-emitted type reference to a TpyType.

        Accepts `ResolverInputNode` (the four walker outputs: TpyTypeRef,
        TpyUnionRef, TpyCallableRef, TpyLiteralRef).  `TpyInferFromDefaultRef`
        is deliberately *not* a valid input -- it's a field-storage marker
        that sema catches explicitly in its field loop and never routes
        through the resolver.

        `pending_alias`, when set (typically by sema's alias pass), enables
        same-body self-ref placeholder behaviour for recursive aliases like
        `type JsonValue = str | list[JsonValue]`.  It is saved on the
        resolver for the duration of this call so recursive re-entries
        inherit the context, and restored on exit.
        """
        if pending_alias is not None:
            prev = self._pending_alias
            self._pending_alias = pending_alias
            try:
                return self._resolve_ref(ref, type_param_scope)
            finally:
                self._pending_alias = prev
        return self._resolve_ref(ref, type_param_scope)

    def _resolve_ref(
        self, ref: ResolverInputNode,
        type_param_scope: dict[str, TypeParamKind] | None,
    ) -> TpyType:
        """Internal resolver.  Recursive calls to `self.resolve(...)` without
        `pending_alias` re-enter the public method, which skips the
        save/restore and forwards straight here."""
        parser = self._parser
        if type_param_scope is None:
            type_param_scope = parser._type_param_scope

        # Union
        if isinstance(ref, TpyUnionRef):
            parsed = [self.resolve(m, type_param_scope) for m in ref.members]
            non_none = [t for t in parsed if not isinstance(t, VoidType)]
            if len(non_none) > 1:
                for t in non_none:
                    if isinstance(t, OwnType):
                        unwrapped = [
                            p.wrapped if isinstance(p, OwnType) else p for p in non_none
                        ]
                        raise ParseError(
                            f"Own[{t.wrapped}] cannot be a union member. "
                            f"Use Own[...] around the whole union instead: "
                            f"Own[{' | '.join(str(u) for u in unwrapped)}]",
                            loc=ref.loc,
                        )
            readonly_count = sum(1 for t in parsed if isinstance(t, ReadonlyType))
            non_none_count = sum(1 for t in parsed if not isinstance(t, VoidType))
            if readonly_count > 0 and readonly_count < non_none_count:
                raise ParseError(
                    "Cannot mix readonly and non-readonly types in a union",
                    loc=ref.loc,
                )
            if readonly_count > 0:
                unwrapped = [
                    t.wrapped if isinstance(t, ReadonlyType) else t for t in parsed
                ]
                return ReadonlyType(make_union(*unwrapped))
            return make_union(*parsed)

        # Callable / Fn
        if isinstance(ref, TpyCallableRef):
            param_types = tuple(
                self.resolve(p, type_param_scope) for p in ref.params
            )
            return_type = self.resolve(ref.return_type, type_param_scope)
            if ref.kind == "Fn":
                return make_fn_type(param_types, return_type)
            return CallableType(param_types, return_type)

        # Literal
        if isinstance(ref, TpyLiteralRef):
            tag = ref.values[0].tag
            base_type = {"str": STR, "int": INT32, "bool": BOOL}[tag]
            return LiteralType(base_type, ref.values)

        # TpyTypeRef
        assert isinstance(ref, TpyTypeRef), f"Unknown ref kind: {type(ref).__name__}"
        name = ref.name

        # "None" (void) -- emitted by walker for ast.Constant(None)
        if name == "None" and not ref.args:
            return VOID

        # Structural wrappers (canonical `mod:Name` names set by the walker
        # only when the source name actually resolved to the expected module).
        # The `:` separator avoids any collision with raw user-source names
        # (including dotted forms like "typing.Optional" written without
        # `import typing`), so raw-name TpyTypeRefs fall through to the
        # generic path where the resolver's unresolved-name error fires.
        if ref.args and name in (
            "tpy:Ptr", "tpy:Own", "tpy:readonly", "tpy:auto_readonly",
            "tpy:auto_own", "typing:Optional", "typing:Final",
        ):
            inner_arg = ref.args[0]
            assert not isinstance(inner_arg, int), \
                f"structural wrapper {name} cannot take int arg"
            inner = self.resolve(inner_arg, type_param_scope)
            if name == "tpy:Ptr":
                if isinstance(inner, ReadonlyType):
                    return PtrType(inner.wrapped, is_readonly=True)
                return PtrType(inner)
            if name == "tpy:Own":
                return OwnType(inner)
            if name == "tpy:readonly":
                return ReadonlyType(inner)
            if name == "tpy:auto_readonly":
                return AutoReadonlyType(inner)
            if name == "tpy:auto_own":
                return AutoOwnType(inner)
            if name == "typing:Optional":
                return OptionalType(inner)
            if name == "typing:Final":
                return FinalType(inner)

        # tuple
        if name == "builtins:tuple" and ref.args:
            elements = tuple(
                self.resolve(a, type_param_scope)
                for a in ref.args if not isinstance(a, int)
            )
            return TupleType(elements)

        # Bare name (no args)
        if not ref.args:
            # Type param scope
            if type_param_scope and name in type_param_scope:
                kind = type_param_scope[name]
                return TypeParamRef(name, kind=kind)

            # Dotted (qualified or nested class)
            if "." in name:
                parts = name.split(".")
                # 2-level qualified (mod.Attr)
                if len(parts) == 2:
                    mod, attr = parts
                    resolved_q = self._resolve_qualified_type_name_str(mod, attr)
                    if resolved_q:
                        primitive = self._resolve_primitive_type(*resolved_q, loc=ref.loc)
                        if primitive is not None:
                            return primitive
                        registered = self._resolve_registered_type(
                            resolved_q[1], loc=ref.loc, resolved=True)
                        if registered is not None:
                            return registered
                # Nested dotted class: Outer.Inner, Outer.Mid.Inner, ...
                dotted = self._resolve_dotted_class_name_str(name)
                if dotted is not None:
                    if parser.registry.get_record(dotted) is not None:
                        return NominalType(dotted)
                    enum_type = parser.registry.get_enum(dotted)
                    if enum_type is not None:
                        return enum_type
                # Helpful error for unimported implicit module
                self._raise_unresolved_qualified_error_str(name, ref.loc)
                raise ParseError(f"Unsupported qualified type: {name}", loc=ref.loc)

            # Bare name resolution
            resolved = parser._resolve_type_name(name)
            if resolved:
                primitive = self._resolve_primitive_type(*resolved, loc=ref.loc)
                if primitive is not None:
                    return primitive
            resolved_name = resolved[1] if resolved else name
            registered = self._resolve_registered_type(
                resolved_name, loc=ref.loc, resolved=bool(resolved))
            if registered is not None:
                if (resolved and isinstance(registered, NominalType)
                        and not registered.is_protocol
                        and not registered._module_qname):
                    module, original = resolved
                    candidate_qname = f"{module}.{original}"
                    is_builtin = (
                        parser.registry.get_builtin_type_key(registered.name) is not None
                        or get_type_factory_param_kinds(candidate_qname) is not None
                    )
                    if is_builtin:
                        registered = NominalType(
                            registered.name, registered.type_args,
                            registered.is_protocol, candidate_qname,
                            registered.is_dynamic_protocol,
                        )
                return registered
            raise ParseError(f"Unknown type: {name}", loc=ref.loc)

        # Generic form (name + args) -- bare or dotted
        if "." in name:
            parts = name.split(".")
            if len(parts) == 2:
                mod, attr = parts
                resolved = self._resolve_qualified_type_name_str(mod, attr)
            else:
                resolved = None
        else:
            resolved = parser._resolve_type_name(name)

        resolved_container = resolved[1] if resolved else name

        # Module-defined generic types (list, Array, Span, ...)
        if resolved_container and (lookup := lookup_generic_type(resolved_container)):
            if not resolved and lookup.qualified_name.startswith("tpy."):
                parser._raise_unresolved_import_error(name, loc=ref.loc)
            return self._resolve_generic_type_from_ref(
                ref, resolved_container, lookup.type_def, type_param_scope)

        # Imported generic from a module
        if resolved:
            source_module, original_name = resolved
            if lookup := lookup_generic_type_in_module(original_name, source_module):
                return self._resolve_generic_type_from_ref(
                    ref, resolved_container, lookup.type_def, type_param_scope)
        elif "." not in name and name:
            if import_source := parser._imports.get_import_source(name):
                source_module, original_name = import_source
                if lookup := lookup_generic_type_in_module(original_name, source_module):
                    return self._resolve_generic_type_from_ref(
                        ref, resolved_container, lookup.type_def, type_param_scope)

        # Qualified name with missing module import -- only for dotted subscripts
        # where qualified resolution failed.
        if "." in name and resolved is None:
            self._raise_unresolved_qualified_error_str(name, ref.loc)

        # User-defined generic protocols
        if user_protocol := parser.registry.get_protocol(resolved_container):
            if user_protocol.type_params:
                expected = len(user_protocol.type_params)
                if len(ref.args) != expected:
                    raise ParseError(
                        f"{resolved_container} requires exactly {expected} type parameters",
                        loc=ref.loc,
                    )
                type_args = tuple(
                    a if isinstance(a, int) else self.resolve(a, type_param_scope)
                    for a in ref.args
                )
                return NominalType(resolved_container, type_args, is_protocol=True)

        # Unresolved bare (non-dotted) name -- helpful import hint
        if not resolved and "." not in name and name:
            parser._raise_unresolved_import_error(name, loc=ref.loc)
        if (resolved or parser.registry.get_record(resolved_container) is not None
                or resolved_container in parser._module_class_names):
            type_args = self._resolve_record_type_args_from_ref(
                ref, resolved_container, type_param_scope)
            return NominalType(resolved_container, type_args)

        # Nested generic records (Outer.Inner[T])
        if "." in name:
            dotted = self._resolve_dotted_class_name_str(name)
            if dotted is not None and parser.registry.get_record(dotted) is not None:
                type_args = self._resolve_record_type_args_from_ref(
                    ref, dotted, type_param_scope)
                return NominalType(dotted, type_args)

        raise ParseError(f"Unknown generic type: {name}", loc=ref.loc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_primitive_type(
        self, module: str, original: str,
        node: ast.expr | None = None, *, loc: SourceLocation | None = None,
    ) -> TpyType | None:
        """Resolve a (module, original_name) pair to a primitive type.

        Returns the type if it's a directly-mapped primitive (int -> BIGINT, etc.),
        or None if it should fall through to registry lookups.
        Raises ParseError for names that can't be used as types (Protocol).
        Either `node` (ast-based) or `loc` (ref-based) may be passed for
        error reporting.
        """
        if module == "builtins":
            if original == "int": return BIGINT
            elif original == "float": return FLOAT
            elif original == "bool": return BOOL
            elif original == "str": return STR
            elif original == "bytes": return BYTES
            elif original == "bytearray": return BYTEARRAY
            elif original == "basic_slice": return BASIC_SLICE
            elif original == "slice": return SLICE
            elif original == "None": return VOID
            elif original == "type": return NominalType("type", _module_qname=qnames.TYPE)
            elif original == "tuple":
                raise ParseError("tuple requires type arguments: tuple[T1, T2, ...]", node, loc=loc)
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
            elif original == "basic_slice":
                return BASIC_SLICE
            elif original == "slice":
                return SLICE
        elif module == "typing":
            if original == "Self":
                return SELF
            elif original == "Protocol":
                raise ParseError("'Protocol' cannot be used as a type annotation", node, loc=loc)
        return None

    def _resolve_registered_type(
        self, name: str, node: ast.expr | None = None,
        *, resolved: bool = False, loc: SourceLocation | None = None,
    ) -> TpyType | None:
        """Look up a name in the type registry (protocols, aliases, records).

        Raises ParseError for generic protocols used without type arguments,
        or for completely unknown names. Either `node` or `loc` may be passed
        for error reporting.
        """
        parser = self._parser
        # Self-reference in a recursive type alias (e.g. list[JsonValue] inside
        # the definition of JsonValue). Return a NominalType placeholder that
        # survives inside container types and is detected post-parse.
        if self._pending_alias is not None and name == self._pending_alias:
            return NominalType(name)
        # Resolve short nested type names: Kind -> Message.Kind
        if name in parser._nested_type_scope:
            dotted = parser._nested_type_scope[name]
            if (enum_type := parser.registry.get_enum(dotted)) is not None:
                return enum_type
            if parser.registry.get_record(dotted) is not None:
                return NominalType(dotted)
        if (user_protocol := parser.registry.get_protocol(name)) is not None:
            if user_protocol.type_params:
                raise ParseError(
                    f"Generic protocol '{name}' requires type arguments: "
                    f"{name}[{', '.join(user_protocol.type_params)}]",
                    node, loc=loc,
                )
            return NominalType(name, is_protocol=True)
        elif (enum_type := parser.registry.get_enum(name)) is not None:
            return enum_type
        elif (alias := parser.registry.get_type_alias(name)) is not None:
            return alias
        elif not resolved:
            parser._raise_unresolved_import_error(name, node, loc=loc)
        if (resolved or parser.registry.is_known_type(name)
                or name in parser._module_class_names
                or name in parser._module_type_alias_names):
            return NominalType(name)
        return None

    def _resolve_qualified_type_name_str(
        self, local_module: str, attr: str,
    ) -> tuple[str, str] | None:
        """Loc-agnostic variant of _resolve_qualified_type_name: takes
        the module-local name and attribute directly."""
        parser = self._parser
        if local_module in parser._local_defs:
            return None
        canonical = parser._reverse_module_aliases.get(local_module, local_module)
        imports = parser._imports.imports
        if imports is None or canonical not in imports:
            return None
        entry = imports[canonical]
        if entry is not None and not (
                isinstance(entry, set) and canonical in parser._bare_module_imports):
            return None
        return (canonical, attr)

    def _resolve_dotted_class_name_str(self, dotted: str) -> str | None:
        """Loc-agnostic variant of _resolve_dotted_class_name: check if a
        dotted-name string resolves to a known module class."""
        parts = dotted.split(".")
        if parts and parts[0] in self._parser._module_class_names:
            return dotted
        return None

    def _raise_unresolved_qualified_error_str(
        self, dotted: str, loc: SourceLocation | None,
    ) -> None:
        """Loc-based variant of _raise_unresolved_qualified_error for a
        dotted name like 'mod.attr'. Only fires for 2-level dotted names
        (mirrors the original which dispatches on ast.Attribute with a
        Name base)."""
        parts = dotted.split(".")
        if len(parts) == 2:
            mod, attr = parts
            canonical = self._parser._reverse_module_aliases.get(mod, mod)
            if canonical in _IMPLICIT_MODULES:
                raise ParseError(
                    f"'{dotted}' requires: import {canonical}", loc=loc,
                )

    def _resolve_generic_type_from_ref(
        self, ref: TpyTypeRef, name: str, type_def: BuiltinTypeDef,
        type_param_scope: dict[str, TypeParamKind] | None,
    ) -> TpyType:
        """Resolve a generic reference (name + args) using a BuiltinTypeDef factory."""
        param_kinds = type_def.param_kinds
        expected_count = len(param_kinds)

        if len(ref.args) != expected_count:
            raise ParseError(
                f"{name} requires exactly {expected_count} type parameters",
                loc=ref.loc,
            )

        parsed_args: list[TpyType | int] = []
        for i, (arg, kind) in enumerate(zip(ref.args, param_kinds)):
            if kind == TypeParamKind.TYPE:
                assert not isinstance(arg, int), f"type slot {i} got int literal"
                parsed_args.append(self.resolve(arg, type_param_scope))
            elif kind == TypeParamKind.INT:
                if isinstance(arg, int):
                    parsed_args.append(arg)
                elif (isinstance(arg, TpyTypeRef) and type_param_scope
                      and arg.name in type_param_scope):
                    param_name = arg.name
                    param_kind = type_param_scope[param_name]
                    if param_kind == TypeParamKind.INT:
                        parsed_args.append(
                            TypeParamRef(param_name, kind=TypeParamKind.INT))
                    else:
                        raise ParseError(
                            f"{name} parameter {i + 1} requires an integer, "
                            f"got type parameter '{param_name}'",
                            loc=ref.loc,
                        )
                else:
                    raise ParseError(
                        f"{name} parameter {i + 1} must be an integer literal or int type parameter",
                        loc=ref.loc,
                    )

        assert type_def.type_factory is not None
        try:
            return type_def.type_factory(*parsed_args)
        except ParseError:
            raise
        except Exception as e:
            raise ParseError(f"Failed to construct type {name}: {e}", loc=ref.loc) from e

    def _resolve_record_type_args_from_ref(
        self, ref: TpyTypeRef, name: str,
        type_param_scope: dict[str, TypeParamKind] | None,
    ) -> tuple[TpyType | int, ...]:
        """Resolve record type arguments (type-args for a user record generic)."""
        type_args: list[TpyType | int] = []
        for arg in ref.args:
            if isinstance(arg, int):
                type_args.append(arg)
            elif (isinstance(arg, TpyTypeRef) and type_param_scope
                  and arg.name in type_param_scope):
                kind = type_param_scope[arg.name]
                type_args.append(TypeParamRef(arg.name, kind=kind))
            else:
                type_args.append(self.resolve(arg, type_param_scope))
        return tuple(type_args)
