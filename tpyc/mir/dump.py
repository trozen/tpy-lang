"""Deterministic internal MIR dump; names supplement identities, never replace them."""

from ..parse import SourceLocation
from .nodes import (
    MIRAlias, MIRBranch, MIRCompare, MIRConstant, MIRDeref, MIRField,
    MIRGoto, MIRFunction, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRValueKind,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove,
)
from .validate import validate_function


def _location(loc: SourceLocation | None) -> str:
    return f" @ {loc.line}:{loc.column}" if loc is not None else ""


def _place(place: MIRPlace) -> str:
    text = f"%{place.root.index}"
    for projection in place.projections:
        if isinstance(projection, MIRDeref):
            text = f"(*{text})"
        elif isinstance(projection, MIRField):
            text += f".{projection.id.owner.qualified_name()}::{projection.id.name}"
    return text


def dump_function(fn: MIRFunction) -> str:
    validate_function(fn)
    lines = [f"fn {fn.id.module}::{fn.id.declaration} -> {fn.return_type}",
             f"entry bb{fn.entry.index}"]
    for slot in fn.slots:
        name = f" {slot.name}" if slot.name is not None else ""
        access = (" readonly-ref" if slot.readonly else " mutable-ref"
                  ) if slot.value_kind is MIRValueKind.BORROWED_RECORD else ""
        if slot.value_kind is MIRValueKind.RECORD_STORAGE:
            access = " owned-storage"
        lines.append(f"  %{slot.id.index}: {slot.type}{access} {slot.kind.name.lower()}{name}")
    for block in fn.blocks:
        lines.append(f"bb{block.id.index}:")
        for stmt in block.statements:
            value = stmt.value
            if isinstance(value, MIRConstant):
                rhs = repr(value.value)
            elif isinstance(value, MIRRead):
                rhs = f"read {_place(value.source)}"
            elif isinstance(value, MIRAlias):
                rhs = f"alias %{value.source.index}"
            elif isinstance(value, MIRBorrow):
                rhs = f"borrow %{value.source.index}"
            elif isinstance(value, MIRConstruct):
                rhs = "construct (" + ", ".join(f"%{s.index}" for s in value.fields) + ")"
            elif isinstance(value, MIRCopy):
                rhs = f"copy {_place(value.source)}"
            elif isinstance(value, MIRMove):
                rhs = f"move %{value.source.index}"
            elif isinstance(value, MIRCompare):
                rhs = f"%{value.left.index} {value.op} %{value.right.index}"
            elif isinstance(value, MIRNot):
                rhs = f"not %{value.operand.index}"
            else:
                raise AssertionError("validated rvalue missing dump")
            lines.append(f"  {_place(stmt.target)} = {rhs}{_location(stmt.loc)}")
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
