# `with g:` on a module-global context manager (a global renders as a
# `CM*` pointer slot, so the manager bind must deref it). The manager
# mutates its own state inside the with; observing the count through the
# global afterward forces the borrow -- a silent copy would lose it.
from tpy import Int32


class Counter:
    opens: Int32

    def __init__(self) -> None:
        self.opens = 0

    def __enter__(self) -> Int32:
        self.opens += 1
        return self.opens

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


g = Counter()


def main() -> None:
    with g as n1:
        print(n1)
    with g as n2:
        print(n2)
    print(g.opens)


main()
