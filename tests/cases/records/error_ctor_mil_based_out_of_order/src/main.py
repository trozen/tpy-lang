# A record WITH BASES keeps its declared field order -- the reorder into
# `__init__` assignment order only runs for a base-less record -- so member
# inits run b-then-a here while the constructor assigns a-then-b. `b`'s
# initializer would read `a` before `a` has a value, so it is rejected.
from tpy import int32


class Base:
    tag: int32

    def __init__(self) -> None:
        self.tag = int32(1)


class Derived(Base):
    b: int32
    a: int32

    def __init__(self) -> None:
        super().__init__()
        self.a = int32(5)
        # The subject: `b` is laid out BEFORE `a`, so its init runs first.
        self.b = self.a + int32(1)  # tpyc: error(/ctor\.mil_reads_unready_field/)


def main() -> None:
    d = Derived()
    print(d.a, d.b)


main()
