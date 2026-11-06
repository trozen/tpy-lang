# A sibling nested def that returns its own capture by reference, the shape a
# lambda at a Callable slot would lend through (`lambda: get()`), is refused
# at the def's own return: a nested def's borrow result may not point into
# what it captures either.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    h = P(1)

    def get() -> P:
        # The subject: get() lends its capture, refused before the lambda.
        return h  # tpyc: error(/Cannot return local or temporary as reference/)
    c: Callable[[], P] = lambda: get()
    print(c().n)


main()
