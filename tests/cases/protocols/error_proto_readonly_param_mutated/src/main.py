# The readonly fence on a protocol parameter. The concept probes a
# `readonly[...]` parameter at the CONST form, so an implementation that
# mutates through it cannot conform -- and the rejection stays in sema, at the
# mutation itself, rather than surfacing as a C++ conformance failure.
from typing import Protocol
from tpy import int32, readonly


class Shapes(Protocol):
    def total(self, xs: readonly[list[int32]]) -> int32: ...


class Impl:
    def __init__(self) -> None:
        pass

    def total(self, xs: readonly[list[int32]]) -> int32:
        xs.append(1)  # tpyc: error(/readonly/)
        return int32(len(xs))


def drive(s: Shapes, xs: readonly[list[int32]]) -> int32:
    return s.total(xs)


def main() -> None:
    ys: list[int32] = [1, 2]
    print(drive(Impl(), ys))


main()
