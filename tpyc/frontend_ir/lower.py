"""Lowering pass: FrontendModule (IR) -> TpyModule (parser AST).

Lowering owns the IR -> TpyModule adapter and the parser-internal field
synthesis that a Python parse would normally perform (import dicts,
TpyImport emission, call-site `resolved_import` tagging).

It does NOT resolve names or types -- that is sema's job. For M1 the IR
has no type expressions, so symbolic-type lowering is stubbed.

Diagnostics emitted here use the `FrontendDiagnostic` envelope with
category `PLUGIN_IR_INVALID` (structural problem in plugin output) or
`LOWERING_INTERNAL` (compiler bug after validation passed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..diagnostics import Diagnostic, DiagnosticLevel
from ..frontend_diagnostics import FrontendDiagnostic, FrontendDiagnosticCategory
from ..parse.nodes import (
    ModuleDirectives,
    SourceLocation,
    TpyCall,
    TpyExprStmt,
    TpyImport,
    TpyModule,
    TpyName,
    TpyStrLiteral,
)
from .nodes import (
    API_VERSION,
    Call,
    ExprStmt,
    FrontendModule,
    FromImport,
    Import,
    Loc,
    Name,
    StrLit,
)


class _NullResolver:
    """Stub TypeResolver for plugin-lowered modules with no symbolic types.

    `resolve_refs` raises unconditionally when `module.resolver is None`,
    even if a module has nothing to resolve. The stub satisfies the
    null-check and raises a clear error if a future plugin emits a
    TypeRefNode without arranging proper type lowering. M1's hello-world
    has no types so `.resolve()` is never reached.
    """

    def __init__(self) -> None:
        # `resolve_refs` and `_populate_submodule_registry` touch a few
        # registry attributes (`.modules`, `register_type_alias`,
        # `get_record`, `register_record`) on the resolver's registry.
        # A real -- but empty -- `TypeRegistry` satisfies all of them
        # cheaply and avoids modeling the surface twice.
        from ..typesys import TypeRegistry
        self.registry = TypeRegistry()

    def resolve(self, *args, **kwargs):
        raise NotImplementedError(
            "frontend-plugin lowering has no type resolver attached -- "
            "M1 does not lower symbolic types"
        )

    def refresh_module_aliases(self) -> None:
        pass

    def canonicalize_import_table(self, lookup) -> None:
        pass

    def index_imported_name(self, *args, **kwargs) -> None:
        pass


@dataclass
class LoweredFrontend:
    """Result of lowering one FrontendModule.

    `module` is `None` when validation failed before a partial TpyModule
    could be constructed; callers should bail on the affected module.
    `diagnostics` holds wrapped FrontendDiagnostics produced by lowering
    plus any diagnostics the plugin returned in its FrontendOutput.
    """
    module: TpyModule | None
    diagnostics: list[FrontendDiagnostic] = field(default_factory=list)


def lower_module(
    fm: FrontendModule,
    plugin_name: str,
    plugin_diagnostics: Iterable[Diagnostic] = (),
) -> LoweredFrontend:
    """Lower a FrontendModule produced by a plugin to a TpyModule.

    `plugin_name` is used to tag wrapped diagnostics from the plugin.
    `plugin_diagnostics` come from `FrontendOutput.diagnostics`; they
    are wrapped with category `PLUGIN_REPORTED` and propagated.
    """
    diags: list[FrontendDiagnostic] = []

    for d in plugin_diagnostics:
        diags.append(FrontendDiagnostic(
            diagnostic=d,
            category=FrontendDiagnosticCategory.PLUGIN_REPORTED,
            plugin_name=plugin_name,
            source_language=fm.source_language or None,
        ))
    plugin_had_error = any(
        fd.diagnostic.level == DiagnosticLevel.ERROR
        and fd.category == FrontendDiagnosticCategory.PLUGIN_REPORTED
        for fd in diags
    )

    # Structural validation. Failures here surface as PLUGIN_IR_INVALID.
    if fm.api_version != API_VERSION:
        diags.append(_ir_invalid(
            plugin_name, fm,
            f"FrontendModule.api_version={fm.api_version}; "
            f"compiler supports api_version={API_VERSION}",
        ))
        return LoweredFrontend(module=None, diagnostics=diags)

    if not fm.qname:
        diags.append(_ir_invalid(
            plugin_name, fm,
            "FrontendModule.qname is empty",
        ))
        return LoweredFrontend(module=None, diagnostics=diags)

    # Imports: build the four dicts the parser/sema expect and emit
    # corresponding TpyImport statements at the head of top_level_stmts.
    module_imports: dict[str, set[tuple[str, str]] | None] = {}
    user_module_imports: dict[str, int] = {}
    bare_module_imports: set[str] = set()
    module_aliases: dict[str, str] = {}
    name_to_origin: dict[str, tuple[str, str]] = {}
    leading_imports: list[TpyImport] = []

    for imp in fm.imports:
        loc = _to_source_loc(imp.loc)
        lineno = loc.line if loc else 0
        if isinstance(imp, Import):
            if not imp.module:
                diags.append(_ir_invalid(
                    plugin_name, fm, "Import.module is empty"))
                continue
            if imp.module not in module_imports:
                module_imports[imp.module] = set()
            user_module_imports[imp.module] = lineno
            bare_module_imports.add(imp.module)
            if imp.alias is not None:
                module_aliases[imp.module] = imp.alias
            leading_imports.append(TpyImport(
                module_name=imp.module, alias=imp.alias, loc=loc))
        elif isinstance(imp, FromImport):
            if not imp.module:
                diags.append(_ir_invalid(
                    plugin_name, fm, "FromImport.module is empty"))
                continue
            bucket = module_imports.setdefault(imp.module, set())
            assert isinstance(bucket, set)  # never None for FromImport
            for nm in imp.names:
                bucket.add((nm.original, nm.local))
                name_to_origin[nm.local] = (imp.module, nm.original)
            user_module_imports[imp.module] = lineno
            leading_imports.append(TpyImport(
                module_name=imp.module, loc=loc))
        else:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"unknown import node kind: {type(imp).__name__}",
            ))

    # Statements
    top_level_stmts = list(leading_imports)
    for stmt in fm.top_level_stmts:
        lowered = _lower_stmt(stmt, name_to_origin, plugin_name, fm, diags)
        if lowered is not None:
            top_level_stmts.append(lowered)

    # Directives: map FrontendDirectives onto ModuleDirectives.
    directives = ModuleDirectives(
        includes=list(fm.directives.cpp_includes),
        link_libs=list(fm.directives.link_libs),
        third_party_deps=list(fm.directives.third_party_deps),
        native_module=fm.directives.native_module,
        cpp_namespace=fm.directives.cpp_namespace,
    )

    has_ir_error = any(
        fd.category == FrontendDiagnosticCategory.PLUGIN_IR_INVALID
        for fd in diags
    )
    if has_ir_error or plugin_had_error:
        return LoweredFrontend(module=None, diagnostics=diags)

    module = TpyModule(
        records=[],
        functions=[],
        protocols=[],
        enums=[],
        top_level_stmts=top_level_stmts,
        source_lines=list(fm.source_lines),
        imports=module_imports,
        user_module_imports=user_module_imports,
        module_aliases=module_aliases,
        bare_module_imports=bare_module_imports,
        directives=directives,
        # No real TypeResolver -- the IR carries no symbolic types in
        # M1. `_NullResolver` satisfies `resolve_refs`'s null-check and
        # raises clearly if a future plugin emits a TypeRefNode without
        # also arranging proper type lowering.
        resolver=_NullResolver(),
    )
    return LoweredFrontend(module=module, diagnostics=diags)


def _lower_stmt(
    stmt,
    name_to_origin: dict[str, tuple[str, str]],
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
):
    if isinstance(stmt, ExprStmt):
        expr = _lower_expr(stmt.value, name_to_origin, plugin_name, fm, diags)
        if expr is None:
            return None
        out = TpyExprStmt(expr=expr)
        out.loc = _to_source_loc(stmt.loc)
        return out
    diags.append(_ir_invalid(
        plugin_name, fm,
        f"unsupported top-level stmt kind: {type(stmt).__name__}",
    ))
    return None


def _lower_expr(
    expr,
    name_to_origin: dict[str, tuple[str, str]],
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
):
    if isinstance(expr, StrLit):
        out = TpyStrLiteral(value=expr.value)
        out.loc = _to_source_loc(expr.loc)
        return out
    if isinstance(expr, Name):
        out = TpyName(name=expr.ident)
        out.loc = _to_source_loc(expr.loc)
        return out
    if isinstance(expr, Call):
        callee = _lower_expr(expr.callee, name_to_origin, plugin_name, fm, diags)
        if callee is None:
            return None
        args = []
        for a in expr.args:
            la = _lower_expr(a, name_to_origin, plugin_name, fm, diags)
            if la is None:
                return None
            args.append(la)
        out = TpyCall(func=callee, args=args)
        out.loc = _to_source_loc(expr.loc)
        # If the callee is a bare Name that we imported via FromImport,
        # tag the call so sema knows which module exports it (parallel
        # to what Parser._resolve_call_import does for .py sources).
        if isinstance(expr.callee, Name):
            origin = name_to_origin.get(expr.callee.ident)
            if origin is not None:
                out.resolved_import = origin
        return out
    diags.append(_ir_invalid(
        plugin_name, fm,
        f"unsupported expr kind: {type(expr).__name__}",
    ))
    return None


def _to_source_loc(loc: Loc | None) -> SourceLocation | None:
    if loc is None:
        return None
    return SourceLocation(
        line=loc.line,
        column=max(0, loc.col - 1),  # parser uses 0-based columns
        file=str(loc.file),
    )


def _ir_invalid(
    plugin_name: str, fm: FrontendModule, message: str,
) -> FrontendDiagnostic:
    return FrontendDiagnostic(
        diagnostic=Diagnostic(level=DiagnosticLevel.ERROR, message=message),
        category=FrontendDiagnosticCategory.PLUGIN_IR_INVALID,
        plugin_name=plugin_name,
        source_language=fm.source_language or None,
    )
