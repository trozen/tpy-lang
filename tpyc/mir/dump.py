"""Deterministic internal MIR dump; names supplement identities, never replace them."""

from ..parse import SourceLocation
from .nodes import (
    MIRAlias, MIRBranch, MIRCompare, MIRConstant, MIRDeref, MIRField,
    MIRGoto, MIRFunction, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRValueKind,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalPayload,
    MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionPayload, MIRUnionExtract,
)
from .validate import validate_function


def _location(loc: SourceLocation | None) -> str:
    return f" @ {loc.line}:{loc.column}" if loc is not None else ""


def _place(place: MIRPlace) -> str:
    text = f"%{place.root.index}"
    for projection in place.projections:
        match projection:
            case MIRDeref():
                text = f"(*{text})"
            case MIRField(id=field):
                text += f".{field.owner.qualified_name()}::{field.name}"
            case MIRTupleIndex(index=index):
                text += f"[{index}]"
            case MIROptionalPayload():
                text += ".payload"
            case MIRUnionPayload(alternative=alternative):
                text += f".alternative[{alternative}]"
    return text


def dump_function(fn: MIRFunction) -> str:
    validate_function(fn)
    lines = [f"fn {fn.id.module}::{fn.id.declaration} -> {fn.return_type}",
             f"entry bb{fn.entry.index}"]
    for slot in fn.slots:
        name = f" {slot.name}" if slot.name is not None else ""
        if slot.global_id is not None:
            name = f" {slot.global_id.module}::{slot.global_id.name}"
        match slot.value_kind:
            case MIRValueKind.BORROWED_RECORD:
                access = " readonly-ref" if slot.readonly else " mutable-ref"
            case MIRValueKind.RECORD_STORAGE:
                access = " owned-storage"
            case MIRValueKind.TUPLE:
                access = " payload(" + ", ".join(
                    ("readonly-ref" if e.readonly else "mutable-ref")
                    if e.kind is MIRValueKind.BORROWED_RECORD else "value"
                    for e in slot.tuple_layout.elements) + ")"
            case MIRValueKind.OPTIONAL:
                member = slot.optional_layout
                access = " optional(" + (("readonly-ref" if member.readonly else "mutable-ref")
                                          if member.kind is MIRValueKind.BORROWED_RECORD else "value") + ")"
            case MIRValueKind.UNION:
                access = " union(" + ", ".join("absent" if m is None else
                    ("readonly-ref" if m.readonly else "mutable-ref")
                    if m.kind is MIRValueKind.BORROWED_RECORD else "value" for m in slot.union_layout.elements) + ")"
            case MIRValueKind.PAYLOAD_ALIAS:
                access = f" payload-alias({_place(slot.alias_source)})"
            case _:
                access = " readonly" if slot.global_id is not None and slot.readonly else ""
        lines.append(f"  %{slot.id.index}: {slot.type}{access} {slot.kind.name.lower()}{name}")
    if fn.receiver_init is not None:
        init = fn.receiver_init
        values = ", ".join(repr(value.value) if isinstance(value, MIRConstant) else f"%{value.index}"
                           for value in init.fields)
        lines.append(f"initialize-receiver %{init.receiver.index} ({values})")
    for block in fn.blocks:
        lines.append(f"bb{block.id.index}:")
        for stmt in block.statements:
            match stmt.value:
                case MIRConstant(value=value):
                    rhs = repr(value)
                case MIRRead(source=source):
                    rhs = f"read {_place(source)}"
                case MIRAlias(source=source):
                    rhs = f"alias %{source.index}"
                case MIRBorrow(source=source):
                    rhs = f"borrow {_place(source)}"
                case MIRConstruct(fields=fields):
                    rhs = "construct (" + ", ".join(f"%{s.index}" for s in fields) + ")"
                case MIRTupleConstruct(elements=elements):
                    rhs = "tuple (" + ", ".join(f"%{s.index}" for s in elements) + ")"
                case MIRTupleCopy(source=source):
                    rhs = f"tuple-copy %{source.index}"
                case MIROptionalConstruct(source=source):
                    rhs = "absent" if source is None else f"present %{source.index}"
                case MIROptionalCopy(source=source):
                    rhs = f"optional-copy %{source.index}"
                case MIRIsPresent(source=source):
                    rhs = f"is-present %{source.index}"
                case MIRUnionConstruct(alternative=alternative, source=source):
                    payload = f"%{source.index}" if source is not None else "absent"
                    rhs = f"union[{alternative}] {payload}"
                case MIRUnionCopy(source=source):
                    rhs = f"union-copy %{source.index}"
                case MIRIsAlternative(source=source, alternatives=alternatives):
                    rhs = f"is-alternative %{source.index} {alternatives}"
                case MIRUnionExtract(source=source):
                    rhs = f"extract {_place(source)}"
                case MIRCopy(source=source):
                    rhs = f"copy {_place(source)}"
                case MIRMove(source=source):
                    rhs = f"move %{source.index}"
                case MIRCompare(op=op, left=left, right=right):
                    rhs = f"%{left.index} {op} %{right.index}"
                case MIRNot(operand=operand):
                    rhs = f"not %{operand.index}"
                case _:
                    raise AssertionError("validated rvalue missing dump")
            lines.append(f"  {_place(stmt.target)} = {rhs}{_location(stmt.loc)}")
        term = block.terminator
        match term:
            case MIRGoto(target=target):
                line = f"goto bb{target.index}"
            case MIRBranch(condition=condition, then=then, otherwise=otherwise):
                line = f"branch %{condition.index} -> bb{then.index}, bb{otherwise.index}"
            case MIRReturn(value=value):
                line = "return" if value is None else f"return %{value.index}"
            case _:
                raise AssertionError("validated terminator missing dump")
        lines.append(f"  {line}{_location(term.loc)}")
    return "\n".join(lines) + "\n"
