# An owning def at a generic class's `Callable[[], T]` constructor parameter
# is refused, naming the parameter and the `Callable[[], Own[T]]` spelling
# (LANGUAGE_FEATURES.md, the Restriction on a generic class's `Callable[[], V]`
# member).
from typing import Callable
from tpy import Own, int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> Own[P]:
    return P(2)


class Lazy[T]:
    f: Callable[[], T]

    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f

    def make(self) -> T:
        return self.f()


def main() -> None:
    # the subject: a fresh P where Lazy[P]'s callable lends one
    print(Lazy[P](mk).make().n)  # tpyc: error(/'mk' returns Own\[P\], but parameter 'f' of Lazy.__init__ \(Callable\[\[\], T\]\) returns a borrow; declare parameter 'f' of Lazy.__init__ as Callable\[\[\], Own\[T\]\]/)


main()
