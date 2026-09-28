# Args passed through an `__init__`-less parent whose own field has no
# default are rejected, naming it (BUGS.md#init-forwarding-no-default-field).
from tpy import int32


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


class B(A):
    y: int32


class C(B):
    def __init__(self) -> None:
        super().__init__(5)  # tpyc: error(/'B' has no '__init__' and its field 'y' has no default, so it cannot pass arguments on to 'A.__init__'; give 'B' an '__init__' or a default for 'y'/)
        self.y = 1


def main() -> None:
    print(C().x)


main()
