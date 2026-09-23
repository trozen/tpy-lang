# A tuple local whose NESTED tuple element owns a member holds it by value, so
# the whole-tuple copy at an owning insert is refused: a located reject.
from tpy import int32, nocopy


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def ins(xs: list[tuple[tuple[N, int32], P]], p: P) -> None:
    t = ((N(1), 1), p)
    # Copying `t` whole would copy the N its first element owns.
    xs.append(t)  # tpyc: warning(/tuple element 1\)/) error(/method.arg_shape/)


def main() -> None:
    xs: list[tuple[tuple[N, int32], P]] = []
    ins(xs, P(2))
    print(len(xs))


main()
