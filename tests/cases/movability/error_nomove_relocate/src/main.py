# @nomove marks an otherwise-movable class non-movable; relocating it is a
# clean compile error even though all its fields move fine.
from tpy import int32, Own, nomove


@nomove
class Pinned:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def make() -> Own[Pinned]:
    p = Pinned(5)
    return p  # tpyc: error(/Pinned is not movable .marked @nomove./)


def main() -> None:
    p = make()


main()
