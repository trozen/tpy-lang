# Cycle peer naming the entry module's enum.
from tpy import int32
import main  # importing the entry module back closes the cycle  # tpyc: error(/module 'main' is the program's entry point and cannot be part of an import cycle \(imported here by 'a_user'\)/)


class C:
    e: main.E = main.E.B

    def __init__(self, n: int32) -> None:
        self.n = n


def pick() -> main.E:
    return main.E.A
