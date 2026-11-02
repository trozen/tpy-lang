# A record global read as a field receiver in a body that later rebinds the
# name: the binding makes `gate` local to the whole body, so the early
# `gate.x` is CPython's UnboundLocalError and never reads the module global.
from tpy import int32


class Gate:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


gate: Gate = Gate(7)


def read() -> int32:
    return gate.x


def read_then_shadow() -> int32:
    # The attribute-receiver read the later `gate = Gate(1)` makes local.
    y = gate.x  # tpyc: error(/'gate' is read before it is bound on line 24: a name bound anywhere in a function is local to the whole function, so this read does not see the module's 'gate'; rename one of them/)
    gate = Gate(1)
    return y + gate.x


def main() -> None:
    print(read(), read_then_shadow())


main()
