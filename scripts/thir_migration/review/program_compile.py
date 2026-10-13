"""One compile of a generated program, for the verdict-table sweeps here.

A sweep hands `compile_program` its program and a function of
`(compiler, run, *args)` -- `run()` analyzes the program and returns its
modules -- and gets back what the function returns. The function runs in
this process by default, or wherever an installed runner sends it: the test
suite installs one that forks it from a compile server holding the analyzed
stdlib (tests/fork_compile.py). So it reads everything it needs off the
compiler where the compiler is and returns plain data only (str / int /
None / list / tuple / dict), and a sweep's table cannot depend on where its
programs were compiled.

`program_facts` is that function for every sweep; each sweep keeps its own
precedence and line-to-cell mapping over the facts. (`arg_family_sweep
--routes`, script-only, still builds its own Compiler.)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[3]

# (entry, fn, args, default_int, lib_dirs) -> fn's result, or None to compile
# in this process.
_runner: Callable | None = None


def use(runner: Callable | None) -> None:
    """Send every later `compile_program` through `runner` (None: none)."""
    global _runner
    _runner = runner


def compile_program(entry: Path, fn: Callable, *args, repo: Path = REPO):
    """`fn(compiler, run, *args)` over the program `entry`, compiled with
    int32 literals against `repo`'s stdlib."""
    lib_dirs = [repo / "lib" / "tpy"]
    if _runner is not None:
        out = _runner(entry, fn, args, "int32", lib_dirs)
        if out is not None:
            return out
    # Imported here, after `repo` is on sys.path, so a script's `--repo`
    # compiles with that checkout's compiler and not an installed one.
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from tpyc.compiler import Compiler
    compiler = Compiler(entry, default_int="int32", lib_dirs=lib_dirs)
    return fn(compiler, compiler.compile, *args)


def _line(obj) -> int:
    return getattr(getattr(obj, "loc", None), "line", 0) or 0


def program_facts(compiler, run: Callable, codegen_dir: Path | None = None) -> dict:
    """What the sweeps read off one compile, as plain data:

    - `raised`: None, or what `run()` raised as {"sema": a SemanticError,
      "message", "line"}; nothing else is filled in then.
    - `diagnostics`: (level, line, message) in `format_diagnostics` order;
      line 0 for none.
    - `codegen_error`: what the THIR survey (or, with `codegen_dir`, the
      emission) raised, or None. Neither runs when a diagnostic is an error.
    - `traceback`: the formatted traceback of `raised` or `codegen_error`.
    - `top_reject`: the entry module's own reject reason, or None.
    - `bodies`: every entry-module callable and constructor in
      `iter_module_callables` / `iter_module_constructors` order, as
      {"kind": "callable" | "constructor", "name" (a constructor's record),
      "line", "function" / "resumable" / "constructor" (its THIR dump lines,
      None where it has none), "reject" (its reason, or None)}.

    With `codegen_dir` every module's C++ is written there instead of the
    THIR survey running; `top_reject` and `bodies` stay empty.
    """
    from tpyc.codegen_cpp.context import CodeGenOptions
    from tpyc.diagnostics import (DiagnosticLevel, SemanticError,
                                  format_diagnostics)
    from tpyc.thir.dump import (_constructor_lines, _function_lines,
                                _resumable_lines)
    from tpyc.thir.lower import iter_module_callables
    from tpyc.thir.lower.functions import iter_module_constructors

    facts: dict = {"raised": None, "diagnostics": [], "codegen_error": None,
                   "traceback": None, "top_reject": None, "bodies": []}
    try:
        modules = run()
    except Exception as exc:  # noqa: BLE001 -- every failure is a verdict
        facts["raised"] = {"sema": isinstance(exc, SemanticError),
                           "message": str(exc), "line": _line(exc)}
        facts["traceback"] = traceback.format_exc()
        return facts
    reported = format_diagnostics(compiler, modules)
    facts["diagnostics"] = [(d.level.value, _line(d), str(d.message))
                            for d, _text in reported]
    if any(d.level == DiagnosticLevel.ERROR for d, _text in reported):
        return facts

    entry = next(m for m in modules if m.is_entry_point)
    try:
        if codegen_dir is not None:
            for mod in modules:
                compiler.generate_code(mod, Path(codegen_dir),
                                       entry_module_name=entry.name)
            return facts
        ctx = compiler.collect_thir(entry, CodeGenOptions(),
                                    tolerate_reject=True)
    except Exception as exc:  # noqa: BLE001 -- every failure is a verdict
        facts["codegen_error"] = str(exc)
        facts["traceback"] = traceback.format_exc()
        return facts

    rejects = compiler.thir_reject_by_node

    def body(kind: str, name: str, node) -> dict:
        res = ctx.thir_resumables.get(node)
        why = rejects.get(node)
        return {
            "kind": kind, "name": name, "line": _line(node),
            "function": (_function_lines(ctx.thir_functions[node])
                         if node in ctx.thir_functions else None),
            "resumable": (_resumable_lines(node.name, res)
                          if res is not None else None),
            "constructor": (_constructor_lines(node.name,
                                               ctx.thir_constructors[node])
                            if node in ctx.thir_constructors else None),
            "reject": None if why is None else str(why),
        }

    top = rejects.get(entry.ast)
    facts["top_reject"] = None if top is None else str(top)
    facts["bodies"] = (
        [body("callable", fn.name, fn)
         for fn, _st in iter_module_callables(entry.ast, entry.analyzer)]
        + [body("constructor", rec.name, init)
           for rec, init, _st in iter_module_constructors(entry.ast,
                                                          entry.analyzer)])
    return facts


def failure(facts: dict, what: str) -> RuntimeError:
    """The compile failure `facts` records, for a sweep that cannot turn it
    into a verdict, with the compiler's own traceback attached."""
    failed = facts["raised"]["message"] if facts["raised"] else facts["codegen_error"]
    exc = RuntimeError(f"{what} raised: {failed}")
    exc.add_note(f"--- the compiler's traceback ---\n{facts['traceback']}")
    return exc


def first_error(facts: dict) -> tuple[int, str] | None:
    """The compile's sema error as (line, message): a raised SemanticError,
    else the first ERROR diagnostic; None when there is neither."""
    raised = facts["raised"]
    if raised is not None:
        return (raised["line"], raised["message"]) if raised["sema"] else None
    for level, line, message in facts["diagnostics"]:
        if level == "error":
            return line, message
    return None


def warnings_of(facts: dict) -> list[tuple[int, str]]:
    """Every WARNING diagnostic as (line, message), in reported order."""
    return [(line, message) for level, line, message in facts["diagnostics"]
            if level == "warning"]
