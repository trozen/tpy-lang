# A tuple yield uses borrow form per-element (std::tuple<int, Box*>), so a
# freshly-constructed non-value member would store &temp and dangle after the
# yield-return. The per-element dangling check must reject it (the scalar
# Iterator[Box] analog is already rejected); the fix is Own[Box] on the element.
from typing import Iterator


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def g(n: int) -> Iterator[tuple[int, Box]]:
    for i in range(n):
        yield (i, Box(i))  # tpyc: error(/Cannot yield local.*tuple element 1.*Own\[Box\]/)


def main() -> None:
    for i, b in g(3):
        print(b.val)


main()
