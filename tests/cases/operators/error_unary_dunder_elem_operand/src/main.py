# A record ELEMENT read is still not an admitted operand -- the unary dunder
# reads its operand exactly as the binary one does, and the binary one
# rejects this shape too, so widening the unary read must not open it.
from tpy import int32


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __neg__(self) -> int32:
        return -self.n


def main() -> None:
    items = [Cell(1), Cell(2)]
    print(-items[0])  # tpyc: error(/not yet supported/)


main()
