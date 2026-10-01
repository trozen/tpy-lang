# A view-holding union field set from another object's str field: nothing
# proves that object outlives the holder, so the view fence rejects it
# (BUGS.md#field-sink-remaining-rejects).
from tpy import StrView, int32


class Obj:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Label:
    view: StrView | int32

    def __init__(self) -> None:
        self.view = "x"

    def mark(self, obj: Obj) -> None:
        # The view member would point into `obj`.
        self.view = obj.name  # tpyc: error(/field_write\.lift\.storage/)


def main() -> None:
    lb = Label()
    lb.mark(Obj("abc"))
    print(lb.view)


main()
