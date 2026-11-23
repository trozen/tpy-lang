# Error: LANGUAGE_FEATURES "A `Callable`'s borrowed result points into its
# arguments only", per instantiation of an open `T`: refused where it is a borrow.
from typing import Callable
from tpy import int32, readonly


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mkf[T](x: T) -> Callable[[], T]:
    return lambda: x  # tpyc: warning(/lambda at line 15 may keep its own copy of 'x'/)


# a `readonly[T]` result is a borrow at a reference `T` too
def mkr[T](x: T) -> Callable[[], readonly[T]]:
    return lambda: x  # tpyc: warning(/lambda at line 20 may keep its own copy of 'x'/)


class Keep[T]:
    def __init__(self) -> None:
        pass

    # the result names the class's `T`, the parameters the method's `U`
    def get_f[U](self, u: U, x: T) -> Callable[[list[U]], T]:
        return lambda xs: x  # tpyc: warning(/lambda at line 29 may keep its own copy of 'x'/)


def main() -> None:
    g = mkf(P(20))  # tpyc: error(/this lambda's result points into 'x', which it captures.*declare Callable\[\[\], Own\[T\]\] to return a copy \(the lambda in 'mkf' \(line 15\), instantiated here with a result of 'P'\)/)
    print(g().n)
    r = mkr(P(21))  # tpyc: error(/the lambda in 'mkr' \(line 20\), instantiated here with a result of 'readonly\[P\]'/)
    print(r().n)
    k = Keep[P]()  # tpyc: error(/the lambda in 'Keep.get_f' \(main.py:29\), instantiated here with a result of 'P'/)
    f = k.get_f(1, P(22))
    print(f([1]).n)


main()
