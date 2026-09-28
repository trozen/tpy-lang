# Two `__init__`-less levels, the lower one blocked by a no-default field:
# names the upper level's reason (BUGS.md#init-forwarding-no-default-field).
from tpy import int32


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


class M(A):
    y: int32


class B(M):
    pass


class C(B):
    def __init__(self) -> None:
        super().__init__(5)  # tpyc: error(/'B' has no '__init__' and its base 'M' has no '__init__' either, so it cannot pass arguments on to 'A.__init__'; give 'B' an '__init__'/)
        self.y = 1


def main() -> None:
    print(C().x)


main()
