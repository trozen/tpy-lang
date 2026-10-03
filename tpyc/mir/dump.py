"""Deterministic internal MIR dump; names supplement identities, never replace them."""

from ..parse import SourceLocation
from ..thir.nodes import THIRStubCallee
from .nodes import (
    MIRAlias, MIRBranch, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRDeref, MIRField,
    MIRGoto, MIRFunction, MIRNot, MIROp, MIRPrint, MIRPlace, MIRRead, MIRReturn, MIRValueKind,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRMemberInit, MIRMemberInitMode,
    MIRRegionId, MIRStorageInit, MIRRecordStorageInit, MIRRecordStorageKind,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex, MIRTupleInitialization,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalPayload,
    MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionPayload, MIRUnionExtract,
    MIRContainerStructure, MIRContainerElements, MIRContainerLayout, MIRTupleElement,
    MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance,
    MIRRangeAdvance, MIRSlotId,
)
from .validate import validate_function


def _location(loc: SourceLocation | None) -> str:
    return f" @ {loc.line}:{loc.column}" if loc is not None else ""


def _call(call: MIRCall) -> str:
    identity = call.summary.callee.identity
    args = ", ".join(f"%{sid.index}" for sid in call.arguments)
    writes = sorted(f"param{w.parameter}" + "".join(_write_step(step) for step in w.path) for w in call.summary.writes)
    effects = "writes={" + ", ".join(writes) + "}" if writes else "reader"
    if isinstance(call.summary.callee, THIRStubCallee):
        # Overloads share the name; the parameter types tell them apart.
        callee = f"stub {identity.qualified_name}[" + ", ".join(str(t) for t in identity.param_types) + "]"
        # A method stub's effects are derived from its receiver facts when it declares no contract.
        if call.summary.callee.contract is not None:
            effects = f"{call.summary.callee.contract.value}, {effects}"
    elif identity.owner is not None:
        # A method by its owning record's qualified name, as `--dump-thir` lists it.
        callee = f"{identity.owner}.{identity.name}"
    else:
        callee = f"{identity.module}::{identity.name}"
    if call.summary.borrowed_result is not None:
        effects += ", returns={" + ", ".join(sorted(f"param{o.parameter}" + "".join(_write_step(step) for step in o.path)
                                                   for o in call.summary.returns)) + "}"
    if call.summary.global_reads:
        effects += ", global-reads={" + ", ".join(sorted(f"{g.module}::{g.name}"
                                                        for g in call.summary.global_reads)) + "}"
    return f"call {callee}({args}) [{effects}, {'may-raise' if call.may_raise else 'normal-return'}]"


def _write_step(step: object) -> str:
    match step:
        case MIRContainerStructure():
            return "[structure]"
        case MIRContainerElements():
            return "[elements]"
    return f".{step.name}"


def _layout(layout: MIRContainerLayout) -> str:
    """A container's members: `element=T:kind`, a dict's `value=` too."""
    def member(name: str, m: MIRTupleElement) -> str:
        return f" {name}={m.type}:{m.kind.name.lower()}" + (":readonly" if m.readonly else "")
    return member("element", layout.element) + ("" if layout.value is None else member("value", layout.value))


def _member_init(member: MIRMemberInit, borrowed: set[MIRSlotId]) -> str:
    """One receiver member's entry initialization: a scalar by its value, an
    owned leaf by how its buffer arrives (a copy through a borrowed
    parameter reads the storage it points at)."""
    source = (repr(member.source.value) if isinstance(member.source, MIRConstant)
              else "construct (" + ", ".join(f"%{s.index}" for s in member.source.fields) + ")"
              if isinstance(member.source, MIRConstruct)
              else f"(*%{member.source.index})" if member.source in borrowed else f"%{member.source.index}")
    match member.mode:
        case MIRMemberInitMode.SCALAR:
            return source
        case MIRMemberInitMode.MOVE:
            return f"move {source}"
    return f"copy {source}" + (" may-raise" if member.may_raise else "")


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
            case MIRContainerStructure():
                text += "[structure]"
            case MIRContainerElements():
                text += "[elements]"
            case _:
                raise ValueError("unknown place projection")
    return text


