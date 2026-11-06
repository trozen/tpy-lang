# A lambda passed to an Fn parameter captures by reference, so a call through
# it may hand back a borrow of a local it captures: returning that from a
# `-> P` function is the local-borrow error, as for the def twin.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", "A callable's return
# reads as a def's" (the call lends each argument, the callable at an Fn
# parameter too).
from tpy import Fn, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    p: P

    def __init__(self) -> None:
        self.p = P(1)


def ret(f: Fn[[P], P], x: P) -> P:
    return f(x)


def pick(x: P) -> P:
    local_h = H()
    # The subject: the result may point into local_h, which dies here.
    return ret(lambda p: local_h.p, x)  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(pick(P(2)).n)


main()
