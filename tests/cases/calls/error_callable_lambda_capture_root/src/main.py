# A lambda at a Callable slot returning a borrow of a capture is refused: a
# Callable's borrow result may point into its arguments only.
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
    c: Callable[[], P] = lambda: h  # tpyc: error(/this lambda's result points into 'h', which it captures, not into an argument/)
    print(c().n)


main()
