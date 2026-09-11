# A container ELEMENT read at an `Own[record]` constructor slot: the element
# cannot move out of its list, so the slot keeps rejecting (sema warns the
# copy) while the borrow-slot twin lowers (record_elem_subscript_arg_sinks).
from tpy import Int32, Own


class Thing:
    x: float

    def __init__(self, x: Int32) -> None:
        self.x = float(x)


class Keep:
    t: Thing

    def __init__(self, t: Own[Thing]) -> None:
        self.t = t


def main() -> None:
    things = [Thing(4), Thing(5)]
    k = Keep(things[0])  # tpyc: error(/not yet supported/)
    k.t.x += 1.0
    print(k.t.x, things[0].x)


main()
