# Storing an owning-call result (Own[tuple] return) into a field moves a
# fresh unshared temporary -- no aliasing is observable, so the per-element
# field copy warning must NOT fire (unlike borrow-form sources, which copy
# where CPython aliases).
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def make_pair(v: int32) -> Own[tuple[int32, Box]]:
    return (v, Box(v))


class H:
    t: tuple[int32, Box]

    def __init__(self) -> None:
        self.t = make_pair(5)  # tpyc: ok


def main() -> None:
    h = H()
    print(h.t[0])
    print(h.t[1].val)


main()
