# A nullable owned-record tuple binding holds its records by value, so
# binding it whole to a second local would copy them where Python shares
# them (`u[0].n = 9` would not reach `t`): refused until the local alias
# renders. A call result binds (tuple/optional_tuple_return).
from tpy import int32, Own


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def owned(k: bool) -> tuple[Own[Box], int32] | None:
    if k:
        return None
    return (Box(1), 2)


def main() -> None:
    t = owned(False)
    u = t  # tpyc: error(/decl.slot_type/)
    if u is not None:
        u[0].n = 9
    if t is not None:
        print(t[0].n)


main()
