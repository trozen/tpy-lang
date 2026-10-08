# The declared return type is the contract: an explicit @readonly method
# returning its own storage must declare a readonly return.
from tpy import int32, readonly


class A:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class M:
    own: A

    def __init__(self) -> None:
        self.own = A()

    @readonly
    def get_own(self) -> A:
        return self.own  # tpyc: error(/Cannot return readonly\[A\] at mutable return type 'A'; declare the return as 'readonly\[A\]'/)


def main() -> None:
    m = M()
    print(m.get_own().n)


main()
