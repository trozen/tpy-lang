# Writing a freshly built record into an Optional field of a list element
# (`self.slots[i].opt = Inner(1)`): not lowered yet, so this pins the reject.
from tpy import int32


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Slot:
    opt: Inner | None
    n: int32

    def __init__(self) -> None:
        self.opt = None
        self.n = 0


class Holder:
    slots: list[Slot]

    def __init__(self) -> None:
        self.slots = [Slot()]


    def clear(self, i: int32) -> bool:
        # Only a None source got the subscript receiver; a record source
        # carries a move/copy verdict this row does not spell.
        self.slots[i].opt = Inner(1)  # tpyc: error(/assign.field_write_shape/)
        return True


def main() -> None:
    h = Holder()
    print(h.clear(0))


main()
