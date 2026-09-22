# The protocol-method family admits TEMP-FREE sources only -- its gate has no
# flush position to thread -- so a member-typed name at a value-union slot,
# whose render hoists a `std::variant<..> __tmp_N`, keeps rejecting while the
# temp-free sources beside it now compile (protocols/proto_arg_tempfree_rows).
from typing import Protocol
from tpy import int32


class UReader(Protocol):
    def take_union(self, u: int32 | str) -> int32: ...


class USink:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def take_union(self, u: int32 | str) -> int32:
        return len(str(u))


def drive(pp: UReader) -> None:
    n = 7
    print(pp.take_union(n))  # tpyc: error(/not yet supported.*protocol.arg_shape/)


def main() -> None:
    drive(USink(0))


main()
