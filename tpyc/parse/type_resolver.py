"""
TypeResolver -- name-to-TpyType resolution for parser-emitted TypeRefNodes.

Lives under `parse/` because it is a parser-internal concern: it binds
the names the walker emits to concrete `TpyType` values, using live
parser state (registry, imports, local_defs, ...) via a back-reference
to its owning `Parser`.  Parse-time callers (@builtin_decorator stubs,
nested defs, type-param bounds, macro fragments) invoke `resolve()`
directly.  The module-level walker `parse.resolve_refs.resolve_refs`
calls it once per annotation site after the parser finishes.

The 9 attributes and 2 methods read off the back-reference are
documented in the `TypeResolver` class docstring -- that coupling stays
intra-package (parser + resolver evolve together) and does not surface
in sema.
"""
from __future__ import annotations
import ast
from typing import TYPE_CHECKING, Callable

from ..typesys import (
    TpyType, NominalType, AliasRef, PtrType, OwnType, ReadonlyType, AutoReadonlyType,
    AutoOwnType, FinalType, ClassVarType, OptionalType, VoidType, UnionType, TupleType,
    CallableType, make_union, make_fn_type,
    SendType, SyncType, make_send_marker, make_sync_marker, MarkerAssertionError,
    TypeParamRef, TypeParamKind, LiteralType, LiteralTag,
    INT32, VOID, NONE, STR, STRING, STRVIEW, CHAR, BYTES, BYTEARRAY, BYTESVIEW,
    BOOL, FLOAT, FLOAT32, BIGINT, SELF, BASIC_SLICE, SLICE, ANY, AnyType,
    ALL_FIXED_INTS,
    substitute_type_params_structural,
)
from .. import qnames
from ..type_def_registry import (
    TypeDef, get_type_def, find_factory_by_simple_name, find_factory_in_module,
)
from .nodes import (
    ParseError, ResolutionFailure, SourceLocation,
    TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef, ResolverInputNode,
)
from .imports import _IMPLICIT_MODULES

if TYPE_CHECKING:
    from .parser import Parser


# Map of fixed-int type names to their singleton instances.  Used by
# the resolver for bare-name lookup and by sema's field-default inferrer
# (`analyzer._infer_field_type_from_default`).  Parser-side callers that
# only need name membership use a local `_FIXED_INT_NAMES` frozenset
# instead of pulling in the singleton values.
_FIXED_INT_MAP: dict[str, TpyType] = {str(t): t for t in ALL_FIXED_INTS}


def _refloat_markers(
    inner: TpyType, build: Callable[[TpyType], TpyType], loc: SourceLocation,
) -> TpyType:
    """Build a readonly/Own wrapper over `inner`, floating any Send/Sync
    markers back outside it (canonical order: markers outermost), e.g.
    readonly[Send[T]] -> Send[readonly[T]]."""
    has_send = has_sync = False
    base = inner
    while isinstance(base, (SendType, SyncType)):
        has_send = has_send or isinstance(base, SendType)
        has_sync = has_sync or isinstance(base, SyncType)
        base = base.wrapped
    result = build(base)
    try:
        if has_sync:
            result = make_sync_marker(result)
        if has_send:
            result = make_send_marker(result)
    except MarkerAssertionError as e:
        raise ParseError(str(e), loc=loc)
    return result


def _is_ptr_shaped(t: TpyType) -> bool:
    """True when `t` already lowers to `T*` -- a `Ptr[T]` or a readonly-wrapped
    `Ptr[T]`. Wrapping such a type in `Optional[...]` / `| None` is redundant
    because `Ptr[T]` is already nullable. Used by the resolver to warn at the
    user's spelling site, before `OptionalType.__new__` collapses the wrapper.
    """
    if isinstance(t, PtrType):
        return True
    if isinstance(t, ReadonlyType) and isinstance(t.wrapped, PtrType):
        return True
    return False


