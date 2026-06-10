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
from typing import Any, Iterable

from ..diagnostics import Diagnostic, DiagnosticLevel
from ..frontend_diagnostics import FrontendDiagnostic, FrontendDiagnosticCategory
from ..parse.nodes import (
    FunctionLinkage,
    ModuleDirectives,
    RecordLinkage,
    SourceLocation,
    TpyArrayLiteral,
    TpyAsPattern,
    TpyAssign,
    TpyBinOp,
    TpyCallableRef,
    TpyClassPattern,
    TpyBoolLiteral,
    TpyBreak,
    TpyCall,
    TpyContinue,
    TpyEnum,
    TpyExprStmt,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyForEach,
    TpyFunction,
    TpyIf,
    TpyIfExpr,
    TpyImport,
    TpyIntLiteral,
    TpyLiteralPattern,
    TpyMatch,
    TpyMatchCase,
    TpyMethodCall,
    TpyModule,
    TpyName,
    TpyNoneLiteral,
    TpyRaise,
    TpyRecord,
    TpyReturn,
    TpySetLiteral,
    TpyStrLiteral,
    TpySubscript,
    TpyTypeRef,
    TpyUnaryOp,
    TpyUnionRef,
    TpyValuePattern,
    TpyVarDecl,
    TpyWhile,
    TpyWildcardPattern,
)
from ..typesys import FieldInfo, RecordInfo
from .decorators import (
    DecoratorEntry, DecoratorRegistryError, DecoratorRoute, build_registry)
