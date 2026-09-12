# Forwarding an owned-tuple param to another while still using it afterward is
# a use-after-move, rejected with a clean diagnostic like the scalar Own[T] arg.
from tpy import Own, nocopy, int32


@nocopy
class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def sink(p: tuple[Own[A], Own[A]]) -> int32:
    a, b = p
    return a.n + b.n


def fwd(p: tuple[Own[A], Own[A]]) -> int32:
    x = sink(p)  # tpyc: error(/used after this point and cannot be moved into 'p'/)
    return x + p[0].n


def main() -> None:
    print(fwd((A(1), A(2))))


main()
