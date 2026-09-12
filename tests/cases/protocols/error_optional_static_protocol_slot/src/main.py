# An annotation-only Optional slot whose pointee is a STATIC protocol: the
# pointer-slot renders are pinned for records, containers, scalars and @dynamic
# protocols, so a structural protocol pointee rejects at the decl. The record
# pointee is pinned by
# tests/cases/records/annotation_only_optional_record_local.
from typing import Protocol
from tpy import int32


class HasValue(Protocol):
    def value(self) -> int32: ...


class Impl:
    def value(self) -> int32:
        return 3


def proto_slot() -> int32:
    p: HasValue | None  # tpyc: error(/decl\.opt_slot_pointee/)
    p = Impl()
    if p is not None:
        return p.value()
    return 0


def main() -> None:
    print(proto_slot())


main()
