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
    TpyBoolLiteral,
    TpyBreak,
    TpyCall,
    TpyExprStmt,
    TpyFieldAccess,
    TpyForEach,
    TpyFunction,
    TpyIf,
    TpyImport,
    TpyIntLiteral,
    TpyLiteralPattern,
    TpyMatch,
    TpyMatchCase,
    TpyModule,
    TpyName,
    TpyRecord,
    TpyReturn,
    TpyStrLiteral,
    TpySubscript,
    TpyTypeRef,
    TpyUnaryOp,
    TpyVarDecl,
    TpyWhile,
    TpyWildcardPattern,
)
from ..typesys import FieldInfo, RecordInfo
from .nodes import (
    API_VERSION,
    Assign,
    Attr,
    BinOp,
    BinOpKind,
    BoolLit,
    Call,
    CmpOpKind,
    Compare,
    ExprStmt,
    Field,
    ForEach,
    ForRange,
    FrontendModule,
    FromImport,
    Function,
    If,
    Import,
    IntLit,
    IntTypeArg,
    Loc,
    Match,
    MatchCase,
    MatchValue,
    MatchWildcard,
    Name,
    NamedType,
    Param,
    PointerType,
    RangeDir,
    Record,
    RepeatUntil,
    Return,
    StrLit,
    Subscript,
    TypeArg,
    TypeExpr,
    TypeTypeArg,
    UnaryOp,
    UnaryOpKind,
    VarDecl,
    While,
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

# IR comparison enum -> parser string opcode (matches the parser's
# `_CMPOP_TO_STR` table). Used for both the single-op case (lowers to
# `TpyBinOp`) and the chained-op case (lowers to `TpyChainedCompare`).
_CMPOP_OP_STR: dict[CmpOpKind, str] = {
    CmpOpKind.EQ: "==",
    CmpOpKind.NE: "!=",
    CmpOpKind.LT: "<",
    CmpOpKind.LE: "<=",
    CmpOpKind.GT: ">",
    CmpOpKind.GE: ">=",
    CmpOpKind.IS: "is",
    CmpOpKind.IS_NOT: "is not",
    CmpOpKind.IN: "in",
    CmpOpKind.NOT_IN: "not in",
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

    # Build the parser-adapter / TypeResolver early so we can register
    # user records into the adapter's registry before lowering any
    # bodies that might reference them. The same TypeResolver the .py
    # parser uses runs over our adapter -- it reads parser-internal
    # attributes/methods exposed on the adapter object.
    from .resolver_adapter import make_plugin_resolver
    resolver = make_plugin_resolver(
        module_name=fm.qname,
        imports=dict(module_imports),
        name_index=tuple(
            (local, mod, original)
            for local, (mod, original) in name_to_origin.items()
        ),
    )

    # Records: lower each and register the resulting RecordInfo into
    # the resolver's registry so type references downstream (var decls,
    # function params, other records) can resolve user record names.
    tpy_records: list[TpyRecord] = []
    record_class_names: set[str] = set()
    for rec in fm.records:
        lowered_rec, rinfo = _lower_record(rec, plugin_name, fm, diags)
        if lowered_rec is None or rinfo is None:
            continue
        tpy_records.append(lowered_rec)
        resolver.registry.register_record(rinfo)
        record_class_names.add(rec.name)
    if record_class_names:
        # Adapter exposes a `frozenset` here; merge with whatever the
        # resolver-adapter constructor pre-populated (currently empty).
        resolver._parser._module_class_names = frozenset(
            resolver._parser._module_class_names | record_class_names
        )

    # Functions: lower each to a TpyFunction. Function bodies do not
    # share the module-level statement list, so lowering them before
    # walking top_level_stmts keeps things tidy.
    tpy_functions: list[TpyFunction] = []
    for fn in fm.functions:
        lowered_fn = _lower_function(fn, name_to_origin, plugin_name, fm, diags)
        if lowered_fn is not None:
            tpy_functions.append(lowered_fn)

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
        records=tpy_records,
        functions=tpy_functions,
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
    if isinstance(stmt, If):
        cond = _lower_expr(stmt.cond, name_to_origin, plugin_name, fm, diags)
        if cond is None:
            return None
        then_body = _lower_stmt_list(
            stmt.then_body, name_to_origin, plugin_name, fm, diags)
        else_body = _lower_stmt_list(
            stmt.else_body, name_to_origin, plugin_name, fm, diags)
        return TpyIf(condition=cond, then_body=then_body, else_body=else_body, loc=loc)
    if isinstance(stmt, While):
        cond = _lower_expr(stmt.cond, name_to_origin, plugin_name, fm, diags)
        if cond is None:
            return None
        body = _lower_stmt_list(
            stmt.body, name_to_origin, plugin_name, fm, diags)
        return TpyWhile(condition=cond, body=body, loc=loc)
    if isinstance(stmt, RepeatUntil):
        # Desugar to `while True: body; if cond: break`. Pascal's
        # `repeat...until` runs the body at least once and exits when
        # cond becomes true; the `while True` shape preserves that.
        cond = _lower_expr(stmt.cond, name_to_origin, plugin_name, fm, diags)
        if cond is None:
            return None
        body = _lower_stmt_list(
            stmt.body, name_to_origin, plugin_name, fm, diags)
        body.append(TpyIf(
            condition=cond,
            then_body=[TpyBreak(loc=loc)],
            else_body=[],
            loc=loc,
        ))
        return TpyWhile(
            condition=TpyBoolLiteral(value=True, loc=loc),
            body=body, loc=loc,
        )
    if isinstance(stmt, ForRange):
        # Desugar to `for var in range(start, end[+1], [-1]): body`.
        # `inclusive=True` (Pascal default) bumps the endpoint by one
        # so the upper bound is visited; descending loops use a step
        # of -1 and bump the endpoint by -1 instead.
        start = _lower_expr(stmt.start, name_to_origin, plugin_name, fm, diags)
        end = _lower_expr(stmt.end, name_to_origin, plugin_name, fm, diags)
        if start is None or end is None:
            return None
        bump = 1 if stmt.direction == RangeDir.ASC else -1
        if stmt.inclusive:
            end = TpyBinOp(
                left=end,
                op="+" if bump > 0 else "-",
                right=TpyIntLiteral(value=1, loc=loc),
                loc=loc,
            )
        range_args = [start, end]
        if bump != 1:
            range_args.append(TpyIntLiteral(value=bump, loc=loc))
        range_call = TpyCall(
            func=TpyName(name="range", loc=loc),
            args=range_args,
            loc=loc,
        )
        body = _lower_stmt_list(
            stmt.body, name_to_origin, plugin_name, fm, diags)
        return TpyForEach(var=stmt.var, iterable=range_call, body=body, loc=loc)
    if isinstance(stmt, ForEach):
        it = _lower_expr(stmt.iter, name_to_origin, plugin_name, fm, diags)
        if it is None:
            return None
        body = _lower_stmt_list(
            stmt.body, name_to_origin, plugin_name, fm, diags)
        return TpyForEach(var=stmt.var, iterable=it, body=body, loc=loc)
    if isinstance(stmt, Return):
        value = None
        if stmt.value is not None:
            value = _lower_expr(stmt.value, name_to_origin,
                                plugin_name, fm, diags)
            if value is None:
                return None
        return TpyReturn(value=value, loc=loc)
    if isinstance(stmt, Match):
        subject = _lower_expr(stmt.subject, name_to_origin, plugin_name, fm, diags)
        if subject is None:
            return None
        tpy_cases: list[TpyMatchCase] = []
        for case in stmt.cases:
            pattern = _lower_pattern(case.pattern, name_to_origin,
                                     plugin_name, fm, diags)
            if pattern is None:
                return None
            guard = None
            if case.guard is not None:
                guard = _lower_expr(case.guard, name_to_origin,
                                    plugin_name, fm, diags)
                if guard is None:
                    return None
            body = _lower_stmt_list(
                case.body, name_to_origin, plugin_name, fm, diags)
            tpy_cases.append(TpyMatchCase(
                pattern=pattern, guard=guard, body=body,
                loc=_to_source_loc(case.loc),
            ))
        return TpyMatch(subject=subject, cases=tpy_cases, loc=loc)
    diags.append(_ir_invalid(
        plugin_name, fm,
        f"unsupported top-level stmt kind: {type(stmt).__name__}",
    ))
    return None


def _lower_stmt_list(stmts, name_to_origin, plugin_name, fm, diags):
    out: list = []
    for s in stmts:
        lowered = _lower_stmt(s, name_to_origin, plugin_name, fm, diags)
        if lowered is not None:
            out.append(lowered)
    return out


def _is_known_record_name(name: str, fm: FrontendModule) -> bool:
    """True iff `name` matches the name of a record declared in this
    FrontendModule. Used during expression lowering to recognise
    constructor calls (`Point()`) so the resulting TpyCall carries a
    `call_type` -- the same shape the Python parser emits for
    type-instantiation calls.
    """
    return any(rec.name == name for rec in fm.records)


def _lower_record(
    rec: Record,
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
) -> tuple[TpyRecord | None, RecordInfo | None]:
    """Lower an IR `Record` to a `TpyRecord` plus the parallel
    `RecordInfo` the resolver registry consumes.

    Field types lower to `TpyTypeRef`; sema's `resolve_refs` pass turns
    them into real `TpyType`s using the resolver. The TpyRecord and
    RecordInfo share the same FieldInfo objects (parser convention),
    so mutation by either downstream consumer is visible to the other.
    """
    field_infos: list[FieldInfo] = []
    for f in rec.fields:
        t = _lower_type(f.type, plugin_name, fm, diags)
        if t is None:
            return None, None
        default_expr = None
        if f.default is not None:
            default_expr = _lower_expr(
                f.default, {}, plugin_name, fm, diags)
            if default_expr is None:
                return None, None
        field_infos.append(FieldInfo(
            name=f.name, type=t,
            default_expr=default_expr,
            loc=_to_source_loc(f.loc),
        ))
    if rec.methods or rec.nested_records or rec.nested_enums:
        diags.append(_ir_invalid(
            plugin_name, fm,
            "M5 records support fields only (methods / nested types "
            "are reserved for later milestones)",
        ))
        return None, None
    tpy_rec = TpyRecord(
        name=rec.name,
        fields=field_infos,
    )
    # RecordInfo's `module` is the public module qname; for plugin
    # modules that match the FrontendModule.qname directly (no facade
    # rewriting). Builtin-type-key stays unset because plugin records
    # aren't `@builtin_type` decorated.
    record_info = RecordInfo(
        name=rec.name,
        fields=field_infos,
        has_init=False,
        module=fm.qname,
    )
    return tpy_rec, record_info


def _lower_function(
    fn: Function,
    name_to_origin: dict[str, tuple[str, str]],
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
) -> TpyFunction | None:
    """Lower a frontend `Function` to a `TpyFunction`. Plugins emit
    functions whose params already carry resolved types (NamedType /
    PointerType); the body uses the M4 expression / statement set.
    """
    params: list = []
    for p in fn.params:
        t = _lower_type(p.type, plugin_name, fm, diags)
        if t is None:
            return None
        if p.default is not None:
            diags.append(_ir_invalid(
                plugin_name, fm,
                "parameter defaults are not lowered in M4",
            ))
            return None
        params.append((p.name, t))
    return_type = None
    if fn.return_type is not None:
        return_type = _lower_type(fn.return_type, plugin_name, fm, diags)
        if return_type is None:
            return None
    body = _lower_stmt_list(
        fn.body, name_to_origin, plugin_name, fm, diags)
    return TpyFunction(
        name=fn.name,
        params=params,
        return_type=return_type,
        body=body,
    )


def _lower_pattern(p, name_to_origin, plugin_name, fm, diags):
    loc = _to_source_loc(p.loc) if getattr(p, "loc", None) else None
    if isinstance(p, MatchWildcard):
        return TpyWildcardPattern(loc=loc)
    if isinstance(p, MatchValue):
        # M3 only emits literal value patterns (IntLit / StrLit / BoolLit).
        # Sema's TpyLiteralPattern accepts int/float/str/bool/None.
        v = p.value
        if isinstance(v, IntLit):
            return TpyLiteralPattern(value=v.value, loc=loc)
        if isinstance(v, StrLit):
            return TpyLiteralPattern(value=v.value, loc=loc)
        if isinstance(v, BoolLit):
            return TpyLiteralPattern(value=v.value, loc=loc)
        diags.append(_ir_invalid(
            plugin_name, fm,
            f"MatchValue payload must be a literal in M3, got {type(v).__name__}",
        ))
        return None
    diags.append(_ir_invalid(
        plugin_name, fm,
        f"unsupported MatchPattern kind: {type(p).__name__}",
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
    if isinstance(expr, BoolLit):
        out = TpyBoolLiteral(value=expr.value)
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
    if isinstance(expr, Compare):
        if not expr.ops or len(expr.ops) != len(expr.comparators):
            diags.append(_ir_invalid(
                plugin_name, fm,
                "Compare requires ops and comparators of equal, "
                "non-zero length",
            ))
            return None
        # Single-op comparison lowers to `TpyBinOp` (matches what the
        # parser produces for a non-chained Python compare). Chained
        # form (a < b < c) would lower to `TpyChainedCompare`; no
        # M3-target source language uses chains, so it's left for a
        # future plugin to opt into.
        if len(expr.ops) != 1:
            diags.append(_ir_invalid(
                plugin_name, fm,
                "chained comparisons are not lowered in M3",
            ))
            return None
        op_str = _CMPOP_OP_STR.get(expr.ops[0])
        if op_str is None:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"unsupported CmpOpKind: {expr.ops[0]}",
            ))
            return None
        lhs = _lower_expr(expr.lhs, name_to_origin, plugin_name, fm, diags)
        rhs = _lower_expr(expr.comparators[0], name_to_origin,
                          plugin_name, fm, diags)
        if lhs is None or rhs is None:
            return None
        out = TpyBinOp(left=lhs, op=op_str, right=rhs)
        out.loc = loc
        return out
    if isinstance(expr, Attr):
        target = _lower_expr(expr.target, name_to_origin, plugin_name, fm, diags)
        if target is None:
            return None
        out = TpyFieldAccess(obj=target, field=expr.ident)
        out.loc = loc
        return out
    if isinstance(expr, Subscript):
        target = _lower_expr(expr.target, name_to_origin, plugin_name, fm, diags)
        index = _lower_expr(expr.index, name_to_origin, plugin_name, fm, diags)
        if target is None or index is None:
            return None
        out = TpySubscript(obj=target, index=index)
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
        # `type_args` on the IR maps to the parser's `call_type`: a
        # TpyTypeRef whose `args` are the explicit type/int args. This
        # is what powers generic-constructor calls like
        # `Array[Int32, 8]()` and `Container[T]()`.
        if expr.type_args:
            if not isinstance(expr.callee, Name):
                diags.append(_ir_invalid(
                    plugin_name, fm,
                    "type_args only allowed on calls with a bare-name callee",
                ))
                return None
            type_ref_args: list = []
            for a in expr.type_args:
                if isinstance(a, IntTypeArg):
                    type_ref_args.append(a.value)
                elif isinstance(a, TypeTypeArg):
                    lowered = _lower_type(a.value, plugin_name, fm, diags)
                    if lowered is None:
                        return None
                    type_ref_args.append(lowered)
                else:
                    diags.append(_ir_invalid(
                        plugin_name, fm,
                        f"unsupported TypeArg on Call: {type(a).__name__}",
                    ))
                    return None
            out.call_type = TpyTypeRef(
                name=expr.callee.ident, args=tuple(type_ref_args), loc=loc,
            )
        # If the callee is a bare Name that we imported via FromImport,
        # tag the call so sema knows which module exports it (parallel
        # to what Parser._resolve_call_import does for .py sources).
        if isinstance(expr.callee, Name):
            origin = name_to_origin.get(expr.callee.ident)
            if origin is not None:
                out.resolved_import = origin
            # Constructor call (no explicit type_args): if the callee
            # is the name of a user-declared record in this module,
            # tag the call as a constructor so sema's record-instantiation
            # path fires with the right module-qname.
            elif (out.call_type is None
                  and _is_known_record_name(expr.callee.ident, fm)):
                out.call_type = TpyTypeRef(
                    name=expr.callee.ident, args=(), loc=loc,
                )
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

    M2: `NamedType` (with generic args). M4: `PointerType` for Pascal
    `var` parameters; lowers to `Ptr[T]` via the canonical resolver
    wrapper name. Other wrappers (Optional/Own/Readonly/...) land
    milestone-by-milestone.
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
    if isinstance(t, PointerType):
        inner = _lower_type(t.inner, plugin_name, fm, diags)
        if inner is None:
            return None
        # `tpy:Ptr` is the canonical-name spelling TypeResolver
        # recognises for the Ptr wrapper (see type_resolver.py's
        # structural-wrapper table). The walker that parses the
        # surface form `Ptr[T]` produces this name only when the
        # source identifier truly resolved to `tpy.Ptr`; for plugin
        # lowering we know the binding by construction.
        return TpyTypeRef(name="tpy:Ptr", args=(inner,),
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
