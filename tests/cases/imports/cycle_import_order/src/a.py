# `a` is still being imported while `b` and `fb` run: it has bound Three (the
# earlier statement on the line that imports `b`), not Item.
from enum import Enum
from t3 import B as Three; import b  # noqa: E702
import fb


class Item:
    def __init__(self, n: int) -> None:
        self.n = n


class Kind(Enum):
    X = 1


def main() -> None:
    it = Item(4)
    b.show(it)
    fb.show(it)
    print("fb tag:", fb.tag_value())
    # b and fb mutated the object main holds: passed by reference.
    print("a:", it.n)
