# A default read from an import-cycle peer is filled at every call, so the
# union default before it is rendered by no C++ declaration either.
from enum import Enum
from tpy import int32
import part_cycle_b


class EA(Enum):
    A = 1


def fa(a: int32 | str = 1, b: part_cycle_b.EB = part_cycle_b.EB.B) -> int32:  # tpyc: ok
    return b.value


# The union default is itself the cycle peer's.
def fa2(a: part_cycle_b.EB | int32 = part_cycle_b.EB.B) -> int32:  # tpyc: ok
    return 3


class CA:
    def __init__(self) -> None:
        pass

    # The method face of `fa`.
    def m(self, a: int32 | str = 1, b: part_cycle_b.EB = part_cycle_b.EB.B) -> int32:  # tpyc: ok
        return b.value + 2


def main() -> None:
    print("cycle", fa(), fa2(), part_cycle_b.fb(EA.A))  # tpyc: ok
    obj = CA()
    print("cycle method", obj.m())  # tpyc: ok
