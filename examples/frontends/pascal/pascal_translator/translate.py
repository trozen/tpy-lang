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
    Call,
    ExprStmt,
    FrontendDirectives,
    FrontendModule,
    FromImport,
    ImportName,
    IntLit,
    Loc as IRLoc,
    Name,
    NamedType,
    StrLit,
    UnaryOp,
    UnaryOpKind,
    VarDecl,
)

from . import ast as pa


# Pascal type spellings -> TPy builtin names. Single source of truth so
# both var decls and writeln dispatch agree on the mapping.
_TYPE_MAP: dict[str, str] = {
    "integer": "Int32",
}

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
}

_UNARY_OP: dict[str, UnaryOpKind] = {
    "+": UnaryOpKind.POS,
    "-": UnaryOpKind.NEG,
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
    diagnostics.append(_diag(
        f"unsupported statement {type(stmt).__name__}",
        getattr(stmt, "loc", _zero_loc()),
    ))
    return None


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
    if isinstance(expr, pa.Ident):
        return Name(ident=expr.name, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.BinOp):
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
