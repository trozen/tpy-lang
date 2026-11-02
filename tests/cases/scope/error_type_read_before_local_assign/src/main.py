# A class name a function later rebinds to a value is that function's local
# for its whole body: constructing through it before the binding is CPython's
# UnboundLocalError, never a construction of the module class.
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    # The type head the later `P = 3` makes local.
    a = P(1)  # tpyc: error(/'P' is read before it is bound on line 15: a name bound anywhere in a function is local to the whole function, so this read does not see the module's 'P'; rename one of them/)
    P = 3
    print(a.n, P)


main()
