# A list-repeat rvalue at a @dynamic-protocol parameter slot: that slot takes the
# adapter machinery, so the structural argument temp declines it.
from typing import Protocol
from tpy import int32, dynamic


@dynamic
class DynSized(Protocol):
    def __len__(self) -> int32: ...


def dyn_len(d: DynSized) -> int32:
    return len(d)


def repeated(n: int32) -> int32:
    return dyn_len([1] * n)  # tpyc: error(/call.arg_shape.protocol.dyn/)


def main() -> None:
    print(repeated(3))


main()
