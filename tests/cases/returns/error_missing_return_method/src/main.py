# Missing-return enforcement applies to methods (shared body chokepoint).
from tpy import int32


class Calc:
    base: int32

    def __init__(self) -> None:
        self.base = 1

    def pick(self, n: int32) -> int32:  # tpyc: error(/'pick' can reach the end of the function without returning/)
        if n > 0:
            return self.base


def main() -> None:
    print(Calc().pick(1))


main()
