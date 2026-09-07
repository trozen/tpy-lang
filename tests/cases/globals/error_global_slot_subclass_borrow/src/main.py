# A global slot initialized from a borrow whose type differs from the slot: the
# retype is the polymorphic arm's business, so the exact-type row rejects.
from tpy import Int32


class Base:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Sub(Base):
    def __init__(self) -> None:
        Base.__init__(self, 1)


class Holder:
    s: Sub

    def __init__(self, s: Sub) -> None:
        self.s = s

    def get(self) -> Sub:
        return self.s


h = Holder(Sub())
g: Base = h.get()  # tpyc: error(/top_level.global_slot_shape/)
print(g.n)
