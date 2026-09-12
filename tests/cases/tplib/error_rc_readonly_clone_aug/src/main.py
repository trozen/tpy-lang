# A readonly Rc clone has a readonly payload -- augmented-assign through the
# auto-__deref__ (c.n += 1) is rejected, like plain assign.
from tpy import int32, readonly
from tplib import Rc


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bad(r: readonly[Rc[Counter]]) -> None:
    c = r.clone()
    c.n += 1     # tpyc: error(/readonly|const|Cannot mutate/)


def main() -> None:
    pass


main()
