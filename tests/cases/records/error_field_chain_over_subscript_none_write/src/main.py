# Writing `None` through a field chain rooted at a list subscript
# (`self.slots[i].mid.opt = None`): not lowered yet, so this pins the reject.
from tpy import Int32


class Inner:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Mid:
    opt: Inner | None

    def __init__(self) -> None:
        self.opt = None


class Slot:
    mid: Mid

    def __init__(self) -> None:
        self.mid = Mid()


class Holder:
    slots: list[Slot]

    def __init__(self) -> None:
        self.slots = [Slot()]


    def clear(self, i: Int32) -> bool:
        # A field receiver whose own receiver is the element -- a CHAIN, not
        # the plain subscript receiver the None-write row names.
        self.slots[i].mid.opt = None  # tpyc: error(/assign.field_write_shape/)
        return True


def main() -> None:
    h = Holder()
    print(h.clear(0))


main()
