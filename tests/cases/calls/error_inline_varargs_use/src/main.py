# An @inline function whose body uses its `*args` parameter is refused at the
# call: the expansion has no spelling for the packed arguments yet.
from tpy import inline, int64


def show(n: int64) -> int64:
    print(n)
    return n


@inline
def f(*xs: int64) -> None:
    show(xs)


def main() -> None:
    f(1, 2, 3)  # tpyc: error(/cannot use its \*args/)


main()