from .resolver_adapter import make_plugin_resolver
from .nodes import (
    API_VERSION,
    Assign,
    Attr,
    BinOp,
    BinOpKind,
    BoolLit,
    Break,
    Call,
    CallableType,
    CmpOpKind,
    Compare,
    Conditional,
    Continue,
    Decorator,
    Enum,
    EnumValue,
    Expr,
    ExprStmt,
    Field,
    FloatLit,
    ForEach,
    ForRange,
    FrontendModule,
    FromImport,
    Function,
    If,
    Import,
    IntLit,
    IntTypeArg,
    ListLit,
    Loc,
    Match,
    MatchCase,
    MatchClass,
    MatchValue,
    MatchWildcard,
    Name,
    NamedType,
    NoneLit,
    Param,
    PointerType,
    Raise,
    RangeDir,
    Record,
    RepeatUntil,
    Return,
    SetLit,
    StarImport,
    StrLit,
    Subscript,
    TypeArg,
    TypeExpr,
    TypeTypeArg,
    UnaryOp,
    UnaryOpKind,
    UnionType,
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
    *,
    is_entry_point: bool = False,
    decorator_manifest: tuple[DecoratorEntry, ...] = (),
) -> LoweredFrontend:
    """Lower a FrontendModule produced by a plugin to a TpyModule.

    `plugin_name` is used to tag wrapped diagnostics from the plugin.
    `plugin_diagnostics` come from `FrontendOutput.diagnostics`; they
    are wrapped with category `PLUGIN_REPORTED` and propagated.
    `is_entry_point` mirrors TPy's parser-side convention: the
    entry-point module's records and enums are minted with qname prefix
    `__main__` (matching sema's `ctx.module_name = "__main__"` rename)
    so resolver-side placeholders agree with sema's
    `attach_dynamic_type_def` registrations downstream.

    `decorator_manifest` is the plugin's `decorator_manifest` ClassVar;
    it is merged with TPy core's built-in decorators to route each
    emitted `Decorator` (see `frontend_ir/decorators.py`).
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

    # Merge TPy core's built-in decorators with the plugin's manifest. A
    # conflicting redefinition is a plugin-authoring error -- surface it
    # as PLUGIN_IR_INVALID rather than crashing the compiler.
    try:
        registry = build_registry(tuple(decorator_manifest))
    except DecoratorRegistryError as e:
        diags.append(_ir_invalid(plugin_name, fm, str(e)))
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
    star_imports: set[str] = set()

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
        elif isinstance(imp, StarImport):
            if not imp.module:
                diags.append(_ir_invalid(
                    plugin_name, fm, "StarImport.module is empty"))
                continue
            # The compiler's `_expand_star_imports_for_module` consumes
            # `star_imports` + `module_imports[mod] = set()` and fans
            # the imported module's public surface into the importer.
            # Implicit-stdlib star imports (`tpy` / `builtins` /
            # `typing`) are handled at parse time by the .py parser
            # itself; plugin lowering doesn't take that path because
            # source-language plugins would name a non-implicit
            # module here.
            module_imports.setdefault(imp.module, set())
            user_module_imports[imp.module] = lineno
            star_imports.add(imp.module)
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
    # For the entry point we mint qnames against `__main__`; sema
    # follows the same rule for the entry module, so this keeps
    # parser-side and sema-side qnames in lockstep.
    qname_prefix = "__main__" if is_entry_point else fm.qname
    tpy_records: list[TpyRecord] = []
    record_class_names: set[str] = set()
    for rec in fm.records:
        lowered_rec, rinfo = _lower_record(
            rec, plugin_name, fm, diags, qname_prefix, registry)
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

    # Enums: lower each and register a placeholder NominalType so the
    # resolver can route `EnumName` and `EnumName.Member` references.
    # Sema's `register_enum` re-registers later with the fully-populated
    # NominalType + TypeDef.enum payload (members, underlying type, ...).
    tpy_enums: list[TpyEnum] = []
    for en in fm.enums:
        lowered_en = _lower_enum(en, plugin_name, fm, diags)
        if lowered_en is None:
            continue
        tpy_enums.append(lowered_en)
        resolver.registry.register_enum_placeholder(
            en.name, module=qname_prefix,
        )

    # Functions: lower each to a TpyFunction. Function bodies do not
    # share the module-level statement list, so lowering them before
    # walking top_level_stmts keeps things tidy.
    tpy_functions: list[TpyFunction] = []
    for fn in fm.functions:
        lowered_fn = _lower_function(
            fn, name_to_origin, plugin_name, fm, diags, registry=registry)
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
        enums=tpy_enums,
        top_level_stmts=top_level_stmts,
        source_lines=list(fm.source_lines),
        imports=module_imports,
        user_module_imports=user_module_imports,
        module_aliases=module_aliases,
        bare_module_imports=bare_module_imports,
        star_imports=star_imports,
        directives=directives,
        resolver=resolver,
        macro_data=fm.macro_data,
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
    if isinstance(stmt, Break):
        return TpyBreak(loc=loc)
    if isinstance(stmt, Continue):
        return TpyContinue(loc=loc)
    if isinstance(stmt, Raise):
        value = None
        if stmt.value is not None:
            value = _lower_expr(stmt.value, name_to_origin,
                                plugin_name, fm, diags)
            if value is None:
                return None
        out = TpyRaise(raise_expr=value)
        out.loc = loc
        return out
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


def _lower_enum(
    en: Enum,
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
) -> TpyEnum | None:
    """Lower an IR `Enum` to a `TpyEnum`.

    M7 emits Pascal-style auto-numbered enums (members get values 0, 1,
    2, ...). Explicit member values from `EnumValue.value` are accepted
    only when they are `IntLit`; richer expressions wait on a later
    milestone since TpyEnum's members slot is `(name, int, loc)` --
    pre-resolved at parser/lowering time.
    """
    members: list = []
    auto_index = 0
    for v in en.values:
        if v.value is None:
            int_value = auto_index
        elif isinstance(v.value, IntLit):
            int_value = v.value.value
        else:
            diags.append(_ir_invalid(
                plugin_name, fm,
                "EnumValue.value must be IntLit or None in M7 "
                "(auto-numbering / explicit int literal)",
            ))
            return None
        members.append((v.name, int_value, _to_source_loc(v.loc)))
        auto_index = int_value + 1
    return TpyEnum(
        name=en.name,
        members=members,
        is_int_enum=False,
        loc=_to_source_loc(en.loc),
    )


def _is_known_record_name(name: str, fm: FrontendModule) -> bool:
    """True iff `name` matches the name of a record declared in this
    FrontendModule. Used during expression lowering to recognise
    constructor calls (`Point()`) so the resulting TpyCall carries a
    `call_type` -- the same shape the Python parser emits for
    type-instantiation calls.
    """
    return any(rec.name == name for rec in fm.records)


@dataclass
class _NativeSpec:
    """Resolved `tpy.native` decorator. `symbol` is the C++ name (None for
    a bare `@native`; sema normalizes to the decl name). Mirrors the
    parser's `@native` handling (`parse/parser.py`) -- both feed the same
    `TpyFunction`/`TpyRecord` native fields and the same codegen path."""
    symbol: str | None = None
    function: bool = False


# Sentinel distinguishing "not a literal node" from a literal whose value
# is None (a `NoneLit`).
_NO_LITERAL = object()


def _decorator_literal(expr: Expr) -> object:
    """Python value of a literal IR `Expr`, or `_NO_LITERAL` when `expr`
    is not a literal node. Decorator args/kwargs carry declarative config
    only -- arbitrary plugin data rides `FrontendModule.macro_data`."""
    if isinstance(expr, NoneLit):
        return None
    if isinstance(expr, (StrLit, BoolLit, IntLit, FloatLit)):
        return expr.value
    return _NO_LITERAL


def _native_spec(
    dec: Decorator, plugin_name: str, fm: FrontendModule,
    diags: list[FrontendDiagnostic],
) -> _NativeSpec | None:
    """Read a `tpy.native` Decorator into a `_NativeSpec`: an optional
    positional C++ symbol string and a `function: bool` kwarg."""
    symbol: str | None = None
    if dec.args:
        if len(dec.args) != 1:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"@native takes at most one positional argument (the C++ "
                f"symbol), got {len(dec.args)}"))
            return None
        val = _decorator_literal(dec.args[0])
        if not isinstance(val, str):
            diags.append(_ir_invalid(
                plugin_name, fm, "@native symbol must be a string literal"))
            return None
        symbol = val
    function = False
    for key, vexpr in dec.kwargs:
        if key == "function":
            val = _decorator_literal(vexpr)
            if not isinstance(val, bool):
                diags.append(_ir_invalid(
                    plugin_name, fm,
                    "@native(function=...) must be a bool literal"))
                return None
            function = val
        else:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"@native: unsupported keyword {key!r} (v1 supports the "
                f"positional symbol and `function`)"))
            return None
    return _NativeSpec(symbol=symbol, function=function)


def _route_decorators(
    decorators: tuple[Decorator, ...], target_kind: str,
    registry: dict[str, DecoratorEntry], plugin_name: str,
    fm: FrontendModule, diags: list[FrontendDiagnostic],
) -> tuple[list[tuple[str, dict]], _NativeSpec | None] | None:
    """Resolve a declaration's IR `Decorator`s via the registry into
    `(pending_macros, native_spec)`. Returns None on a routing error
    (diagnostic already appended). Unknown names are rejected -- no
    silent passthrough (FRONTEND_PLUGIN_DESIGN.md)."""
    pending_macros: list[tuple[str, dict]] = []
    native: _NativeSpec | None = None
    for dec in decorators:
        if not isinstance(dec, Decorator):
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"decorator must be a frontend_ir Decorator node, got {dec!r}"))
            return None
        entry = registry.get(dec.name)
        if entry is None:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"unknown decorator {dec.name!r}: not in the decorator "
                f"registry (a plugin declares custom decorators via its "
                f"decorator_manifest)"))
            return None
        if target_kind not in entry.target_kinds:
            diags.append(_ir_invalid(
                plugin_name, fm,
                f"decorator {dec.name!r} is not valid on a {target_kind} "
                f"(allowed: {', '.join(entry.target_kinds)})"))
            return None
        if entry.route is DecoratorRoute.BUILTIN_LOWERING:
            if dec.name != "tpy.native":
                diags.append(_ir_invalid(
                    plugin_name, fm,
                    f"builtin decorator {dec.name!r} has no IR lowering yet"))
                return None
            if native is not None:
                diags.append(_ir_invalid(
                    plugin_name, fm, "duplicate @native decorator"))
                return None
            native = _native_spec(dec, plugin_name, fm, diags)
            if native is None:
                return None
        else:  # MACRO -> pending_macros (literal kwargs only)
            if dec.args:
                diags.append(_ir_invalid(
                    plugin_name, fm,
                    f"macro decorator {dec.name!r}: positional args are not "
                    f"supported (use keyword args; non-literal data rides "
                    f"macro_data)"))
                return None
            kwargs: dict = {}
            for key, vexpr in dec.kwargs:
                val = _decorator_literal(vexpr)
                if val is _NO_LITERAL:
                    diags.append(_ir_invalid(
                        plugin_name, fm,
                        f"macro decorator {dec.name!r}: keyword {key!r} must "
                        f"be a literal (non-literal data rides macro_data)"))
                    return None
                kwargs[key] = val
            pending_macros.append((dec.name, kwargs))
    return pending_macros, native


def _lower_record(
    rec: Record,
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
    qname_prefix: str,
    registry: dict[str, DecoratorEntry],
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
    if rec.nested_records or rec.nested_enums:
        diags.append(_ir_invalid(
            plugin_name, fm,
            "nested records / enums are reserved for later milestones",
        ))
        return None, None
    methods: list[TpyFunction] = []
    for m in rec.methods:
        lowered = _lower_function(
            m, {}, plugin_name, fm, diags, is_method=True, registry=registry)
        if lowered is None:
            return None, None
        lowered.is_method = True
        # Strip the leading `self` parameter from `params`: TPy's
        # method-arg accounting (sema's `init_params`, arity checks,
        # etc.) excludes self. The IR carries it because plugins
        # build method bodies that reference `self` by name; for the
        # TpyFunction the receiver is implicit.
        if lowered.params and lowered.params[0][0] == "self":
            lowered.params = lowered.params[1:]
        if m.is_property_getter:
            lowered.is_property_getter = True
        if m.is_property_setter:
            lowered.is_property_setter = True
            if m.property_name is not None:
                lowered.property_name = m.property_name
        methods.append(lowered)
    # Thread the IR base through as a TpyTypeRef so the post-parse resolve_refs
    # + registration ancestor-walk inherit the base's fields/methods, exactly
    # as a parser-produced `class D(B)` does. Single inheritance only.
    bases: list[TpyTypeRef] = []
    if rec.base is not None:
        base_ref = _lower_type(rec.base, plugin_name, fm, diags)
        if base_ref is None:
            return None, None
        bases.append(base_ref)
    routed = _route_decorators(
        rec.decorators, "record", registry, plugin_name, fm, diags)
    if routed is None:
        return None, None
    pending_macros, native = routed
    rec_linkage = RecordLinkage.DEFAULT
    rec_native_name: str | None = None
    if native is not None:
        if native.function:
            diags.append(_ir_invalid(
                plugin_name, fm,
                "@native(function=...) is not valid on a record"))
            return None, None
        rec_linkage = RecordLinkage.NATIVE
        rec_native_name = native.symbol
    tpy_rec = TpyRecord(
        name=rec.name,
        fields=field_infos,
        methods=methods,
        bases=bases,
        linkage=rec_linkage,
        native_name=rec_native_name,
        pending_macros=pending_macros,
    )
    # RecordInfo's `module` is the public module qname; entry-point
    # modules use `__main__` (matches sema's `ctx.module_name` rename
    # for the entry point), other modules use their dotted name.
    # Builtin-type-key stays unset because plugin records aren't
    # `@builtin_type` decorated.
    record_info = RecordInfo(
        name=rec.name,
        fields=field_infos,
        has_init=False,
        module=qname_prefix,
    )
    return tpy_rec, record_info


def _lower_function(
    fn: Function,
    name_to_origin: dict[str, tuple[str, str]],
    plugin_name: str,
    fm: FrontendModule,
    diags: list[FrontendDiagnostic],
    is_method: bool = False,
    *,
    registry: dict[str, DecoratorEntry],
) -> TpyFunction | None:
    """Lower a frontend `Function` to a `TpyFunction`. Plugins emit
    functions whose params already carry resolved types (NamedType /
    PointerType); the body uses the M4 expression / statement set.
    """
    params: list = []
    for p in fn.params:
        # An un-annotated param (`type is None`) is permitted for
        # `self` on record methods, mirroring the no-annotation form
        # in ordinary TPy source. The resolver-attached pre-pass
        # treats a None-typed first-param of a method as `Self`.
        if p.type is None:
            t = None
        else:
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
    routed = _route_decorators(
        fn.decorators, "function", registry, plugin_name, fm, diags)
    if routed is None:
        return None
    pending_macros, native = routed
    # Function macros run only on module-level free functions (sema's
    # pass-5.5 doesn't scan methods), so a macro decorator on a method
    # would silently never fire -- reject it, as the parser does. A
    # BUILTIN_LOWERING `@native`, by contrast, IS valid on a method (it
    # becomes method linkage, not a macro).
    if is_method and pending_macros:
        diags.append(_ir_invalid(
            plugin_name, fm,
            f"method {fn.name!r}: function-macro decorators are not "
            f"supported on methods"))
        return None
    linkage = FunctionLinkage.DEFAULT
    native_name: str | None = None
    native_function = False
    is_stub = False
    if native is not None:
        # @native lowers to the same fields the parser sets for source
        # `@native`: NATIVE linkage + the C++ symbol, and is_stub so
        # codegen emits a declaration only (the body lives in C++).
        linkage = FunctionLinkage.NATIVE
        native_name = native.symbol
        native_function = native.function
        is_stub = True
    return TpyFunction(
        name=fn.name,
        params=params,
        return_type=return_type,
        body=body,
        pending_macros=pending_macros,
        linkage=linkage,
        native_name=native_name,
        native_function=native_function,
        is_stub=is_stub,
    )


def _lower_pattern(p, name_to_origin, plugin_name, fm, diags):
    loc = _to_source_loc(p.loc) if getattr(p, "loc", None) else None
    if isinstance(p, MatchWildcard):
        return TpyWildcardPattern(loc=loc)
    if isinstance(p, MatchValue):
        # Literal patterns (int / str / bool) route through TpyLiteralPattern.
        # Named-constant patterns (`Color.Red`) keep their expression
        # form via TpyValuePattern, which sema resolves to the enum
        # member value at match-analysis time.
        v = p.value
        if isinstance(v, IntLit):
            return TpyLiteralPattern(value=v.value, loc=loc)
        if isinstance(v, StrLit):
            return TpyLiteralPattern(value=v.value, loc=loc)
        if isinstance(v, BoolLit):
            return TpyLiteralPattern(value=v.value, loc=loc)
        if isinstance(v, Attr):
            lowered = _lower_expr(v, {}, plugin_name, fm, diags)
            if lowered is None:
                return None
            return TpyValuePattern(expr=lowered, loc=loc)
        diags.append(_ir_invalid(
            plugin_name, fm,
            f"MatchValue payload must be a literal or attribute "
            f"reference, got {type(v).__name__}",
        ))
        return None
    if isinstance(p, MatchClass):
        cls_ref = TpyName(name=p.class_name)
        cls_ref.loc = loc
        class_pat = TpyClassPattern(
            cls=cls_ref, positional=[], keywords=[], loc=loc,
        )
        if p.bind is not None:
            return TpyAsPattern(pattern=class_pat, name=p.bind, loc=loc)
        return class_pat
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
    if isinstance(expr, FloatLit):
        out = TpyFloatLiteral(value=expr.value)
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
    if isinstance(expr, SetLit):
        lowered_elems: list = []
        for elem in expr.elements:
            le = _lower_expr(elem, name_to_origin, plugin_name, fm, diags)
            if le is None:
                return None
            lowered_elems.append(le)
        out = TpySetLiteral(elements=lowered_elems)
        out.loc = loc
        return out
    if isinstance(expr, ListLit):
        lowered_elems = []
        for elem in expr.elements:
            le = _lower_expr(elem, name_to_origin, plugin_name, fm, diags)
            if le is None:
                return None
            lowered_elems.append(le)
        out = TpyArrayLiteral(elements=lowered_elems)
        out.loc = loc
        return out
    if isinstance(expr, NoneLit):
        out = TpyNoneLiteral()
        out.loc = loc
        return out
    if isinstance(expr, Conditional):
        cond = _lower_expr(expr.cond, name_to_origin, plugin_name, fm, diags)
        then = _lower_expr(expr.then, name_to_origin, plugin_name, fm, diags)
        else_ = _lower_expr(expr.else_, name_to_origin, plugin_name, fm, diags)
        if cond is None or then is None or else_ is None:
            return None
        out = TpyIfExpr(condition=cond, then_expr=then, else_expr=else_)
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
        # Method-call shape: callee is `obj.method` -- emit TpyMethodCall
        # directly so sema's method-dispatch path fires. TpyCall with a
        # field-access function would otherwise have to be re-routed by
        # sema, and not every method-resolution path handles that.
        if isinstance(expr.callee, Attr):
            obj = _lower_expr(expr.callee.target, name_to_origin,
                              plugin_name, fm, diags)
            if obj is None:
                return None
            args: list = []
            for a in expr.args:
                la = _lower_expr(a, name_to_origin, plugin_name, fm, diags)
                if la is None:
                    return None
                args.append(la)
            kwargs: dict = {}
            for kw_name, kw_value in expr.kwargs:
                la = _lower_expr(kw_value, name_to_origin, plugin_name, fm,
                                 diags)
                if la is None:
                    return None
                kwargs[kw_name] = la
            mcall = TpyMethodCall(
                obj=obj, method=expr.callee.ident, args=args, kwargs=kwargs,
            )
            mcall.loc = loc
            return mcall
        callee = _lower_expr(expr.callee, name_to_origin, plugin_name, fm, diags)
        if callee is None:
            return None
        args = []
        for a in expr.args:
            la = _lower_expr(a, name_to_origin, plugin_name, fm, diags)
            if la is None:
                return None
            args.append(la)
        kwargs: dict = {}
        for kw_name, kw_value in expr.kwargs:
            la = _lower_expr(kw_value, name_to_origin, plugin_name, fm, diags)
            if la is None:
                return None
            kwargs[kw_name] = la
        out = TpyCall(func=callee, args=args, kwargs=kwargs)
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
    if isinstance(t, UnionType):
        members: list = []
        for m in t.members:
            lm = _lower_type(m, plugin_name, fm, diags)
            if lm is None:
                return None
            members.append(lm)
        return TpyUnionRef(members=tuple(members),
                           loc=_to_source_loc(t.loc))
    if isinstance(t, CallableType):
        params_lowered: list = []
        for p in t.params:
            lp = _lower_type(p, plugin_name, fm, diags)
            if lp is None:
                return None
            params_lowered.append(lp)
        if t.return_type is None:
            # `procedure(...)` -- the void-returning form. TPy
            # spells this as `Callable[[...], None]`; the resolver
            # accepts `NoneType` (`TpyTypeRef("None")`) as the
            # return type.
            ret = TpyTypeRef(name="None", args=(),
                             loc=_to_source_loc(t.loc))
        else:
            ret = _lower_type(t.return_type, plugin_name, fm, diags)
            if ret is None:
                return None
        return TpyCallableRef(
            kind="Callable", params=tuple(params_lowered),
            return_type=ret, loc=_to_source_loc(t.loc),
        )
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
