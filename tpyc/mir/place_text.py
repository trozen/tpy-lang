"""How the MIR dumps spell a place, a summary path step and a source location:
shared by the full dump and every pass dump below it."""

from ..parse import SourceLocation
from .call_contract import MIRAliasObligation
from .nodes import MIRPlace
from .shapes import MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload
from .value import MIRField


def _location(loc: SourceLocation | None) -> str:
    return f" @ {loc.line}:{loc.column}" if loc is not None else ""


def obligation_text(obligation: MIRAliasObligation) -> str:
    """`param2 vs param0[structure]`: the place the callee still uses, then
    the one it writes."""
    write = obligation.written
    return (f"param{obligation.parameter}" + "".join(map(_write_step, obligation.path))
            + f" vs param{write.parameter}" + "".join(map(_write_step, write.path)))


def _write_step(step: object) -> str:
    match step:
        case MIRContainerStructure():
            return "[structure]"
        case MIRContainerElements():
            return "[elements]"
        case MIRTupleIndex(index=index):
            return f"[{index}]"
        case MIRUnionPayload(alternative=alternative):
            return f".alternative[{alternative}]"
    return f".{step.name}"


def _place(place: MIRPlace) -> str:
    text = f"%{place.root.index}"
    for projection in place.projections:
        match projection:
            case MIRDeref():
                text = f"(*{text})"
            case MIRField(id=field):
                text += f".{field.owner.qualified_name()}::{field.name}"
            case MIRTupleIndex() | MIRUnionPayload() | MIRContainerStructure() | MIRContainerElements():
                text += _write_step(projection)
            case _:
                raise ValueError("unknown place projection")
    return text
