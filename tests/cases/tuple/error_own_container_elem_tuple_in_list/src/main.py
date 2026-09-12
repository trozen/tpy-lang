# A container literal whose element is a mixed `Own` tuple with an owned
# CONTAINER member: the container-element sink has no form for it.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_lc() -> tuple[Own[list[int32]], Own[Box]]:
    return ([1, 2], Box(3))


def lc_in_list() -> int32:
    # The list literal's only element is that owning tuple.
    ys = [make_lc()]  # tpyc: error(/expr\.container_literal/)
    return len(ys)


def main() -> None:
    print(lc_in_list())


main()
