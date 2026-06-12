# The owned `Foo | None`-local move-out is gated on LAST use: a store at a
# non-last use copies (not moves), so a @nocopy local is rejected there.
from tpy import nocopy


@nocopy
class Foo:
    v: int

    def __init__(self, v: int):
        self.v = v


class Holder:
    slot: Foo | None

    def __init__(self):
        self.slot = None


def main() -> None:
    h = Holder()
    p: Foo | None = Foo(3)
    h.slot = p  # tpyc: error(/cannot copy Foo into field/)
    if p is not None:
        print(p.v)


main()
