"""Deterministic internal MIR dump; names supplement identities, never replace them."""


from ..thir.nodes import THIRStubCallee
from ..thir.scalar_leaves import MIRClass
from .call_contract import MIRResultHolder, MIRStaticLoan
from .nodes import (
    MIRAlias, MIRBranch, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRGoto, MIRFunction, MIRNot, MIROp, MIRPrint, MIRSuspend,
    MIRRead, MIRReturn, MIRBorrow, MIRConstruct, MIRCopy, MIRMove,
    MIRRegionId, MIRStorageInit,
    MIRIsAlternative, MIRUnionExtract, MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance,
    MIRRangeAdvance, MIRWrap,
)
from .value import MIRHolding, MIRShape
from .place_text import _location, _place, _write_step, obligation_text
from .prepare import first_phase
from .validate import MIRPresenceError


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
    if call.summary.transfers:
        def end(index: object, path: tuple[object, ...]) -> str:
            root = "result" if isinstance(index, MIRResultHolder) else "static" if isinstance(index, MIRStaticLoan) \
                else f"param{index}"
            return root + "".join(_write_step(step) for step in path)
        effects += ", stores={" + ", ".join(sorted(f"{end(t.holder, t.path)} <- {end(t.source, t.source_path)}"
                                                  for t in call.summary.transfers)) + "}"
    if call.summary.obligations:
        effects += ", no-alias={" + ", ".join(sorted(map(obligation_text, call.summary.obligations))) + "}"
    if call.summary.global_reads:
        effects += ", global-reads={" + ", ".join(sorted(f"{g.module}::{g.name}"
                                                        for g in call.summary.global_reads)) + "}"
    if call.initializes is not None:
        effects += f", initializes={call.initializes}"
    return f"call {callee}({args}) [{effects}, {'may-raise' if call.may_raise else 'normal-return'}]"






def _layout(layout: MIRShape) -> str:
    """A container's members: `element=T:class`, a dict's `value=` too."""
    def member(name: str, m: MIRShape) -> str:
        return f" {name}={m.type}:{m.cls.name.lower()}" + (":readonly" if m.readonly else "")
    return member("element", layout.element) + ("" if layout.value is None else member("value", layout.value))


def _member_access(member: MIRShape | None) -> str:
    """How a wrapper holds a member: a reference with its access, or a value."""
    if member is None:
        return "absent"
    if member.is_pointer:
        return "readonly-ref" if member.readonly else "mutable-ref"
    return "value"


def _wrap(wrap: MIRWrap) -> str:
    return f"wrap[{wrap.alternative}]" + ("" if wrap.source is None else f" {_place(wrap.source)}")


def _rvalue(value: object) -> str:
    """A value as a statement's right-hand side or an entry initializer
    spells it; a composed member lists its fields' values in braces."""
    match value:
        case MIRRangeAdvance(source=source, step=step):
            return f"range-advance %{source.index} step={step}"
        case MIRIteratorInit(source=source):
            return f"iterator-init %{source.index}"
        case MIRIteratorHasNext(source=source):
            return f"iterator-has-next %{source.index}"
        case MIRIteratorRead(source=source):
            return f"iterator-read %{source.index}"
        case MIRIteratorAdvance(source=source):
            return f"iterator-advance %{source.index}"
        case MIRConstant(value=constant):
            return repr(constant)
        case MIRRead(source=source, may_raise=may_raise):
            return f"read {_place(source)}" + (" may-raise" if may_raise else "")
        case MIRAlias(source=source):
            return f"alias %{source.index}"
        case MIRBorrow(source=source, may_raise=may_raise):
            return f"borrow {_place(source)}" + (" may-raise" if may_raise else "")
        case MIRConstruct(fields=fields, may_raise=may_raise):
            return "construct (" + ", ".join(_rvalue(s) if isinstance(s, MIRConstant) else f"%{s.index}"
                                             for s in fields) + ")" + (" may-raise" if may_raise else "")
        case MIRWrap():
            return _wrap(value)
        case MIRIsAlternative(source=source, alternatives=alternatives):
            return f"is-alternative {_place(source)} {alternatives}"
        case MIRUnionExtract(source=source):
            return f"extract {_place(source)}"
        case MIRCopy(source=source, may_raise=may_raise):
            return f"copy {_place(source)}" + (" may-raise" if may_raise else "")
        case MIRMove(source=source):
            return f"move %{source.index}"
        case MIRCall() as call:
            return _call(call)
        case MIRCompare(op=op, left=left, right=right):
            return f"%{left.index} {op} %{right.index}"
        case MIRNot(operand=operand):
            return f"not %{operand.index}"
        case MIROp(op=op, operands=values, may_raise=may_raise):
            return f"op {op} (" + ", ".join(f"%{v.index}" for v in values) + ")" + (" may-raise" if may_raise else "")
    raise AssertionError("validated rvalue missing dump")




def dump_function(fn: MIRFunction) -> str:
    # A printer prints a body whose dependency facts are refused too: it
    # checks structure and presence, never that every field read is proven,
    # so it runs no dependency analysis.
    first = first_phase(fn)
    if first.presence.issues:
        raise MIRPresenceError(first.presence.issues[0].message)
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
        shape = slot.shape
        match shape.holding, shape.cls:
            case (MIRHolding.CURSOR, _):
                access = " native-iterator"
                access += " readonly" if slot.readonly else " mutable"
                access += _layout(shape)
            case (MIRHolding.POINTER | MIRHolding.REGION, _):
                access = " readonly-ref" if slot.readonly else " mutable-ref"
                if shape.members:
                    access += _layout(shape)
            case (MIRHolding.ALIAS, _):
                access = f" payload-alias({_place(slot.alias_source)})"
            case (_, MIRClass.OWNED_LEAF | MIRClass.RECORD | MIRClass.CONTAINER):
                access = " owned-storage" + (_layout(shape) if shape.members else "")
            case (_, MIRClass.TUPLE):
                access = " payload(" + ", ".join(map(_member_access, shape.elements)) + ")"
            case (_, MIRClass.OPTIONAL):
                access = " optional(" + _member_access(shape.payload) + ")"
            case (_, MIRClass.UNION):
                access = " union(" + ", ".join(map(_member_access, shape.alternatives)) + ")"
            case _:
                access = " readonly" if slot.global_id is not None and slot.readonly else ""
        duration = (f" duration=r{slot.storage_duration.index}" if isinstance(slot.storage_duration, MIRRegionId)
                    else f" duration={slot.storage_duration.name.lower()}" if slot.storage_duration is not None else "")
        if slot.residence is not None:
            duration += f" residence=r{slot.residence.index}"
        lines.append(f"  %{slot.id.index}: {slot.type}{access} {slot.kind.name.lower()}{name}{duration}")
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
            if isinstance(stmt, MIRSuspend):
                lines.append(f"  suspend %{stmt.value.index}{_location(stmt.loc)}")
                continue
            if isinstance(stmt, MIRStorageInit) and stmt.value is None:
                lines.append(f"  begin-storage {_place(stmt.target)}{_location(stmt.loc)}")
                continue
            if isinstance(stmt, MIRStorageInit):
                lines.append(f"  initialize-storage {_place(stmt.target)} alternative={stmt.alternative} "
                             f"value={stmt.value.value!r}{_location(stmt.loc)}")
                continue
            rhs = _rvalue(stmt.value)
            fact = stmt.storage_write
            write = f" [{fact.mode.name.lower()}]" if fact is not None else ""
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
