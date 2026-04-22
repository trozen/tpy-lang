"""Parser-output ref resolution walker.

Resolves every `TypeRefNode` the parser emitted at annotation sites to a
concrete `TpyType`, in place on the `TpyModule`.  Runs once per module
(between import canonicalization and sema) and leaves the AST with
TpyType invariants (`map_inner_types`, `isinstance` against structural
classes, etc.) everywhere.

Covers every module-level annotation site: type aliases, top-level
`TpyVarDecl`, function / overload-group signatures, type-parameter
bounds, record fields / bases / methods / self, protocol fields and
method signatures, and expression-level type uses inside bodies.
Nested defs and macro-fragment functions are resolved eagerly at parse
time (`parser._finalize_function_refs` / `_parse_method`) and are not
re-walked here.

The walker uses `module.resolver` (a `TypeResolver` constructed by the
parser) as the only authority for name binding.  Field-default
inference for class-body `name = expr` assignments reuses the resolver
for record lookups, so no sema context is needed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NominalType, UnionType, VOID, TypeParamKind,
    BIGINT, FLOAT, STR,
)
from ..typesys import _contains_self_reference, validate_recursive_union_paths
from .nodes import (
    TpyModule, TpyVarDecl, TpyCall, TpyMethodCall, TpyNestedDef,
    TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef,
    TpyInferFromDefaultRef, ParseError, ResolutionFailure,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyName,
)
from .type_resolver import _FIXED_INT_MAP
from ..diagnostics import SemanticError

if TYPE_CHECKING:
    from .nodes import TpyExpr
    from .type_resolver import TypeResolver

_REF_TYPES = (TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef)


def promote_bare_nominals(typ: TpyType, registry) -> TpyType:
    """Return `typ` with every bare `NominalType` whose short name
    resolves in `registry` replaced by its qname-bearing counterpart.
    Recurses into structural wrappers.  Closes the bridge from macro-
    emitted `types.named("Foo")` placeholders to the strict-equality
    NominalType identity sema/codegen downstream relies on.
    """
    if isinstance(typ, NominalType) and typ._module_qname is None:
        info = registry.get_record(typ.name)
        if info is not None:
            qname = info.qualified_name()
            if qname:
                typ = NominalType(
                    typ.name, typ.type_args, typ.is_protocol,
                    qname, typ.is_dynamic_protocol,
                )
    return typ.map_inner_types(lambda t: promote_bare_nominals(t, registry))


def _infer_field_type_from_default(
    expr: 'TpyExpr | None', resolver: 'TypeResolver',
) -> TpyType | None:
    """Infer a field type from its default-value expression.

    Handles:
      - int / float / str literals -> BIGINT / FLOAT / STR.
      - Fixed-int constructor calls (`Int32(...)`, ...) -> the matching
        singleton.
      - `int()` / `float()` -> BIGINT / FLOAT.
      - Calls to a resolvable record / protocol / enum name (same-module
        or imported via the canonicalized import table) -> qname-
        bearing NominalType.

    Returns None if the expression does not match any recognised form;
    the caller raises "Cannot infer type for field 'X'".
    """
    if expr is None:
        return None
    if isinstance(expr, TpyIntLiteral):
        return BIGINT
    if isinstance(expr, TpyFloatLiteral):
        return FLOAT
    if isinstance(expr, TpyStrLiteral):
        return STR
    if not (isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)):
        return None
    type_name = expr.func_name
    if (fixed_int := _FIXED_INT_MAP.get(type_name)) is not None:
        return fixed_int
    if type_name == "int":
        return BIGINT
    if type_name == "float":
        return FLOAT
    # Route through the resolver: it consults both the parser's
    # registry (same-module records) and the canonicalized import
    # table (cross-module records), minting `_module_qname` uniformly.
    try:
        resolved = resolver.resolve(TpyTypeRef(type_name))
    except (ParseError, ResolutionFailure):
        return None
    if isinstance(resolved, NominalType):
        return resolved
    return None


def _func_scope(func):
    """Build a `{name: TypeParamKind}` scope dict for a TpyFunction or
    `None` if it has no type params."""
    if not func.type_params:
        return None
    kinds = func.type_param_kinds
    return {
        name: (kinds[i] if i < len(kinds) else TypeParamKind.TYPE)
        for i, name in enumerate(func.type_params)
    }


def _record_scope(record):
    if not record.type_params:
        return None
    kinds = record.type_param_kinds
    return {
        name: (kinds[i] if i < len(kinds) else TypeParamKind.TYPE)
        for i, name in enumerate(record.type_params)
    }


def _merged_method_scope(record_scope, method):
    """Record type params + method type params, method-level wins on collision."""
    if not method.type_params:
        return record_scope
    merged = dict(record_scope) if record_scope else {}
    method_kinds = method.type_param_kinds
    for i, name in enumerate(method.type_params):
        kind = method_kinds[i] if i < len(method_kinds) else TypeParamKind.TYPE
        merged[name] = kind
    return merged


def _protocol_scope(protocol):
    """Protocols only carry TYPE-kind type params (parser constraint)."""
    if not protocol.type_params:
        return None
    return {name: TypeParamKind.TYPE for name in protocol.type_params}


def resolve_method_body_refs(method, record, resolver, *, promote_registry=None) -> None:
    """Resolve all TypeRefNodes in a record method's body (TpyVarDecl
    annotations, `TpyCall.call_type`, `TpyCall.type_args`,
    `TpyMethodCall.type_args`).

    Called by sema's `register_record` after `_apply_class_macros` so
    macro-added methods get the same treatment source-defined methods
    received during the module-level `resolve_refs` walk.  Idempotent:
    already-resolved sites fall through the walker's isinstance checks
    unchanged.

    `promote_registry` (optional) is consulted to promote bare
    `NominalType` placeholders left behind by `types.named(...)` macro
    calls.  Callers from sema pass `ctx.registry` so imported records
    are visible.
    """
    scope = _merged_method_scope(_record_scope(record), method)
    _walk_body(method.body, scope, resolver, promote_registry=promote_registry)


def _walk_body(stmts, call_scope, resolver, *, promote_registry=None):
    """Body walker extracted so both `resolve_refs` and
    `resolve_method_body_refs` share the same traversal.

    `promote_registry`, when provided, is used to upgrade bare
    `NominalType("Foo")` placeholders (left behind by macro-emitted
    `types.named("Foo")`) to their qname-bearing counterparts so
    strict `NominalType` equality holds downstream.
    """
    for stmt in stmts:
        if isinstance(stmt, TpyVarDecl) and stmt.type is not None:
            if isinstance(stmt.type, _REF_TYPES):
                stmt.type = resolver.resolve(stmt.type, call_scope)
            elif promote_registry is not None:
                # Only promote bare NominalTypes when sema explicitly
                # asks (post-macro-deps pass).  Parse-time walks use
                # `promote_registry=None` so we don't traverse every
                # already-resolved type on every module-level walk.
                stmt.type = promote_bare_nominals(stmt.type, promote_registry)
        for expr in stmt.exprs():
            _walk_expr_calls(expr, call_scope, resolver)
        for body in stmt.sub_bodies():
            _walk_body(body, call_scope, resolver, promote_registry=promote_registry)
        # TpyNestedDef inherits `sub_bodies() -> []` from TpyStmt, so the
        # loop above is a no-op for nested defs -- the body must be
        # walked here explicitly, under the nested function's own scope.
        if isinstance(stmt, TpyNestedDef):
            # Nested def signatures are resolved at parse time (before
            # the compiler canonicalizes cross-module imports), so any
            # imported-record param types land as bare NominalTypes.
            # Promote them now that `promote_registry` has the full
            # cross-module picture.
            if promote_registry is not None:
                stmt.func.params = [
                    (n, promote_bare_nominals(t, promote_registry))
                    for n, t in stmt.func.params
                ]
                if stmt.func.return_type is not None:
                    stmt.func.return_type = promote_bare_nominals(
                        stmt.func.return_type, promote_registry)
                if stmt.func.vararg_type is not None:
                    stmt.func.vararg_type = promote_bare_nominals(
                        stmt.func.vararg_type, promote_registry)
                if stmt.func.kwarg_type is not None:
                    stmt.func.kwarg_type = promote_bare_nominals(
                        stmt.func.kwarg_type, promote_registry)
            nested_scope = _func_scope(stmt.func) or call_scope
            _walk_body(stmt.func.body, nested_scope, resolver, promote_registry=promote_registry)


def _walk_expr_calls(expr, call_scope, resolver):
    if isinstance(expr, TpyCall):
        if isinstance(expr.call_type, _REF_TYPES):
            # Parser catches structural errors at parse time; here we
            # catch name-resolution errors silently to match pre-flip
            # behaviour (sema falls back to type_args / subscript_callee
            # when the name isn't a type).
            try:
                expr.call_type = resolver.resolve(expr.call_type, call_scope)
            except ParseError:
                expr.call_type = None
        _walk_type_args(expr, call_scope, resolver)
    elif isinstance(expr, TpyMethodCall):
        _walk_type_args(expr, call_scope, resolver)
    for child in expr.children():
        _walk_expr_calls(child, call_scope, resolver)


def _walk_type_args(expr, call_scope, resolver):
    """Resolve TypeRefNode elements in expr.type_args.  On any element
    failure, drop the whole tuple and propagate the error message
    through `type_args_parse_error` so sema's generic-call validator
    reports it."""
    if not expr.type_args:
        return
    new_args = []
    for ta in expr.type_args:
        if isinstance(ta, _REF_TYPES):
            try:
                new_args.append(resolver.resolve(ta, call_scope))
            except ParseError as e:
                expr.type_args = ()
                if expr.type_args_parse_error is None:
                    expr.type_args_parse_error = e.message
                return
        else:
            new_args.append(ta)
    expr.type_args = tuple(new_args)

def resolve_refs(module: TpyModule) -> None:
    """Resolve every TypeRefNode in `module` to TpyType in place.

    `TpyInferFromDefaultRef` field markers (class-body `name = expr`
    without a type annotation) are handled inline via
    `_infer_field_type_from_default`; it returns a TpyType or None,
    the latter triggering a "Cannot infer type" SemanticError at the
    field's loc.
    """
    resolver = module.resolver
    if resolver is None:
        raise SemanticError(
            "TypeRefNode encountered but no parser resolver is attached "
            "(module was not produced by the current-phase Parser)"
        )

    def _resolve(t, scope=None):
        if isinstance(t, _REF_TYPES):
            return resolver.resolve(t, scope)
        return t

    def _resolve_return_type(t, scope=None):
        # Parser emits None for an absent return annotation.  Substitute
        # VOID so downstream readers see a concrete TpyType.
        if t is None:
            return VOID
        return _resolve(t, scope)

    # Type parameter bounds: resolved with no enclosing type-param
    # scope since bounds reference protocols in scope, not other
    # type params.
    def _resolve_bounds(bounds):
        if not bounds:
            return bounds
        return {name: _resolve(t, None) for name, t in bounds.items()}

    # Type alias RHS: resolve in declaration order, passing
    # `pending_alias=alias_name` to the resolver so same-body self-
    # references produce a `NominalType(name)` placeholder.  Register
    # each resolved alias in the registry immediately so later alias
    # bodies can find it by name.  Recursive unions are detected post-
    # resolution.
    if module.type_aliases:
        resolved_aliases: dict[str, tuple[TpyType, object]] = {}
        for alias_name, (alias_ref, alias_loc) in module.type_aliases.items():
            if isinstance(alias_ref, _REF_TYPES):
                alias_type = resolver.resolve(alias_ref, None, pending_alias=alias_name)
            else:
                alias_type = alias_ref
            if isinstance(alias_type, UnionType) and _contains_self_reference(alias_type, alias_name):
                err = validate_recursive_union_paths(alias_name, alias_type.members)
                if err is not None:
                    raise SemanticError(err, loc=alias_loc)
                module.recursive_union_names.add(alias_name)
            resolver.registry.register_type_alias(alias_name, alias_type)
            resolved_aliases[alias_name] = (alias_type, alias_loc)
        module.type_aliases = resolved_aliases

    # Top-level functions (methods are resolved in the record loop
    # below; nested defs are eagerly resolved at parse time).
    for func in module.functions:
        scope = _func_scope(func)
        func.params = [(n, _resolve(t, scope)) for (n, t) in func.params]
        func.return_type = _resolve_return_type(func.return_type, scope)
        if func.vararg_type is not None:
            func.vararg_type = _resolve(func.vararg_type, scope)
        if func.kwarg_type is not None:
            func.kwarg_type = _resolve(func.kwarg_type, scope)
        func.type_param_bounds = _resolve_bounds(func.type_param_bounds)

    # All records (including nested) -- fields, methods, bases.  Record
    # methods use a merged scope (record type params + method type
    # params) when resolving param / return / vararg refs; self is also
    # resolved here so `method_expansion` sees TpyType.  Bases resolve
    # here too, so sema's own class-body validation runs after any
    # base-resolution error would surface.
    for record in module.all_records():
        scope = _record_scope(record)
        record.type_param_bounds = _resolve_bounds(record.type_param_bounds)
        record.bases = [_resolve(b, scope) for b in record.bases]
        for fld in record.fields:
            if isinstance(fld.type, TpyInferFromDefaultRef):
                # Parser emits this marker for bare `name = expr`
                # class-body assignments; infer from the parsed default
                # expression, raising only on genuine failure.
                inferred = _infer_field_type_from_default(fld.default_expr, resolver)
                if inferred is None:
                    raise SemanticError(
                        f"Cannot infer type for field '{fld.name}'",
                        loc=fld.type.loc,
                    )
                fld.type = inferred
            else:
                fld.type = _resolve(fld.type, scope)
        for method in record.methods:
            method_scope = _merged_method_scope(scope, method)
            method.params = [(n, _resolve(t, method_scope)) for (n, t) in method.params]
            method.return_type = _resolve_return_type(method.return_type, method_scope)
            if method.vararg_type is not None:
                method.vararg_type = _resolve(method.vararg_type, method_scope)
            if method.self_annotation is not None:
                method.self_annotation = _resolve(method.self_annotation, method_scope)
            if method.kwarg_type is not None:
                method.kwarg_type = _resolve(method.kwarg_type, method_scope)
            method.type_param_bounds = _resolve_bounds(method.type_param_bounds)

    # Protocol MethodSignatures + field types: resolve under the
    # protocol's own type-param scope.
    for protocol in module.protocols:
        scope = _protocol_scope(protocol)
        protocol.fields = [
            (fname, _resolve(ftype, scope)) for fname, ftype in protocol.fields
        ]
        for msig in protocol.methods:
            msig.params = [(n, _resolve(t, scope)) for (n, t) in msig.params]
            msig.return_type = _resolve_return_type(msig.return_type, scope)

    # Top-level TpyVarDecls
    for stmt in module.top_level_stmts:
        if isinstance(stmt, TpyVarDecl) and stmt.type is not None:
            stmt.type = _resolve(stmt.type)

    # Resolve body-level type refs: in-body `TpyVarDecl.type`
    # annotations, `TpyCall.call_type`, `TpyCall.type_args`,
    # `TpyMethodCall.type_args`.  Shared walker with
    # `resolve_method_body_refs` (used for macro-added methods).
    for func in module.functions:
        _walk_body(func.body, _func_scope(func), resolver)
    for record in module.all_records():
        rscope = _record_scope(record)
        for method in record.methods:
            _walk_body(method.body, _merged_method_scope(rscope, method), resolver)
    _walk_body(module.top_level_stmts, None, resolver)
