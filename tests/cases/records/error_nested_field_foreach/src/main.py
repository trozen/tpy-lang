# A for-each over a NESTED field chain (`self.inner.xs`): the field-receiver
# row admits a bare name receiver only, so this nested chain is rejected today.
from tpy import Int32


class Inner:
    xs: list[Int32]

    def __init__(self) -> None:
        self.xs = [1, 2]


class Outer:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner()

    def total(self) -> Int32:
        n = 0
        for x in self.inner.xs:  # tpyc: error(/foreach.field_parent/)
            n = n + x
        return n


def main() -> None:
    print(Outer().total())


main()
