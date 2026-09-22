# A readonly[container] CONSTRUCTOR parameter is a separate admission gate
# from the free/method call's, and it still rejects a non-empty container
# literal -- widening the call argument must not be read as covering it.
from tpy import int32, readonly


class Numbers:
    total: int32

    def __init__(self, xs: readonly[list[int32]]) -> None:
        self.total = len(xs)


def main() -> None:
    n = Numbers([1, 2, 3])  # tpyc: error(/not yet supported/)
    print(n.total)


main()
