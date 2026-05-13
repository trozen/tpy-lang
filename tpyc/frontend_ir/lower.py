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
    TpyAssign,
    TpyBinOp,
    TpyCall,
    TpyExprStmt,
    TpyImport,
    TpyIntLiteral,
    TpyModule,
    TpyName,
    TpyStrLiteral,
    TpyTypeRef,
    TpyUnaryOp,
    TpyVarDecl,
)
from .nodes import (
    API_VERSION,
    Assign,
    BinOp,
    BinOpKind,
    Call,
    ExprStmt,
    FrontendModule,
    FromImport,
    Import,
    IntLit,
    IntTypeArg,
    Loc,
    Name,
    NamedType,
    StrLit,
    TypeArg,
    TypeExpr,
    TypeTypeArg,
    UnaryOp,
    UnaryOpKind,
    VarDecl,
)


# Map IR operator enums onto the string opcodes TPy's parser AST uses
# (see `tpyc/parse/parser.py` -- `_BINOP_TO_STR` / `_UNARYOP_TO_STR`).
_BINOP_OP_STR: dict[BinOpKind, str] = {
    BinOpKind.ADD: "+",
    BinOpKind.SUB: "-",
    BinOpKind.MUL: "*",
    BinOpKind.TRUE_DIV: "div",
    BinOpKind.FLOOR_DIV: "//",
    BinOpKind.MOD: "%",
    BinOpKind.POW: "**",
    BinOpKind.BIT_OR: "|",
    BinOpKind.BIT_XOR: "^",
    BinOpKind.BIT_AND: "&",
    BinOpKind.LSHIFT: "<<",
    BinOpKind.RSHIFT: ">>",
    BinOpKind.LOGICAL_AND: "&&",
    BinOpKind.LOGICAL_OR: "||",
}

_UNARYOP_OP_STR: dict[UnaryOpKind, str] = {
    UnaryOpKind.POS: "+",
    UnaryOpKind.NEG: "-",
    UnaryOpKind.NOT: "!",
    UnaryOpKind.INVERT: "~",
}


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
    # `module_aliases` follows the parser convention -- canonical name
    # keys the local alias -- not the reverse map used at lookup time.
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

    # Build a real `TypeResolver` over a parser-shaped adapter. This is
    # the same `TypeResolver` the .py parser uses; the adapter just
    # exposes the parser-internal attributes/methods the resolver reads.
    # Lowering is responsible for populating the adapter's import
    # name-index from the IR's `FromImport` nodes so that
    # `_resolve_type_name` can route a bare type like `"Int32"` to its
    # origin module before sema looks up the primitive.
    from .resolver_adapter import make_plugin_resolver
    resolver = make_plugin_resolver(
        module_name=fm.qname,
        imports=dict(module_imports),
        name_index=tuple(
            (local, mod, original)
            for local, (mod, original) in name_to_origin.items()
        ),
    )
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
        resolver=resolver,
    )
    return LoweredFrontend(module=module, diagnostics=diags)


def _lower_stmt(
    stmt,
    name_to_origin: dict[str, tuple[str, str]],
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
):
    loc = _to_source_loc(stmt.loc) if getattr(stmt, "loc", None) else None
    if isinstance(stmt, ExprStmt):
        expr = _lower_expr(stmt.value, name_to_origin, plugin_name, fm, diags)
        if expr is None:
            return None
        out = TpyExprStmt(expr=expr)
        out.loc = loc
        return out
    if isinstance(stmt, VarDecl):
        type_ref = (_lower_type(stmt.type, plugin_name, fm, diags)
                    if stmt.type is not None else None)
        init = (_lower_expr(stmt.init, name_to_origin, plugin_name, fm, diags)
                if stmt.init is not None else None)
        if stmt.init is not None and init is None:
            return None
        return TpyVarDecl(name=stmt.name, type=type_ref, init=init, loc=loc)
    if isinstance(stmt, Assign):
        if len(stmt.targets) != 1:
            diags.append(_ir_invalid(
                plugin_name, fm,
                "Assign requires exactly one target in M2 "
                "(chained assignment lands later)",
            ))
            return None
        target = _lower_expr(stmt.targets[0], name_to_origin, plugin_name, fm, diags)
        value = _lower_expr(stmt.value, name_to_origin, plugin_name, fm, diags)
        if target is None or value is None:
            return None
        return TpyAssign(target=target, value=value, loc=loc)
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
    loc = _to_source_loc(expr.loc) if getattr(expr, "loc", None) else None
    if isinstance(expr, StrLit):
        out = TpyStrLiteral(value=expr.value)
        out.loc = loc
        return out
    if isinstance(expr, IntLit):
        out = TpyIntLiteral(value=expr.value)
        out.loc = loc
        return out
    if isinstance(expr, Name):
        out = TpyName(name=expr.ident)
        out.loc = loc
        return out
    if isinstance(expr, BinOp):
        op_str = _BINOP_OP_STR.get(expr.op)
        if op_str is None:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"unsupported BinOpKind: {expr.op}",
            ))
            return None
        lhs = _lower_expr(expr.lhs, name_to_origin, plugin_name, fm, diags)
        rhs = _lower_expr(expr.rhs, name_to_origin, plugin_name, fm, diags)
        if lhs is None or rhs is None:
            return None
        out = TpyBinOp(left=lhs, op=op_str, right=rhs)
        out.loc = loc
        return out
    if isinstance(expr, UnaryOp):
        op_str = _UNARYOP_OP_STR.get(expr.op)
        if op_str is None:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"unsupported UnaryOpKind: {expr.op}",
            ))
            return None
        operand = _lower_expr(expr.operand, name_to_origin, plugin_name, fm, diags)
        if operand is None:
            return None
        out = TpyUnaryOp(op=op_str, operand=operand)
        out.loc = loc
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
        out.loc = loc
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


def _lower_type(
    t: TypeExpr,
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
) -> TpyTypeRef | None:
    """Lower a symbolic IR type to a parser `TpyTypeRef`.

    M2 only carries `NamedType` (possibly with generic args); wrapper
    types (Optional/Ptr/Own/...) land milestone-by-milestone.
    """
    if isinstance(t, NamedType):
        args: list = []
        for a in t.args:
            if isinstance(a, IntTypeArg):
                args.append(a.value)
            elif isinstance(a, TypeTypeArg):
                lowered = _lower_type(a.value, plugin_name, fm, diags)
                if lowered is None:
                    return None
                args.append(lowered)
            else:
                diags.append(_ir_invalid(
                    plugin_name, fm,
                    f"unsupported TypeArg kind: {type(a).__name__}",
                ))
                return None
        return TpyTypeRef(name=t.name, args=tuple(args),
                          loc=_to_source_loc(t.loc))
    diags.append(_ir_invalid(
        plugin_name, fm,
        f"unsupported TypeExpr kind: {type(t).__name__}",
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
