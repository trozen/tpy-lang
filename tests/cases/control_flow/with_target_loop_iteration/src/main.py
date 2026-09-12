# A `with` inside a loop body whose target is read at the TOP of the NEXT
# iteration: the read is only reachable by following the loop back, never by
# scanning forward from the statement, and the manager must be kept for it.
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
        self.n = -999  # an early drop shows up in the sum


def run() -> int32:
    with Reg(100) as g:
        pass

    total = 0
    for i in range(3):
        total += g.n  # the previous iteration's target (or the pre-loop one)
        with Reg(i) as g:
            pass
    return total


def main() -> None:
    print(run())


main()
