# A lambda's by-value record result takes an existing-object arm of a select
# only where sema declared its copy; a lambda result declares none
# (BUGS.md#lambda-body-partial-return-checks).
from tpy import Fn, Own, int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def through(f: Fn[[A], Own[A]], a: A) -> None:
    r = f(a)
    r.x = 9


def main() -> None:
    flag = True
    b = A(2)
    # `q` is the caller's object: the result would copy it silently.
    through(lambda q: q if flag else A(0), b)  # tpyc: error(/expr\.lambda:lambda\.result/)
    print(b.x)


main()
