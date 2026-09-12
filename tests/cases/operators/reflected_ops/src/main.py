# Reflected operators on user types: `left OP right` with no forward op dispatches
# to right.__rOP__(left). Covers __rmul__/__radd__ (value type) and __ror__ (ref class).
from __future__ import annotations
from tpy import ValueType, int64, int32, Own


class Dur(ValueType):
    nanos: int64

    def __init__(self, ns: int64) -> None:
        self.nanos = ns

    def __rmul__(self, f: float) -> float:
        return f * self.nanos

    def __radd__(self, n: int64) -> int64:
        return n + self.nanos


class Flags:
    bits: int32

    def __init__(self, bits: int32) -> None:
        self.bits = bits

    def __ror__(self, other: int32) -> Own[Flags]:
        return Flags(other | self.bits)


def main() -> None:
    d = Dur(100)
    print(1.5 * d)   # float on the left -> Dur.__rmul__ -> 150.0
    print(10 + d)    # int on the left -> Dur.__radd__ -> 110
    f = Flags(1)
    r = 4 | f        # int on the left -> Flags.__ror__ -> 5
    print(r.bits)


main()
