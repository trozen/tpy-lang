# A self-capturing lambda at a Callable slot in a method returning a borrow of
# self is refused: self is a capture, not an argument.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Box:
    p: P

    def __init__(self) -> None:
        self.p = P(1)

    def getter(self) -> None:
        # The subject: the borrow points into self.
        s: Callable[[], P] = lambda: self.p  # tpyc: error(/this lambda's result points into 'self', which it captures/)
        print(s().n)


Box().getter()
