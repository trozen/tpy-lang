# A property setter mutates the receiver, so a store through a
# Ptr[readonly[...]] is rejected even though the setter exists.
from tpy import int32, Ptr, readonly


class Box:
    _v: int32

    def __init__(self) -> None:
        self._v = 0

    @property
    def v(self) -> int32:
        return self._v

    @v.setter
    def v(self, value: int32) -> None:
        self._v = value


def write(p: Ptr[readonly[Box]]) -> None:
    # the setter call through the read-only pointer
    p.v = 1  # tpyc: error(/Cannot assign through read-only pointer/)


def main() -> None:
    b = Box()
    write(b)


main()
