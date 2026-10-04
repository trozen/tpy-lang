# min() / max() with key= hand back an operand itself, so readonly operands
# give a readonly result: writing through it is the readonly-write error.
from tpy import readonly


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def ro(a: readonly[P], b: readonly[P]) -> int:
    m = max(a, b, key=lambda p: p.v)
    m.v = 1  # tpyc: error(/readonly/)
    return m.v


def main() -> None:
    print(ro(P(1), P(2)))


main()
