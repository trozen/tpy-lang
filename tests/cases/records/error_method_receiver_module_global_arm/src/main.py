# A ternary that selects a method RECEIVER: a module GLOBAL arm is not a
# declared local, which the receiver guard requires, so this is rejected today.
from tpy import Int32


class Shape:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def area(self) -> Int32:
        return self.n


G = Shape(7)


def main() -> None:
    b = Shape(2)
    c = True
    print((G if c else b).area())  # tpyc: error(/method.recv.other/)


main()
