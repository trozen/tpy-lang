# Local-source variant of error_param_forward_own_use_after: a @nocopy
# owned-tuple LOCAL forwarded while still used after is the same use-after-move
# error (the check fires for owned locals as well as owned-tuple params).
from tpy import Own, nocopy, Int32


@nocopy
class A:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make() -> tuple[Own[A], Own[A]]:
    return (A(1), A(2))


def sink(p: tuple[Own[A], Own[A]]) -> Int32:
    a, b = p
    return a.n + b.n


def run() -> Int32:
    t = make()
    x = sink(t)  # tpyc: error(/used after this point and cannot be moved into 'p'/)
    return x + t[0].n


def main() -> None:
    print(run())


main()
