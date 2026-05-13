"""Pascal AST -> Frontend IR translator.

Walks the Pascal AST and emits a `FrontendModule`. Hoists Pascal's
`var` section into per-name IR `VarDecl`s preceding the program body,
maps `integer` to `NamedType("Int32")`, and routes `writeln(...)` to
the runtime overload appropriate for the argument's static type.

Imports are emitted in two buckets:
  - The Pascal-runtime entry per call (e.g. `pascal.runtime.io.writeln`
    for `writeln(StrView)`, `pascal.runtime.io.writeln_int` for
    `writeln(Int32)`).
  - `from tpy import Int32` whenever the IR needs to name `Int32`.
"""

from __future__ import annotations

from pathlib import Path

from tpyc.diagnostics import Diagnostic, DiagnosticLevel
from tpyc.frontend_ir import (
    Assign,
    BinOp,
    BinOpKind,
    BoolLit,
    Call,
    CmpOpKind,
    Compare,
    ExprStmt,
    ForRange,
    FrontendDirectives,
    FrontendModule,
    FromImport,
    If,
    ImportName,
    IntLit,
    Loc as IRLoc,
    Match,
    MatchCase,
    MatchValue,
    MatchWildcard,
    Name,
    NamedType,
    RangeDir,
    RepeatUntil,
    StrLit,
    UnaryOp,
    UnaryOpKind,
    VarDecl,
    While,
)

from . import ast as pa


# Pascal type spellings -> TPy builtin names. Single source of truth so
# both var decls and writeln dispatch agree on the mapping. Booleans
# don't need a `from tpy import bool` entry because `bool` is in TPy's
# builtins namespace; the translator records the mapping so the static
# type env still tracks bool variables for writeln dispatch.
_TYPE_MAP: dict[str, str] = {
    "integer": "Int32",
    "boolean": "bool",
}

# Types that should NOT trigger an automatic `from tpy import X` -- the
# entries that live in TPy's builtins module are visible without import.
_BUILTIN_TYPES_NO_IMPORT: frozenset[str] = frozenset({"bool"})

# Pascal infix operators -> IR BinOpKind. Pascal `/` is the integer-or-
# real "true" division (real result for integers); `div` is integer
# division. M2 emits Pascal's `div` as IR `FLOOR_DIV` (TPy's `//`)
# because Pascal integer operands stay integers; `TRUE_DIV` is reserved
# for later when float operands enter the picture.
_BIN_OP: dict[str, BinOpKind] = {
    "+": BinOpKind.ADD,
    "-": BinOpKind.SUB,
    "*": BinOpKind.MUL,
    "div": BinOpKind.FLOOR_DIV,
    "/": BinOpKind.TRUE_DIV,
    "mod": BinOpKind.MOD,
    # Pascal word-spelled logical operators. Bitwise/logical
    # distinction in Pascal depends on operand types; M3 treats `and`
    # and `or` as logical because operands are boolean expressions
    # produced by comparisons.
    "and": BinOpKind.LOGICAL_AND,
    "or": BinOpKind.LOGICAL_OR,
    "xor": BinOpKind.BIT_XOR,
}

_UNARY_OP: dict[str, UnaryOpKind] = {
    "+": UnaryOpKind.POS,
    "-": UnaryOpKind.NEG,
    "not": UnaryOpKind.NOT,
}

# Pascal comparison spellings -> IR CmpOpKind. `=` -> EQ, `<>` -> NE
# follow Pascal's syntax; the rest match Python's spelling.
_CMP_OP: dict[str, CmpOpKind] = {
    "=": CmpOpKind.EQ,
    "<>": CmpOpKind.NE,
    "<": CmpOpKind.LT,
    "<=": CmpOpKind.LE,
    ">": CmpOpKind.GT,
    ">=": CmpOpKind.GE,
}


