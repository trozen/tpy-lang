# A Box[readonly[T]] handle has a readonly payload -- deref field-assign is
# rejected, same as Rc: the check is on the deref target, not the handle.
from tpy import int32, readonly
from tplib import Box


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bad(b: Box[readonly[Counter]]) -> None:
    b.n = 5      # tpyc: error(/readonly|const|Cannot mutate/)


def main() -> None:
    pass


main()
