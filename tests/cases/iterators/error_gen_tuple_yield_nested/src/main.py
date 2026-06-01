# A non-value member nested inside an inner tuple is rejected: codegen's tuple
# borrow/storage conversion is flat, so the nested borrow slot is miscompiled.
from typing import Iterator


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def g(boxes: list[Box]) -> Iterator[tuple[int, tuple[int, Box]]]:
    i = 0
    for b in boxes:
        yield (i, (i, b))  # tpyc: error(/nested inside another tuple is not yet supported/)
        i += 1


def main() -> None:
    data = [Box(1)]
    for i, pair in g(data):
        print(pair[1].val)


main()
