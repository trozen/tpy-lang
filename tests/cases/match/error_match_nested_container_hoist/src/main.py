# A NESTED match whose arm leaks a container binding used after both matches:
# only value-typed hoists are position-neutral. TPy rejects the inner match.
from tpy import int32


class Inner:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    inner: Inner

    def __init__(self, n: int32) -> None:
        self.inner = Inner(n)


def f(h: Holder, g: Holder) -> int32:
    match h:
        case Holder(inner=q):
            match g:  # tpyc: error(/stmt\.match/)
                case Holder():
                    # `xs` is bound in an arm and read outside it.
                    xs = [1, 2]
            return q.n + len(xs)
    return 0


def main() -> None:
    print(f(Holder(1), Holder(2)))


main()
