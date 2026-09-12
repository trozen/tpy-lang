# A `with` target read inside a try body whose handler binds the SAME name.
# The handler's `except ... as view` is a binding of its own, not a rebind that
# kills the read above it, so the manager must still outlive its block --
# `__del__` poisons the payload, making an early drop visible as -999.
from tpy import int32


class Reg:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        self.n = -999


def probe(flag: bool) -> int32:
    with Reg(11) as view:
        pass
    if flag:
        with Reg(22) as view:
            pass

    result = 0
    try:
        result = view.n  # reads the still-live manager's payload
    except ValueError as view:
        result = -1
    return result


def main() -> None:
    print(probe(True))
    print(probe(False))


main()
