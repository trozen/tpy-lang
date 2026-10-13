"""The compile verdict of one generated cell program.

Shared by the verdict-table instruments in this directory
(`property_position_sweep.py`, `tuple_element_matrix.py`): each generates one
program per cell, and each records for it the same pair -- where the compiler
stopped, and the first warning the CELL (not the shared prelude) produced.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from program_compile import (  # noqa: E402
    compile_program, first_error, program_facts, warnings_of)

# The last line of every cell's shared head. `cell_verdict` finds it to tell
# the PRELUDE's own diagnostics from the cell's: a prelude that warns would
# otherwise fill the warning column of every cell with the same line.
CELL_MARK = "# --- cell ---"

_REJECT_PHRASE = "not yet supported by C++ code generation"


def cell_verdict(src: Path, build_dir: Path,
                 all_warnings: bool = False) -> list[str]:
    """`[verdict, warning]`: "ok" or the reject/error tag the compiler stopped
    at, beside the FIRST warning it emitted below `CELL_MARK` ("" for none).

    The warning is half the answer: several cells compile only because a copy
    was made, and say so only in a warning, so a table recording the tag alone
    would call a lost or gained warning "no verdict moved". An admitted cell
    leaves its generated C++ under `build_dir`.

    `all_warnings` records EVERY warning, " | "-joined, for a table whose
    cells warn once per tuple element -- there a gained or lost second warning
    is a verdict of its own. It also keeps a warning that carries no location,
    which the first-warning form has to drop because it cannot place it on
    either side of the mark.
    """
    cell_line = cell_start(src)
    facts = compile_program(src, program_facts, build_dir, repo=REPO)
    # Every failure IS a verdict.
    if facts["raised"] is not None:
        return [diag_tag(facts["raised"]["message"]), ""]
    warned = ""
    warnings = [message for line, message in warnings_of(facts)
                if line > cell_line or (all_warnings and line == 0)]
    if warnings and all_warnings:
        warned = " | ".join(diag_tag(w) for w in warnings)
    elif warnings:
        warned = diag_tag(warnings[0])
    error = first_error(facts)
    if error is not None:
        return [diag_tag(error[1]), warned]
    if facts["codegen_error"] is not None:
        return [diag_tag(facts["codegen_error"]), warned]
    return ["ok", warned]


def cell_start(src: Path) -> int:
    """The line `CELL_MARK` sits on; 0 if the program has no mark."""
    for i, line in enumerate(src.read_text().split("\n"), start=1):
        if line == CELL_MARK:
            return i
    return 0


def diag_tag(message: str) -> str:
    """The reject tag out of a diagnostic, or a short form of the message.

    The tag is what a table records: the prose around it moves with unrelated
    wording changes, the tag is the verdict. Only a lowering reject carries
    one; any other message that happens to end in a parenthesis (a remediation
    hint does) is recorded by its opening words.
    """
    if _REJECT_PHRASE in message and message.rstrip().endswith(")"):
        return message[message.rfind("(") + 1:-1]
    return message.strip().split("\n")[0][:80]
