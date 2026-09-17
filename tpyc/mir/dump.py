"""Deterministic internal MIR dump; names supplement identities, never replace them."""

from ..parse import SourceLocation
from .nodes import (
    MIRBranch, MIRCompare, MIRConstant, MIRGoto, MIRFunction, MIRNot, MIRRead,
    MIRReturn,
)
from .validate import validate_function


def _location(loc: SourceLocation | None) -> str:
    return f" @ {loc.line}:{loc.column}" if loc is not None else ""


def dump_function(fn: MIRFunction) -> str:
    validate_function(fn)
    lines = [f"fn {fn.id.module}::{fn.id.declaration} -> {fn.return_type}",
             f"entry bb{fn.entry.index}"]
    for slot in fn.slots:
        name = f" {slot.name}" if slot.name is not None else ""
        lines.append(f"  %{slot.id.index}: {slot.type} {slot.kind.name.lower()}{name}")
    for block in fn.blocks:
        lines.append(f"bb{block.id.index}:")
        for stmt in block.statements:
            value = stmt.value
            if isinstance(value, MIRConstant):
                rhs = repr(value.value)
            elif isinstance(value, MIRRead):
                rhs = f"read %{value.source.index}"
            elif isinstance(value, MIRCompare):
                rhs = f"%{value.left.index} {value.op} %{value.right.index}"
            elif isinstance(value, MIRNot):
                rhs = f"not %{value.operand.index}"
            else:
                raise AssertionError("validated rvalue missing dump")
            lines.append(f"  %{stmt.target.index} = {rhs}{_location(stmt.loc)}")
        term = block.terminator
        if isinstance(term, MIRGoto):
            line = f"goto bb{term.target.index}"
        elif isinstance(term, MIRBranch):
            line = f"branch %{term.condition.index} -> bb{term.then.index}, bb{term.otherwise.index}"
        elif isinstance(term, MIRReturn):
            line = "return" if term.value is None else f"return %{term.value.index}"
        else:
            raise AssertionError("validated terminator missing dump")
        lines.append(f"  {line}{_location(term.loc)}")
    return "\n".join(lines) + "\n"
