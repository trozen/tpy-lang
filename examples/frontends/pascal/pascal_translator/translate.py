"""Pascal AST -> Frontend IR translator (M1 subset).

For hello-world, the translator emits a single `FrontendModule` whose
`top_level_stmts` contain one `ExprStmt(Call(Name("writeln"), (StrLit,)))`
per Pascal `writeln(...)` call, plus a `FromImport` of `writeln` from the
Pascal runtime package.
"""

from __future__ import annotations

from pathlib import Path

from tpyc.diagnostics import Diagnostic, DiagnosticLevel
from tpyc.frontend_ir import (
    Call,
    ExprStmt,
    FrontendDirectives,
    FrontendModule,
    FromImport,
    ImportName,
    Loc as IRLoc,
    Name,
    StrLit,
)

from . import ast as pa


# M1: only the `writeln` builtin is supported. Each entry: pascal name ->
# (runtime module, exported name). Additional builtins land milestone-
# by-milestone.
_BUILTIN_ROUTES: dict[str, tuple[str, str]] = {
    "writeln": ("pascal.runtime.io", "writeln"),
}


def translate(program: pa.Program,
              module_name: str) -> tuple[FrontendModule, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    needed_imports: dict[str, set[str]] = {}
    top_level_stmts: list = []

    for stmt in program.block.statements:
        if not isinstance(stmt, pa.CallStmt):
            diagnostics.append(_diag(
                "unsupported statement", stmt.loc))
            continue
        route = _BUILTIN_ROUTES.get(stmt.callee.name)
        if route is None:
            diagnostics.append(_diag(
                f"unknown identifier {stmt.callee.name!r}", stmt.callee.loc))
            continue
        mod, exported = route
        needed_imports.setdefault(mod, set()).add(exported)

        # Convert args (M1: StrLit only).
        ir_args: list = []
        for a in stmt.args:
            if isinstance(a, pa.StrLit):
                ir_args.append(StrLit(value=a.value, loc=_to_ir_loc(a.loc)))
            else:
                diagnostics.append(_diag(
                    f"unsupported argument kind {type(a).__name__}", a.loc))
        ir_call = Call(
            callee=Name(ident=exported, loc=_to_ir_loc(stmt.callee.loc)),
            args=tuple(ir_args),
            loc=_to_ir_loc(stmt.loc),
        )
        top_level_stmts.append(ExprStmt(value=ir_call, loc=_to_ir_loc(stmt.loc)))

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


def _to_ir_loc(loc: pa.Loc) -> IRLoc:
    return IRLoc(
        file=loc.file,
        line=loc.line, col=loc.col,
        end_line=loc.end_line, end_col=loc.end_col,
        source_language="pascal",
    )


def _diag(message: str, loc: pa.Loc) -> Diagnostic:
    from tpyc.parse import SourceLocation
    return Diagnostic(
        level=DiagnosticLevel.ERROR,
        message=message,
        loc=SourceLocation(line=loc.line, column=max(0, loc.col - 1),
                           file=str(loc.file)),
    )
