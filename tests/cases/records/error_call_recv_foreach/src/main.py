# A CALL is not a chain hop: the call result is a temporary, so the loop's
# capture would alias an object that dies at the end of the for-head. The
# same rule admits an accessor that hands back a reference (a `@property` or
# `__getitem__` declared `-> T`), and rejects its `-> Own[T]` sibling.
# BUGS.md#chain-temporary-hop-rejected; binding the call result to a local
# first is the workaround.
from tpy import int32


class Inner:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]


class Outer:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner()

    def get(self) -> Inner:
        return self.inner

    def total(self) -> int32:
        n = 0
        for x in self.get().xs:  # tpyc: error(/foreach.field_parent/)
            n = n + x
        return n


def main() -> None:
    print(Outer().total())


main()
