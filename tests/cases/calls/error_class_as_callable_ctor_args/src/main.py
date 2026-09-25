# A class name at a Callable slot calls the constructor with the slot's
# arguments; a constructor needing more is rejected in terms of the class name.
from typing import Callable
from tpy import int32


class Q:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def build(f: Callable[[], Q]) -> int32:
    return f().v


def main() -> None:
    print(build(Q))  # tpyc: error(/'Q' used as a 'Callable\[\[\], Q\]': 'Q\(\)' expects 1 argument/)


main()
