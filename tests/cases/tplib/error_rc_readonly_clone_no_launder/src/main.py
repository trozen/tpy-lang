# A readonly Rc clone yields a readonly-payload handle -- mutating through it
# is rejected (readonly is not laundered into mutable access).
from tpy import int32, readonly
from tplib import Rc


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bad(r: readonly[Rc[Counter]]) -> None:
    c = r.clone()
    c.get().n = 5      # tpyc: error(/readonly|const|Cannot mutate/)


def main() -> None:
    pass


main()
