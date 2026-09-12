# A ternary return of two durable storage reads lifts the selected arm's
# element addresses (C++ evaluates one branch of the conditional): both
# arms root in the param, so the caller's mutation reaches the field.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    a: tuple[int32, Box]
    b: tuple[int32, Box]
    def __init__(self) -> None:
        self.a = (1, Box(5))
        self.b = (2, Box(7))


def pick(h: Holder, c: bool) -> tuple[int32, Box]:
    return h.a if c else h.b


def main() -> None:
    h = Holder()
    t = pick(h, True)
    t[1].val = 99
    print(h.a[1].val)
    u = pick(h, False)
    u[1].val = 88
    print(h.b[1].val)


main()
