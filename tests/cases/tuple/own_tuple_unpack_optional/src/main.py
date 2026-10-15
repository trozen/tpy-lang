# Tuple-unpacking a return of `tuple[Own[P|None], ...]`: the source tuple
# is in storage form (`std::tuple<std::optional<P>, ...>`) because the
# Own pushes per-element storage form via own_tuple_target. The plain
# capture holds the call result, and the pointer-form local borrows from
# it through optional_to_ptr (the capture outlives the local's reads).
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def pair() -> tuple[Own[P | None], int32]:
    return (P(42), int32(99))


def borrow(p: P | None) -> int32:
    if p is None:
        return int32(-1)
    return p.x


def main() -> None:
    p, n = pair()
    print(borrow(p))
    print(n)


main()
