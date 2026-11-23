# An @auto_readonly accessor reached through a base-part call keeps its
# receiver's access, as through self: inferred-const methods bind it const.
from tpy import int32, auto_readonly


class Cell:
    v: int32

    def __init__(self) -> None:
        self.v = 7


class Store:
    cell: Cell

    def __init__(self) -> None:
        self.cell = Cell()

    @auto_readonly
    def get(self) -> auto_readonly[Cell]:
        return self.cell


class Child(Store):
    def via_self(self) -> int32:
        p = self.get()  # tpyc: ok
        return p.v

    def via_super(self) -> int32:
        p = super().get()  # tpyc: ok
        return p.v

    def via_base(self) -> int32:
        p = Store.get(self)  # tpyc: ok
        return p.v


def main() -> None:
    s = Child()
    # mutating through the mutable accessor after the const reads
    s.get().v = 8
    print("base call:", s.via_self(), s.via_super(), s.via_base())
