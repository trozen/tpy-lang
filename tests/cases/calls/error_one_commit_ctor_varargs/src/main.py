# A construction through an `__init__`'s `*args` parameter is refused at
# the call (BUGS.md#ctor-varargs-construction-unlowered); the declaration is valid.
from tpy import int32


class P:
    n: int32

    def __init__(self, *xs: int32) -> None:
        self.n = len(xs)


def main() -> None:
    print(P(1, 2).n)  # tpyc: error(/constructing 'P' through its \*args parameter is not supported yet/)


main()