def translate(program: pa.Program,
              module_name: str) -> tuple[FrontendModule, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    needed_imports: dict[str, set[str]] = {}
    top_level_stmts: list = []
    # Static type environment built as we walk vars / assignments. Pascal
    # is statically typed and M2 has a single type (Int32), but the env
    # is the right place to grow as more types arrive.
    type_env: dict[str, str] = {}

    def add_import(module: str, name: str) -> None:
        needed_imports.setdefault(module, set()).add(name)

    def lower_type(type_name: str, loc: pa.Loc) -> NamedType | None:
        tpy_name = _TYPE_MAP.get(type_name)
        if tpy_name is None:
            diagnostics.append(_diag(
                f"unsupported type {type_name!r}", loc))
            return None
        if tpy_name not in _BUILTIN_TYPES_NO_IMPORT:
            add_import("tpy", tpy_name)
        return NamedType(name=tpy_name, args=(), loc=_to_ir_loc(loc))

    # ----- var section -> per-name VarDecl IR nodes ---------------------
    if program.var_block is not None:
        for decl in program.var_block.decls:
            ir_type = lower_type(decl.type_name, decl.loc)
            for name in decl.names:
                type_env[name] = decl.type_name
                top_level_stmts.append(VarDecl(
                    name=name, type=ir_type, init=None, mutable=True,
                    loc=_to_ir_loc(decl.loc),
                ))

    # ----- program body --------------------------------------------------
    for stmt in program.block.statements:
        lowered = _lower_stmt(
            stmt, type_env, add_import, diagnostics,
        )
        if lowered is not None:
            top_level_stmts.append(lowered)

    imports = tuple(
        FromImport(
            module=mod,
            names=tuple(sorted(
                (ImportName(original=n, local=n) for n in names),
                key=lambda im: im.local,
            )),
        )
        for mod, names in sorted(needed_imports.items())
    )

    fm = FrontendModule(
        qname=module_name,
        source_language="pascal",
        source_lines=program.source_lines,
        imports=imports,
        top_level_stmts=tuple(top_level_stmts),
        directives=FrontendDirectives(),
    )
    return fm, diagnostics


def _lower_stmt(stmt, type_env, add_import, diagnostics):
    if isinstance(stmt, pa.AssignStmt):
        target = Name(ident=stmt.target.name, loc=_to_ir_loc(stmt.target.loc))
        value = _lower_expr(stmt.value, type_env, add_import, diagnostics)
        if value is None:
            return None
        return Assign(targets=(target,), value=value,
                      loc=_to_ir_loc(stmt.loc))
    if isinstance(stmt, pa.CallStmt):
        return _lower_call_stmt(stmt, type_env, add_import, diagnostics)
    if isinstance(stmt, pa.CompoundStmt):
        # A compound statement in a control-flow position isn't its own
        # IR node -- it just contributes its inner statements to the
        # enclosing then/else/body block. The caller hoists by treating
        # a CompoundStmt's children as the block payload.
        return _lower_compound_as_marker(stmt, type_env, add_import, diagnostics)
    if isinstance(stmt, pa.IfStmt):
        cond = _lower_expr(stmt.cond, type_env, add_import, diagnostics)
        if cond is None:
            return None
        then_body = _lower_branch(stmt.then_branch, type_env,
                                  add_import, diagnostics)
        else_body = (_lower_branch(stmt.else_branch, type_env,
                                   add_import, diagnostics)
                     if stmt.else_branch is not None else ())
        return If(cond=cond, then_body=then_body, else_body=else_body,
                  loc=_to_ir_loc(stmt.loc))
    if isinstance(stmt, pa.WhileStmt):
        cond = _lower_expr(stmt.cond, type_env, add_import, diagnostics)
        if cond is None:
            return None
        body = _lower_branch(stmt.body, type_env, add_import, diagnostics)
        return While(cond=cond, body=body, loc=_to_ir_loc(stmt.loc))
    if isinstance(stmt, pa.ForStmt):
        start = _lower_expr(stmt.start, type_env, add_import, diagnostics)
        end = _lower_expr(stmt.end, type_env, add_import, diagnostics)
        if start is None or end is None:
            return None
        # The loop variable is treated as integer in M3 (Pascal `for`
        # only iterates ordinals; integer covers the M3 surface).
        type_env[stmt.var] = "integer"
        body = _lower_branch(stmt.body, type_env, add_import, diagnostics)
        direction = (RangeDir.ASC if stmt.direction == "to"
                     else RangeDir.DESC)
        return ForRange(
            var=stmt.var, start=start, end=end,
            direction=direction, inclusive=True, body=body,
            loc=_to_ir_loc(stmt.loc),
        )
    if isinstance(stmt, pa.RepeatStmt):
        cond = _lower_expr(stmt.cond, type_env, add_import, diagnostics)
        if cond is None:
            return None
        body = _lower_block_stmts(stmt.statements, type_env,
                                  add_import, diagnostics)
        return RepeatUntil(body=body, cond=cond, loc=_to_ir_loc(stmt.loc))
    if isinstance(stmt, pa.CaseStmt):
        return _lower_case_stmt(stmt, type_env, add_import, diagnostics)
    diagnostics.append(_diag(
        f"unsupported statement {type(stmt).__name__}",
        getattr(stmt, "loc", _zero_loc()),
    ))
    return None


def _lower_compound_as_marker(stmt: pa.CompoundStmt, type_env,
                              add_import, diagnostics):
    """A bare CompoundStmt at the program-body level is unusual but
    legal; lowering produces a no-op marker (IR has no compound-stmt
    node). The compound's inner statements are returned as a flat list
    via `_lower_branch`/`_lower_block_stmts` when used as a control-flow
    branch; if encountered as a top-level program statement, we emit
    each child directly via the caller -- but `_lower_stmt` returns a
    single IR node, so this path inlines the children into the outer
    block via a marker.
    """
    # Inline the compound's contents by returning the first lowered
    # statement and pushing the rest onto a stash. M3's only realistic
    # paths use compounds as branches, where `_lower_branch` handles
    # them properly; this fallback exists for completeness.
    if not stmt.statements:
        return None
    if len(stmt.statements) == 1:
        return _lower_stmt(stmt.statements[0], type_env,
                           add_import, diagnostics)
    diagnostics.append(_diag(
        "compound statement at program-body level with multiple "
        "inner statements is not supported (use them directly in the "
        "outer `begin ... end`)",
        stmt.loc,
    ))
    return None


def _lower_branch(stmt, type_env, add_import, diagnostics) -> tuple:
    """Lower a single statement that appears as a control-flow branch
    (e.g. then/else body, while body, for body) into a flat tuple of
    IR statements. Pascal's grammar makes branches single statements;
    `begin ... end` is the multi-statement form. This helper handles
    both: a compound is unwrapped, anything else is wrapped in a
    one-element tuple.
    """
    if isinstance(stmt, pa.CompoundStmt):
        return tuple(_lower_block_stmts(stmt.statements, type_env,
                                        add_import, diagnostics))
    lowered = _lower_stmt(stmt, type_env, add_import, diagnostics)
    return (lowered,) if lowered is not None else ()


def _lower_block_stmts(stmts, type_env, add_import, diagnostics) -> tuple:
    """Lower a flat list of Pascal AST statements into IR statements."""
    out: list = []
    for s in stmts:
        lowered = _lower_stmt(s, type_env, add_import, diagnostics)
        if lowered is not None:
            out.append(lowered)
    return tuple(out)


def _lower_case_stmt(stmt: pa.CaseStmt, type_env, add_import, diagnostics):
    subject = _lower_expr(stmt.subject, type_env, add_import, diagnostics)
    if subject is None:
        return None
    cases: list = []
    for arm in stmt.arms:
        body = _lower_branch(arm.body, type_env, add_import, diagnostics)
        for v in arm.values:
            ir_v = _lower_expr(v, type_env, add_import, diagnostics)
            if ir_v is None:
                return None
            cases.append(MatchCase(
                pattern=MatchValue(value=ir_v, loc=_to_ir_loc(arm.loc)),
                body=body, loc=_to_ir_loc(arm.loc),
            ))
    if stmt.else_branch is not None:
        else_body = _lower_branch(stmt.else_branch, type_env,
                                  add_import, diagnostics)
        cases.append(MatchCase(
            pattern=MatchWildcard(loc=_to_ir_loc(stmt.loc)),
            body=else_body, loc=_to_ir_loc(stmt.loc),
        ))
    return Match(subject=subject, cases=tuple(cases),
                 loc=_to_ir_loc(stmt.loc))


def _lower_call_stmt(stmt: pa.CallStmt, type_env, add_import, diagnostics):
    callee_name = stmt.callee.name
    if callee_name not in ("write", "writeln"):
        diagnostics.append(_diag(
            f"unknown procedure {callee_name!r}", stmt.callee.loc))
        return None
    if len(stmt.args) != 1:
        diagnostics.append(_diag(
            f"{callee_name!r} takes exactly one argument in M2",
            stmt.loc,
        ))
        return None
    arg = stmt.args[0]
    ir_arg = _lower_expr(arg, type_env, add_import, diagnostics)
    if ir_arg is None:
        return None
    # Pick the runtime overload based on the static type of the arg.
    # M2 has two: writeln(StrView) and writeln_int(Int32). Future
    # milestones add float / bool / char overloads.
    arg_type = _static_type_of(arg, type_env)
    if arg_type == "integer":
        runtime_name = f"{callee_name}_int"
    else:
        # StrView path (string literal or future string variable).
        runtime_name = callee_name
    add_import("pascal.runtime.io", runtime_name)
    ir_call = Call(
        callee=Name(ident=runtime_name, loc=_to_ir_loc(stmt.callee.loc)),
        args=(ir_arg,),
        loc=_to_ir_loc(stmt.loc),
    )
    return ExprStmt(value=ir_call, loc=_to_ir_loc(stmt.loc))


def _lower_expr(expr, type_env, add_import, diagnostics):
    if isinstance(expr, pa.IntLit):
        return IntLit(value=expr.value, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.StrLit):
        return StrLit(value=expr.value, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.BoolLit):
        return BoolLit(value=expr.value, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.Ident):
        return Name(ident=expr.name, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.BinOp):
        # Comparison ops emit a Compare IR node (per the design doc).
        # Everything else stays a BinOp.
        cmp_op = _CMP_OP.get(expr.op)
        if cmp_op is not None:
            lhs = _lower_expr(expr.lhs, type_env, add_import, diagnostics)
            rhs = _lower_expr(expr.rhs, type_env, add_import, diagnostics)
            if lhs is None or rhs is None:
                return None
            return Compare(
                lhs=lhs, ops=(cmp_op,), comparators=(rhs,),
                loc=_to_ir_loc(expr.loc),
            )
        op = _BIN_OP.get(expr.op)
        if op is None:
            diagnostics.append(_diag(
                f"unsupported binary operator {expr.op!r}", expr.loc))
            return None
        lhs = _lower_expr(expr.lhs, type_env, add_import, diagnostics)
        rhs = _lower_expr(expr.rhs, type_env, add_import, diagnostics)
        if lhs is None or rhs is None:
            return None
        return BinOp(op=op, lhs=lhs, rhs=rhs, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.UnaryOp):
        op = _UNARY_OP.get(expr.op)
        if op is None:
            diagnostics.append(_diag(
                f"unsupported unary operator {expr.op!r}", expr.loc))
            return None
        operand = _lower_expr(expr.operand, type_env, add_import, diagnostics)
        if operand is None:
            return None
        return UnaryOp(op=op, operand=operand, loc=_to_ir_loc(expr.loc))
    diagnostics.append(_diag(
        f"unsupported expression {type(expr).__name__}",
        getattr(expr, "loc", _zero_loc()),
    ))
    return None


def _static_type_of(expr, type_env: dict[str, str]) -> str | None:
    """Best-effort static type for the writeln-overload dispatch.

    M2 only needs to distinguish integers from strings, so the analysis
    is intentionally shallow. Pascal's real semantics flow types
    through arithmetic; for now we treat any arithmetic node + any
    integer-typed variable as `integer`.
    """
    if isinstance(expr, pa.IntLit):
        return "integer"
    if isinstance(expr, pa.StrLit):
        return "string"
    if isinstance(expr, pa.Ident):
        return type_env.get(expr.name)
    if isinstance(expr, (pa.BinOp, pa.UnaryOp)):
        return "integer"
    return None


def _to_ir_loc(loc: pa.Loc) -> IRLoc:
    return IRLoc(
        file=loc.file,
        line=loc.line, col=loc.col,
        end_line=loc.end_line, end_col=loc.end_col,
        source_language="pascal",
    )


def _zero_loc() -> pa.Loc:
    return pa.Loc(file=Path("<unknown>"),
                  line=0, col=0, end_line=0, end_col=0)


def _diag(message: str, loc: pa.Loc) -> Diagnostic:
    from tpyc.parse import SourceLocation
    return Diagnostic(
        level=DiagnosticLevel.ERROR,
        message=message,
        loc=SourceLocation(line=loc.line, column=max(0, loc.col - 1),
                           file=str(loc.file)),
    )
