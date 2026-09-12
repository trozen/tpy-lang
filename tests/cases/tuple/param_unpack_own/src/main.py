# An owned-element tuple param takes ownership, so the callee can unpack/move
# its elements out (and forward/return them); @nocopy + mutate-after force a
# move, so a silent copy would be a C++ build error.
from tpy import Own, nocopy, int32


@nocopy
class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def consume(p: tuple[Own[A], Own[A]]) -> int32:
    a, b = p
    a.n += 10
    b.n += 20
    return a.n + b.n


def forward(p: tuple[Own[A], Own[A]]) -> int32:
    return consume(p)


def relay(p: tuple[Own[A], Own[A]]) -> tuple[Own[A], Own[A]]:
    return p


def main() -> None:
    print(consume((A(1), A(2))))
    print(forward((A(1), A(2))))
    a, b = relay((A(3), A(4)))
    print(a.n + b.n)


main()
