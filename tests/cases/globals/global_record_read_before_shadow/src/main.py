# A record global read as a field receiver in a body that later SHADOWS the name
# with a local: sema resolves the early read to the global, so it takes the
# dedicated global-record-receiver arm rather than a seeded slot read.
# It compiles and prints `7 8`.
from tpy import int32


class Gate:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


gate: Gate = Gate(7)


def read() -> int32:
    return gate.x


def read_then_shadow() -> int32:
    y = gate.x  # the read before the local binding still means the global
    gate = Gate(1)
    return y + gate.x


def main() -> None:
    print(read(), read_then_shadow())


main()
