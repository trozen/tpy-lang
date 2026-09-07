# A NESTED tuple unpack target with cheap scalar elements binds as a plain value
# copy, an unmirrored bind, so the loop rejects.
from typing import Iterator
from tpy import Int32, Own


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def rows(n: Int32) -> Iterator[tuple[Int32, tuple[Int32, Own[Box]]]]:
    for i in range(n):
        yield (i, (i, Box(i * 5)))


def main() -> None:
    total = 0
    for i, pair in rows(3):  # tpyc: error(/iter.call.generator/)
        total = total + pair[1].val
    print(total)


main()
