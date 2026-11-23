# Error: `Box[Own[P]]` is a class of its own, not `Box[P]`: passing one where
# the other is declared is a type mismatch.
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


def read(b: Box[P]) -> int32:
    return b.v.n


def main() -> None:
    b = Box[Own[P]](P(1))
    print(read(b))  # tpyc: error(/Type mismatch in argument 'b': expected Box\[P\], got Box\[Own\[P\]\]/)


main()