def dump_function(fn: MIRFunction) -> str:
    validate_function(fn)
    lines = [f"fn {fn.id.module}::{fn.id.declaration} -> {fn.return_type}",
             f"entry bb{fn.entry.index}"]
    if fn.borrowed_result is not None:
        lines.append("result borrowed " + ("readonly" if fn.borrowed_result.readonly else "mutable"))
    if fn.exceptional_exits:
        lines.append("exceptional exits")
    for slot in fn.slots:
        name = f" {slot.name}" if slot.name is not None else ""
        if slot.global_id is not None:
            name = f" {slot.global_id.module}::{slot.global_id.name}"
        match slot.value_kind:
            case MIRValueKind.BORROWED_CONTAINER | MIRValueKind.NATIVE_ITERATOR:
                access = (" native-iterator" if slot.value_kind is MIRValueKind.NATIVE_ITERATOR else " container-ref")
                access += " readonly" if slot.readonly else " mutable"
                access += _layout(slot.container_layout)
            case MIRValueKind.BORROWED:
                access = " readonly-ref" if slot.readonly else " mutable-ref"
                if slot.container_layout is not None:
                    access += _layout(slot.container_layout)
            case MIRValueKind.OWNED if slot.container_layout is not None:
                access = " owned" + _layout(slot.container_layout)
            case MIRValueKind.OWNED:
                access = " owned-storage"
            case MIRValueKind.TUPLE:
                access = " payload(" + ", ".join(
                    ("readonly-ref" if e.readonly else "mutable-ref")
                    if e.kind is MIRValueKind.BORROWED else "value"
                    for e in slot.tuple_layout.elements) + ")"
            case MIRValueKind.OPTIONAL:
                member = slot.optional_layout
                access = " optional(" + (("readonly-ref" if member.readonly else "mutable-ref")
                                          if member.kind is MIRValueKind.BORROWED else "value") + ")"
            case MIRValueKind.UNION:
                access = " union(" + ", ".join("absent" if m is None else
                    ("readonly-ref" if m.readonly else "mutable-ref")
                    if m.kind is MIRValueKind.BORROWED else "value" for m in slot.union_layout.elements) + ")"
            case MIRValueKind.PAYLOAD_ALIAS:
                access = f" payload-alias({_place(slot.alias_source)})"
            case _:
                access = " readonly" if slot.global_id is not None and slot.readonly else ""
        duration = (f" duration=r{slot.storage_duration.index}" if isinstance(slot.storage_duration, MIRRegionId)
                    else f" duration={slot.storage_duration.name.lower()}" if slot.storage_duration is not None else "")
        if slot.residence is not None:
            duration += f" residence=r{slot.residence.index}"
        if slot.record_storage is MIRRecordStorageKind.OPTIONAL:
            duration += " optional-backing"
        lines.append(f"  %{slot.id.index}: {slot.type}{access} {slot.kind.name.lower()}{name}{duration}")
    if fn.receiver_init is not None:
        init = fn.receiver_init
        borrowed = {s.id for s in fn.slots if s.value_kind is MIRValueKind.BORROWED}
        values = ", ".join(_member_init(member, borrowed) for member in init.fields)
        lines.append(f"initialize-receiver %{init.receiver.index} ({values})")
    for region in fn.regions:
        parent = f"r{region.parent.index}" if region.parent is not None else "body"
        lines.append(f"region r{region.id.index} parent={parent} entry=bb{region.entry.index}")
    for block in fn.blocks:
        lines.append(f"bb{block.id.index}:")
        for stmt in block.statements:
            if isinstance(stmt, MIRCallStmt):
                lines.append(f"  {_call(stmt.call)}{_location(stmt.loc)}")
                continue
            if isinstance(stmt, MIRPrint):
                arguments = ", ".join(f"%{s.index}" for s in stmt.arguments)
                lines.append(f"  print ({arguments}){_location(stmt.loc)}")
                continue
            if isinstance(stmt, MIRRecordStorageInit):
                lines.append(f"  initialize-record-wrapper {_place(stmt.target)} empty{_location(stmt.loc)}")
                continue
            if isinstance(stmt, MIRStorageInit):
                lines.append(f"  initialize-storage {_place(stmt.target)} alternative={stmt.alternative} "
                             f"value={stmt.value.value!r}{_location(stmt.loc)}")
                continue
            match stmt.value:
                case MIRRangeAdvance(source=source, step=step):
                    rhs = f"range-advance %{source.index} step={step}"
                case MIRIteratorInit(source=source):
                    rhs = f"iterator-init %{source.index}"
                case MIRIteratorHasNext(source=source):
                    rhs = f"iterator-has-next %{source.index}"
                case MIRIteratorRead(source=source):
                    rhs = f"iterator-read %{source.index}"
                case MIRIteratorAdvance(source=source):
                    rhs = f"iterator-advance %{source.index}"
                case MIRConstant(value=value):
                    rhs = repr(value)
                case MIRRead(source=source, may_raise=may_raise):
                    rhs = f"read {_place(source)}" + (" may-raise" if may_raise else "")
                case MIRAlias(source=source):
                    rhs = f"alias %{source.index}"
                case MIRBorrow(source=source, may_raise=may_raise):
                    rhs = f"borrow {_place(source)}" + (" may-raise" if may_raise else "")
                case MIRConstruct(fields=fields, may_raise=may_raise):
                    rhs = "construct (" + ", ".join(f"%{s.index}" for s in fields) + ")" + (
                        " may-raise" if may_raise else "")
                case MIRTupleConstruct(elements=elements):
                    rhs = "tuple (" + ", ".join(
                        "construct (" + ", ".join(f"%{s.index}" for s in element.fields) + ")"
                        if isinstance(element, MIRConstruct) else f"%{element.index}"
                        for element in elements) + ")"
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
                case MIRCopy(source=source, may_raise=may_raise):
                    rhs = f"copy {_place(source)}" + (" may-raise" if may_raise else "")
                case MIRMove(source=source):
                    rhs = f"move %{source.index}"
                case MIRCall() as call:
                    rhs = _call(call)
                case MIRCompare(op=op, left=left, right=right):
                    rhs = f"%{left.index} {op} %{right.index}"
                case MIRNot(operand=operand):
                    rhs = f"not %{operand.index}"
                case MIROp(op=op, operands=values, may_raise=may_raise):
                    rhs = f"op {op} (" + ", ".join(f"%{v.index}" for v in values) + ")" + (" may-raise" if may_raise else "")
                case _:
                    raise AssertionError("validated rvalue missing dump")
            fact = stmt.storage_write
            write = (" [initialize-tuple]" if isinstance(fact, MIRTupleInitialization)
                     else f" [{fact.mode.name.lower()}]" if fact is not None else "")
            lines.append(f"  {_place(stmt.target)} = {rhs}{write}{_location(stmt.loc)}")
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
