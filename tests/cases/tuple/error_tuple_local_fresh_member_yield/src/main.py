# A tuple-typed local that owns a freshly constructed non-value member is
# storage form; borrowing that member across a yield boundary would dangle.
# The bare-name yield (not a literal) must be rejected, pointing at Own[Box].
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(n: Int32) -> Iterator[tuple[Int32, Box]]:
    i = Int32(0)
    while i < n:
        t = (i, Box(i * 10))
        yield t  # tpyc: error(/owns a freshly constructed value/)
        i += 1


def main() -> None:
    for pair in gen(Int32(3)):
        print(pair[0], pair[1].val)


main()
