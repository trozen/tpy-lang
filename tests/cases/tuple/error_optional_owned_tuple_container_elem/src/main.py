# A nullable owned-record tuple binding placed in a list literal would copy
# its records where Python's list shares them: refused. A call result as
# the element is a fresh value and is not this reject.
from tpy import int32, Own


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def owned(k: bool) -> tuple[Own[Box], int32] | None:
    if k:
        return None
    return (Box(1), 2)


def main() -> None:
    t = owned(False)
    xs = [t]  # tpyc: error(/container_literal/)
    if t is not None:
        t[0].n = 9
    print(len(xs))


main()