class TypeResolver:
    """Resolves TypeRefNode -> TpyType using live parser state.

    Holds a back-reference to its owning `Parser` and reads nine
    parser attributes each call so container growth during parse --
    and per-parse re-assignment of mutable containers at the top of
    each `parse()` -- stays visible:

        registry, _imports, _type_param_scope, _module_class_names,
        _module_type_alias_names, _reverse_module_aliases,
        _nested_type_scope, _local_defs, _bare_module_imports

    Plus two parser-owned helpers that FragmentParser can override
    for lenient macro-fragment parsing:

        _resolve_type_name(name)
        _raise_unresolved_import_error(name, node=None, *, loc=None)

    The coupling is intra-package (parser + resolver evolve together);
    no sema code reaches through here.
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

    def canonicalize_import_table(
        self, lookup: 'Callable[[str, str], tuple[str, str] | None]',
    ) -> None:
        """Canonicalize the parser's import table to (defining_module, name).

        Called by the compiler between parse and sema so subsequent
        `resolve()` calls see defining-module tuples for re-exported
        symbols and can mint authoritative `_module_qname` on the first
        pass.
        """
        self._parser._imports.canonicalize_name_index(lookup)

    def refresh_module_aliases(self) -> None:
        """Rebuild the reverse-alias cache from the current module_aliases.

        Called by the compiler after promoting `from pkg import submod` to
        a full submodule import (which appends to ``ast.module_aliases``);
        the parser builds the reverse map once at parse time, so post-parse
        promotions need an explicit refresh to stay visible to qualified-name
        lookup.
        """
        self._parser._reverse_module_aliases = {
            alias: canonical
            for canonical, alias in self._parser._module_aliases.items()
        }

    def index_imported_name(
        self, local_name: str, source_module: str, original_name: str,
    ) -> None:
        """Add a `(source_module, original_name)` entry to the parser's
        name index for `local_name` if not already present.

        Called by `Compiler._expand_star_imports_for_module` after
        compile-time star-import expansion so the parser-level
        `resolve_refs` pass sees star-imported names when resolving
        type annotations.
        """
        self._parser._imports.index_imported_name(
            local_name, source_module, original_name)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse_type_ref(self, type_string: str) -> ResolverInputNode:
        """Parse a type-expression string into a resolver input node, ready
        for `resolve()`. Wraps the parser's annotation walker so callers (e.g.
        the `--explain-send` CLI) don't reach into parser internals."""
        node = ast.parse(type_string, mode="eval").body
        return self._parser._parse_type_ref(node, type_param_scope={})

    def resolve(
        self, ref: ResolverInputNode,
        type_param_scope: dict[str, TypeParamKind] | None = None,
        *, pending_alias: str | None = None,
        is_type_arg: bool = False,
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

        `is_type_arg=True` flips bare `None` resolution from `VoidType`
        (the function-return-shape singleton) to `NoneType` (the value-
        carrying unit type, lowered to `std::monostate`). True at every
        annotation slot except the function-return slot (resolved via
        `resolve_refs._resolve_return_slot`); union members and Optional/
        Final/ClassVar inners also stay False so VoidType-marker semantics
        (Union/Optional collapse, redundant `Any | None`) survive. The
        flag name is historical and worth renaming to `is_value_position`
        if/when this materializes as a THIR-level mark.
        """
        if pending_alias is not None:
            prev = self._pending_alias
            self._pending_alias = pending_alias
            try:
                return self._resolve_ref(ref, type_param_scope, is_type_arg=is_type_arg)
            finally:
                self._pending_alias = prev
        return self._resolve_ref(ref, type_param_scope, is_type_arg=is_type_arg)

    def resolve_lenient(
        self, ref: ResolverInputNode,
        type_param_scope: dict[str, TypeParamKind] | None = None,
        *, is_type_arg: bool = False,
    ) -> TpyType:
        """Resolve like `resolve()` but, on a name-resolution failure for a
        bare `TpyTypeRef`, construct a `NominalType(name, args)` placeholder
        instead of raising. Used by FragmentParser so macro fragments can
        reference symbols not visible to the fragment-level resolver --
        sema re-resolves them in the final context.

        Failure identification is structural via the `ResolutionFailure`
        subclass (raised only at the three bare/dotted/generic
        unknown-name sites). Structural errors (Own-in-union, Callable
        arity, etc.) surface as plain `ParseError` and still propagate.
        Non-`TpyTypeRef` inputs are not leniency targets -- they delegate
        to `resolve()` unchanged.
        """
        try:
            return self.resolve(ref, type_param_scope, is_type_arg=is_type_arg)
        except ResolutionFailure:
            if not isinstance(ref, TpyTypeRef):
                raise
            resolved_args: tuple = tuple(
                a if isinstance(a, int)
                else self.resolve_lenient(a, type_param_scope, is_type_arg=True)
                for a in ref.args
            )
            return NominalType(ref.name, resolved_args)

    def _resolve_ref(
        self, ref: ResolverInputNode,
        type_param_scope: dict[str, TypeParamKind] | None,
        *, is_type_arg: bool = False,
    ) -> TpyType:
        """Internal resolver.  Recursive calls to `self.resolve(...)` without
        `pending_alias` re-enter the public method, which skips the
        save/restore and forwards straight here.

        `is_type_arg` see `resolve()` docstring -- forwarded into the
        bare-`None` branch and into `_resolve_primitive_type`.
        """
        parser = self._parser
        if type_param_scope is None:
            type_param_scope = parser._type_param_scope

        # Union
        if isinstance(ref, TpyUnionRef):
            # Union members keep VoidType marker semantics (collapse to
            # Optional, redundant-Any check, Ptr-already-nullable warning).
            # If the outer context is a type-arg slot, the union as a whole
            # is still the type at that slot, not each member individually.
            parsed = [self.resolve(m, type_param_scope) for m in ref.members]
            non_none = [t for t in parsed if not isinstance(t, VoidType)]
            # Any is the universal supertype -- combining it with None or
            # any other type is redundant.
            for t in parsed:
                if isinstance(t, AnyType):
                    has_none = any(isinstance(p, VoidType) for p in parsed)
                    if has_none:
                        raise ParseError(
                            "Any | None is redundant -- Any already accepts None",
                            loc=ref.loc,
                        )
                    if len(parsed) > 1:
                        raise ParseError(
                            "Any | T is redundant -- Any is the universal supertype",
                            loc=ref.loc,
                        )
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
            # Redundant `Ptr[T] | None` (upstream #17). `Ptr[T]` is already
            # nullable and the collapse rule erases the wrapper; warn at the
            # user's spelling site so the redundant form doesn't drift back in.
            if non_none_count == 1 and any(isinstance(t, VoidType) for t in parsed):
                non_none = next(t for t in parsed if not isinstance(t, VoidType))
                if _is_ptr_shaped(non_none):
                    parser._warn_at_loc(
                        f"`{non_none} | None` is redundant -- `{non_none}` is "
                        f"already nullable; drop the `| None`",
                        ref.loc,
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
                self.resolve(p, type_param_scope, is_type_arg=True) for p in ref.params
            )
            return_type = self.resolve(ref.return_type, type_param_scope)
            if ref.kind == "Fn":
                return make_fn_type(param_types, return_type)
            return CallableType(param_types, return_type)

        # Literal
        if isinstance(ref, TpyLiteralRef):
            tag = ref.values[0].tag
            base_type = {
                LiteralTag.STR: STR, LiteralTag.INT: INT32, LiteralTag.BOOL: BOOL,
            }[tag]
            return LiteralType(base_type, ref.values)

        # TpyTypeRef
        assert isinstance(ref, TpyTypeRef), f"Unknown ref kind: {type(ref).__name__}"
        name = ref.name

        # "None" (void) -- emitted by walker for ast.Constant(None)
        if name == "None" and not ref.args:
            return NONE if is_type_arg else VOID

        # Structural wrappers (canonical `mod:Name` names set by the walker
        # only when the source name actually resolved to the expected module).
        # The `:` separator avoids any collision with raw user-source names
        # (including dotted forms like "typing.Optional" written without
        # `import typing`), so raw-name TpyTypeRefs fall through to the
        # generic path where the resolver's unresolved-name error fires.
        if ref.args and name in (
            "tpy:Ptr", "tpy:Own", "tpy:readonly", "tpy:auto_readonly",
            "tpy:auto_own", "tpy:Send", "tpy:Sync",
            "typing:Optional", "typing:Final", "typing:ClassVar",
        ):
            inner_arg = ref.args[0]
            assert not isinstance(inner_arg, int), \
                f"structural wrapper {name} cannot take int arg"
            # Own/Ptr/readonly/auto_*/Send/Sync are value-bearing slots
            # (Own[None] etc. must produce NoneType). Optional/Final/ClassVar
            # keep the outer context: Optional preserves the union-marker
            # semantics, Final/ClassVar are annotation modifiers at whatever
            # depth they appear.
            inner_is_type_arg = name in (
                "tpy:Ptr", "tpy:Own", "tpy:readonly",
                "tpy:auto_readonly", "tpy:auto_own", "tpy:Send", "tpy:Sync",
            ) or is_type_arg
            inner = self.resolve(
                inner_arg, type_param_scope, is_type_arg=inner_is_type_arg,
            )
            if name in ("tpy:Send", "tpy:Sync"):
                try:
                    if name == "tpy:Send":
                        return make_send_marker(inner)
                    return make_sync_marker(inner)
                except MarkerAssertionError as e:
                    raise ParseError(str(e), loc=ref.loc)
            if name == "tpy:Ptr":
                if isinstance(inner, ReadonlyType):
                    return PtrType(inner.wrapped, is_readonly=True)
                return PtrType(inner)
            if name == "tpy:Own":
                if isinstance(inner, AnyType):
                    raise ParseError(
                        "Own[Any] is redundant -- Any is already owning",
                        loc=ref.loc,
                    )
                return _refloat_markers(inner, OwnType, ref.loc)
            if name == "tpy:readonly":
                return _refloat_markers(inner, ReadonlyType, ref.loc)
            if name == "tpy:auto_readonly":
                return AutoReadonlyType(inner)
            if name == "tpy:auto_own":
                return AutoOwnType(inner)
            if name == "typing:Optional":
                if isinstance(inner, AnyType):
                    raise ParseError(
                        "Optional[Any] is redundant -- Any already accepts None",
                        loc=ref.loc,
                    )
                if _is_ptr_shaped(inner):
                    parser._warn_at_loc(
                        f"`Optional[{inner}]` is redundant -- `{inner}` is "
                        f"already nullable; use `{inner}` directly",
                        ref.loc,
                    )
                return OptionalType(inner)
            if name == "typing:Final":
                return FinalType(inner)
            if name == "typing:ClassVar":
                return ClassVarType(inner)

        # tuple
        if name == "builtins:tuple" and ref.args:
            elements = tuple(
                self.resolve(a, type_param_scope, is_type_arg=True)
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
                        primitive = self._resolve_primitive_type(
                            *resolved_q, loc=ref.loc, is_type_arg=is_type_arg)
                        if primitive is not None:
                            return primitive
                        registered = self._resolve_registered_type(
                            resolved_q[1], loc=ref.loc, resolved=True)
                        if registered is not None:
                            return self._upgrade_from_type_def(
                                registered, resolved_q, loc=ref.loc)
                        # Submodule-import path (`from pkg import submod`):
                        # `attr` is not in the importer's local registry but
                        # is exported by the source module. Consult the source
                        # module directly so the type carries its cross-module
                        # qname.
                        cross = self._resolve_qualified_cross_module(
                            *resolved_q, loc=ref.loc)
                        if cross is not None:
                            return cross
                # Nested dotted class: Outer.Inner, Outer.Mid.Inner, ...
                dotted = self._resolve_dotted_class_name_str(name)
                if dotted is not None:
                    rinfo = parser.registry.get_record(dotted)
                    if rinfo is not None:
                        qname = self._user_record_qname(dotted, None)
                        return NominalType(dotted, _module_qname=qname)
                    enum_type = parser.registry.get_enum(dotted)
                    if enum_type is not None:
                        return enum_type
                # Helpful error for unimported implicit module
                self._raise_unresolved_qualified_error_str(name, ref.loc)
                raise ResolutionFailure(
                    f"Unsupported qualified type: {name}", loc=ref.loc,
                )

            # Bare name resolution
            resolved = parser._resolve_type_name(name)
            if resolved:
                primitive = self._resolve_primitive_type(
                    *resolved, loc=ref.loc, is_type_arg=is_type_arg)
                if primitive is not None:
                    return primitive
            resolved_name = resolved[1] if resolved else name
            registered = self._resolve_registered_type(
                resolved_name, loc=ref.loc, resolved=bool(resolved))
            if registered is not None:
                # Mint _module_qname for cross-module user refs.  The
                # builtin path handles type-factory-backed names (list,
                # Array, ...); the canonical-import path covers user
                # records / protocols / enums imported from other
                # modules -- `canonicalize_import_table` has already
                # rewritten the tuple to point at the defining module,
                # so `{module}.{original}` is the authoritative qname.
                # For canonical protocols we also upgrade is_protocol /
                # is_dynamic_protocol from `TypeDef.protocol` (the dep
                # module's sema has already attached it).
                if (resolved and isinstance(registered, NominalType)
                        and not registered._module_qname):
                    module, original = resolved
                    candidate_qname = f"{module}.{original}"
                    candidate_td = get_type_def(candidate_qname)
                    is_builtin = (
                        parser.registry.get_builtin_type_key(registered.name) is not None
                        or (candidate_td is not None and candidate_td.type_factory is not None)
                    )
                    if is_builtin or parser._imports.is_canonical(name):
                        is_protocol = registered.is_protocol
                        is_dynamic_protocol = registered.is_dynamic_protocol
                        if candidate_td is not None and candidate_td.protocol is not None:
                            proto = candidate_td.protocol
                            if proto.type_params:
                                # Match sema block-3's original error class so
                                # diag.txt formats as `file:line: error: ...`
                                # (ParseError would go through the `Parse
                                # error: ...` CLI path instead).
                                from ..diagnostics import SemanticError
                                raise SemanticError(
                                    f"Generic protocol '{registered.name}' requires type arguments: "
                                    f"{registered.name}[{', '.join(proto.type_params)}]",
                                    loc=ref.loc,
                                )
                            is_protocol = True
                            is_dynamic_protocol = proto.is_dynamic
                        registered = NominalType(
                            registered.name, registered.type_args,
                            is_protocol, candidate_qname, is_dynamic_protocol,
                        )
                return registered
            raise ResolutionFailure(f"Unknown type: {name}", loc=ref.loc)

        # Generic form (name + args) -- bare or dotted

        # Self-reference inside the body of the alias currently being
        # resolved (`type Tree[T] = T | list[Tree[T]]` -- the inner
        # `Tree[T]`).  The alias isn't yet registered, so the normal
        # lookup path would fail.  Emit an `AliasRef` placeholder so
        # `_contains_self_reference` detects it and tags the alias
        # recursive.  Resolve and carry the `[...]` args (restoring the
        # "Unknown type" diagnostic for malformed args like
        # `list[Tree[Undefined]]`) and arity-check against the alias's
        # declared params; the same-args identity rule is enforced later
        # by the recursive-union validator.
        if self._pending_alias is not None and name == self._pending_alias:
            expected = len(type_param_scope) if type_param_scope else 0
            actual = len(ref.args)
            if actual != expected:
                raise ResolutionFailure(
                    f"Recursive alias '{name}' self-reference takes "
                    f"{expected} type argument{'s' if expected != 1 else ''}, "
                    f"got {actual}",
                    loc=ref.loc,
                )
            resolved_args = tuple(
                a if isinstance(a, int)
                else self.resolve(a, type_param_scope, is_type_arg=True)
                for a in ref.args
            )
            return AliasRef(
                name, module=parser._public_module(), args=resolved_args)

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

        # Reject naming a user_spellable=False builtin (e.g. the *args view
        # tpy.varargs) in an annotation. Its own @builtin_type stub is exempt:
        # there the type's local class is what resolves to the builtin qname, so
        # `name in _module_class_names` identifies the defining stub (a user's
        # own same-named class resolves to itself, never to the builtin qname).
        if resolved is not None:
            _resolved_td = get_type_def(f"{resolved[0]}.{resolved[1]}")
            if (_resolved_td is not None and not _resolved_td.user_spellable
                    and resolved[1] not in parser._module_class_names):
                raise ResolutionFailure(
                    f"'{resolved[1]}' is a compiler-internal type and cannot be "
                    f"used in annotations", loc=ref.loc)

        # Module-defined generic types (list, Array, Span, ...)
        if resolved_container and (td := find_factory_by_simple_name(resolved_container)):
            if not resolved and td.qname.startswith("tpy."):
                parser._raise_unresolved_import_error(name, loc=ref.loc)
            return self._resolve_generic_type_from_ref(
                ref, resolved_container, td, type_param_scope)

        # Imported generic from a module
        if resolved:
            source_module, original_name = resolved
            if td := find_factory_in_module(original_name, source_module):
                return self._resolve_generic_type_from_ref(
                    ref, resolved_container, td, type_param_scope)
        elif "." not in name and name:
            if import_source := parser._imports.get_import_source(name):
                source_module, original_name = import_source
                if td := find_factory_in_module(original_name, source_module):
                    return self._resolve_generic_type_from_ref(
                        ref, resolved_container, td, type_param_scope)

        # Qualified name with missing module import -- only for dotted subscripts
        # where qualified resolution failed.
        if "." in name and resolved is None:
            self._raise_unresolved_qualified_error_str(name, ref.loc)

        # User-defined generic protocols
        if user_protocol := parser.registry.scan_by_short_name(resolved_container):
            if user_protocol.type_params:
                expected = len(user_protocol.type_params)
                if len(ref.args) != expected:
                    raise ParseError(
                        f"{resolved_container} requires exactly {expected} type parameters",
                        loc=ref.loc,
                    )
                type_args = tuple(
                    a if isinstance(a, int)
                    else self.resolve(a, type_param_scope, is_type_arg=True)
                    for a in ref.args
                )
                qname = (f"{user_protocol.module}.{resolved_container}"
                         if user_protocol.module else None)
                return NominalType(
                    resolved_container, type_args, is_protocol=True,
                    _module_qname=qname,
                    is_dynamic_protocol=user_protocol.is_dynamic,
                )

        # Generic type alias use site (`Pair[int]`).  Phase 1 of generic
        # recursive aliases (see docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md):
        # substitute non-recursive generic aliases at use sites; recursive
        # ones stay rejected for Phase 2.  Handle local, short-name-imported,
        # and dotted-qualified forms via one helper.
        alias_info = self._lookup_generic_alias_info(name, resolved_container, resolved)
        if alias_info is not None and alias_info.type_params:
            return self._resolve_generic_alias_use(
                ref, resolved_container, alias_info, type_param_scope,
            )

        # Unresolved bare (non-dotted) name -- helpful import hint
        if not resolved and "." not in name and name:
            parser._raise_unresolved_import_error(name, loc=ref.loc)
        if (resolved or parser.registry.get_record(resolved_container) is not None
                or resolved_container in parser._module_class_names):
            type_args = self._resolve_record_type_args_from_ref(
                ref, resolved_container, type_param_scope)
            canonical = "." not in name and parser._imports.is_canonical(name)
            qname = self._user_record_qname(
                resolved_container, resolved, canonical=canonical)
            # Upgrade is_protocol / is_dynamic_protocol for cross-module
            # canonical protocol refs via TypeDef.protocol (replaces the
            # old sema block-3 substitution).
            is_protocol = False
            is_dynamic_protocol = False
            if canonical and qname:
                td = get_type_def(qname)
                if td is not None and td.protocol is not None:
                    is_protocol = True
                    is_dynamic_protocol = td.protocol.is_dynamic
            return NominalType(
                resolved_container, type_args, is_protocol,
                qname, is_dynamic_protocol,
            )

        # Nested generic records (Outer.Inner[T])
        if "." in name:
            dotted = self._resolve_dotted_class_name_str(name)
            if dotted is not None and parser.registry.get_record(dotted) is not None:
                type_args = self._resolve_record_type_args_from_ref(
                    ref, dotted, type_param_scope)
                qname = self._user_record_qname(dotted, None)
                return NominalType(dotted, type_args, _module_qname=qname)

        raise ResolutionFailure(f"Unknown generic type: {name}", loc=ref.loc)

    def _lookup_generic_alias_info(
        self, name: str, resolved_container: str,
        resolved: 'tuple[str, str] | None',
    ) -> 'TypeAliasInfo | None':
        """Find a generic alias's TypeAliasInfo across local + imported forms.

        Handles:
        - Local: `type Pair[T] = ...` in the current module.
        - Imported short name: `from lib_a import Pair` then `Pair[int]`.
        - Dotted qualified: `lib_a.Pair[int]` (resolved is the
          (source_module, original_name) tuple).

        Returns None if the name isn't a registered alias anywhere; the
        caller then continues with the record / unknown-name fallback.
        """
        parser = self._parser
        # Local registry first -- covers same-module aliases and aliases
        # whose import was already materialised into the parser registry.
        local = parser.registry.get_type_alias_info(name)
        if local is not None:
            return local
        # Cross-module: consult the source module's ModuleInfo for the
        # original-named alias.  Works for both short-name imports
        # (`resolved == (source_module, original_name)`) and dotted
        # qualification.
        if resolved is not None:
            source_module, original_name = resolved
            mod_info = parser.registry.modules.get(source_module)
            if mod_info is not None and mod_info.type_aliases:
                return mod_info.type_aliases.get(original_name)
        return None

    def _resolve_generic_alias_use(
        self, ref: 'TpyTypeRef', name: str, alias_info: 'TypeAliasInfo',
        type_param_scope: 'dict[str, TypeParamKind] | None',
    ) -> TpyType:
        """Expand a use of a generic type alias at the use site.

        Two outcomes:
        - Non-recursive aliases (`type Pair[T] = tuple[T, T]`): build
          `{T_i: arg_i}` and apply structural substitution to the body.
          The alias name disappears from the type tree downstream.
        - Recursive aliases (`type Tree[T] = T | list[Tree[T]]`): emit an
          `AliasRef(name, args)` placeholder (parse-resolve runs against a
          separate registry and cannot mint the semantic
          `RecursiveAliasInstanceType`). Sema's finalize pass converts it
          using the sema-registry alias_info. See
          docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md.
        """
        expected = len(alias_info.type_params)
        actual = len(ref.args)
        if actual != expected:
            raise ResolutionFailure(
                f"Type alias '{name}' takes {expected} type "
                f"argument{'s' if expected != 1 else ''}, got {actual}",
                loc=ref.loc,
            )
        # Resolve each type arg.  int args (Array's N slot) shouldn't
        # appear here -- v1 aliases reject int-kind type params at parse
        # time -- but pass them through unchanged for forward compatibility.
        resolved_args: list[TpyType | int] = []
        for arg in ref.args:
            if isinstance(arg, int):
                resolved_args.append(arg)
            else:
                resolved_args.append(
                    self.resolve(arg, type_param_scope, is_type_arg=True)
                )
        if alias_info.is_recursive:
            return AliasRef(
                name, module=self._parser._public_module(),
                args=tuple(resolved_args),
            )
        subst: dict[str, TpyType] = {}
        for tp_name, arg in zip(alias_info.type_params, resolved_args):
            if isinstance(arg, TpyType):
                subst[tp_name] = arg
        return substitute_type_params_structural(alias_info.body, subst)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _upgrade_from_type_def(
        self, registered: TpyType, resolved: tuple[str, str],
        *, loc: SourceLocation | None = None,
    ) -> TpyType:
        """Mint `_module_qname` / upgrade protocol flags on a qualified
        (`typing.Sized`) or canonical bare-name resolution using the
        resolved `(module, name)` tuple's `TypeDef`.

        Only NominalTypes without a qname are upgraded.  For TypeDefs
        carrying a ProtocolInfo payload, `is_protocol` /
        `is_dynamic_protocol` are also upgraded, and a bare generic
        protocol raises `SemanticError` (with `loc` attached so the
        diagnostic formats as `file:line: error: ...`).  Enum-registered
        NominalTypes come back from `_resolve_registered_type` already
        qname-bearing and fall through unchanged.
        """
        if not isinstance(registered, NominalType) or registered._module_qname:
            return registered
        module, original = resolved
        candidate_qname = f"{module}.{original}"
        td = get_type_def(candidate_qname)
        if td is None:
            return registered
        if td.protocol is None and td.record is None and td.enum is None \
                and td.type_factory is None:
            return registered
        is_protocol = registered.is_protocol
        is_dynamic_protocol = registered.is_dynamic_protocol
        if td.protocol is not None:
            if td.protocol.type_params and not registered.type_args:
                from ..diagnostics import SemanticError
                raise SemanticError(
                    f"Generic protocol '{registered.name}' requires type arguments: "
                    f"{registered.name}[{', '.join(td.protocol.type_params)}]",
                    loc=loc,
                )
            is_protocol = True
            is_dynamic_protocol = td.protocol.is_dynamic
        return NominalType(
            registered.name, registered.type_args,
            is_protocol, candidate_qname, is_dynamic_protocol,
        )

    def _user_record_qname(
        self, container: str, resolved: tuple[str, str] | None,
        *, canonical: bool = False,
    ) -> str | None:
        """Mint `_module_qname` for a user-record reference.

        Cross-module refs use the canonicalized import tuple when
        `canonical=True`; same-module refs pull from
        `parser.registry.get_record().module`.  Returns None when
        neither source supplies a module (bare placeholders that later
        passes will resolve) or the tuple is not canonicalized.
        """
        rinfo = self._parser.registry.get_record(container)
        # @builtin_type-with-body records (no factory entry, e.g.
        # asyncio._executor.Task with @builtin_type("tpy.Task")) carry
        # their canonical qname in `builtin_type_key`. It wins over both
        # the canonical-import tuple and the defining-module form so
        # the resulting NominalType's _module_qname matches the static
        # TypeDef key regardless of how the type was imported.
        # `RecordInfo.qualified_name()` short-circuits on builtin_type_key.
        if rinfo is not None and rinfo.builtin_type_key:
            return rinfo.qualified_name()
        if resolved is not None and canonical:
            return f"{resolved[0]}.{resolved[1]}"
        if rinfo is not None and rinfo.module:
            return f"{rinfo.module}.{container}"
        return None

    def _resolve_primitive_type(
        self, module: str, original: str,
        node: ast.expr | None = None, *, loc: SourceLocation | None = None,
        is_type_arg: bool = False,
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
            elif original == "None": return NONE if is_type_arg else VOID
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
            elif original == "Any":
                return ANY
            elif original == "Protocol":
                raise ParseError("'Protocol' cannot be used as a type annotation", node, loc=loc)
        return None

    def _resolve_registered_type(
        self, name: str, node: ast.expr | None = None,
        *, resolved: bool = False, loc: SourceLocation | None = None,
    ) -> TpyType | None:
        """Look up a name in the type registry (protocols, aliases, records).

        Raises SemanticError for generic protocols used without type arguments
        (matches the cross-module canonical path in `_resolve_ref` so diag
        output is uniform), or ParseError for completely unknown names.
        Either `node` or `loc` may be passed for error reporting.
        """
        parser = self._parser
        # Self-reference in a recursive type alias (e.g. list[JsonValue] inside
        # the definition of JsonValue). Return an AliasRef placeholder that
        # survives inside container types and is detected by an
        # `isinstance(typ, AliasRef)` check rather than a name-set lookup.
        if self._pending_alias is not None and name == self._pending_alias:
            return AliasRef(name, module=parser._public_module())
        # Resolve short nested type names: Kind -> Message.Kind
        if name in parser._nested_type_scope:
            dotted = parser._nested_type_scope[name]
            if (enum_type := parser.registry.get_enum(dotted)) is not None:
                return enum_type
            if parser.registry.get_record(dotted) is not None:
                qname = self._user_record_qname(dotted, None)
                return NominalType(dotted, _module_qname=qname)
        if (user_protocol := parser.registry.scan_by_short_name(name)) is not None:
            if user_protocol.type_params:
                from ..diagnostics import SemanticError
                raise SemanticError(
                    f"Generic protocol '{name}' requires type arguments: "
                    f"{name}[{', '.join(user_protocol.type_params)}]",
                    loc=loc,
                )
            qname = f"{user_protocol.module}.{name}" if user_protocol.module else None
            return NominalType(
                name, is_protocol=True, _module_qname=qname,
                is_dynamic_protocol=user_protocol.is_dynamic,
            )
        elif (enum_type := parser.registry.get_enum(name)) is not None:
            return enum_type
        elif (alias_info := parser.registry.get_type_alias_info(name)) is not None:
            if alias_info.type_params:
                # Bare reference to a generic alias -- mirror the protocol
                # arm above: require type arguments at the use site rather
                # than letting the body's TypeParamRefs leak into the
                # annotation.  See docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md.
                from ..diagnostics import SemanticError
                raise SemanticError(
                    f"Generic type alias '{name}' requires type arguments: "
                    f"{name}[{', '.join(alias_info.type_params)}]",
                    loc=loc,
                )
            return alias_info.body
        elif not resolved:
            parser._raise_unresolved_import_error(name, node, loc=loc)
        if (resolved or parser.registry.is_known_type(name)
                or name in parser._module_class_names
                or name in parser._module_type_alias_names):
            # Mint qname for same-module user records from parser.registry.
            # Cross-module refs get their qname minted in `_resolve_ref` from
            # the canonicalized import tuple. Builtin-type stubs carry their
            # qname in `builtin_type_key`; leave them bare so the downstream
            # builtin-path minting (is_builtin gate) stays authoritative.
            rinfo = parser.registry.get_record(name)
            if rinfo is not None and rinfo.module and not rinfo.builtin_type_key:
                return NominalType(name, _module_qname=f"{rinfo.module}.{name}")
            return NominalType(name)
        return None

    def _resolve_qualified_cross_module(
        self, source_module: str, attr: str,
        *, loc: SourceLocation | None = None,
    ) -> TpyType | None:
        """Resolve `attr` against the named source module's exports.

        Used when ``submod.X`` qualifies a type from a submodule that the
        importing module has bound (via `from pkg import submod`) but
        whose individual records / enums / type aliases are not in the
        importer's local registry. Returns a NominalType / enum_type /
        alias type with the correct cross-module qname, or None.
        """
        parser = self._parser
        mod_info = parser.registry.get_module(source_module)
        if mod_info is None:
            return None
        if mod_info.records and attr in mod_info.records:
            rinfo = mod_info.records[attr]
            qname = f"{rinfo.module}.{attr}" if rinfo.module else f"{source_module}.{attr}"
            return NominalType(attr, _module_qname=qname)
        if mod_info.enums and attr in mod_info.enums:
            return mod_info.enums[attr]
        if mod_info.type_aliases and attr in mod_info.type_aliases:
            alias_info = mod_info.type_aliases[attr]
            if alias_info.type_params:
                # Cross-module bare reference to a generic alias --
                # require type arguments rather than leaking
                # TypeParamRefs into the annotation.
                from ..diagnostics import SemanticError
                raise SemanticError(
                    f"Generic type alias '{attr}' requires type arguments: "
                    f"{attr}[{', '.join(alias_info.type_params)}]",
                    loc=ref.loc,
                )
            return alias_info.body
        if mod_info.protocols and attr in mod_info.protocols:
            proto = mod_info.protocols[attr]
            if proto.type_params:
                from ..diagnostics import SemanticError
                raise SemanticError(
                    f"Generic protocol '{attr}' requires type arguments: "
                    f"{attr}[{', '.join(proto.type_params)}]",
                    loc=loc,
                )
            qname = f"{source_module}.{attr}"
            return NominalType(
                attr, is_protocol=True, _module_qname=qname,
                is_dynamic_protocol=proto.is_dynamic,
            )
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
        self, ref: TpyTypeRef, name: str, type_def: TypeDef,
        type_param_scope: dict[str, TypeParamKind] | None,
    ) -> TpyType:
        """Resolve a generic reference (name + args) using a TypeDef factory."""
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
                parsed_args.append(
                    self.resolve(arg, type_param_scope, is_type_arg=True))
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
                type_args.append(
                    self.resolve(arg, type_param_scope, is_type_arg=True))
        return tuple(type_args)
