# A tuple binding deleted and bound again while an alias of its old element is
# live: the new element's object needs storage of its own (nothing on the
# path holds the old tuple, but the alias does), which a function body does
# not have yet.
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def main() -> None:
    b = Box(2)
    p = make_mixed(1, b)
    a = p[0]
    del p
    p = make_mixed(9, b)  # tpyc: error(/or was deleted, so the object the rebind of .p. creates needs storage of its own/)
    print(a.n, p[0].n)


main()
