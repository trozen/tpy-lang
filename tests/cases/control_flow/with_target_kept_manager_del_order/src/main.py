# Pins the acknowledged `__del__`-ordering divergence for a KEPT manager. When
# `__enter__` lends the manager's own storage and the target is read after the
# statement, TPy has to keep the manager alive -- the lent object lives inside it
# -- so its `__del__` runs at the end of the enclosing scope. CPython drops the
# manager at the end of the `with`, because there the target holds an independent
# reference to what `__enter__` returned.
from tpy import Int32


class Item:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n


class Owner:
    item: Item

    def __init__(self, n: Int32):
        self.item = Item(n)

    def __enter__(self) -> Item:
        return self.item

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        print("owner dropped")


def run(flag: bool) -> Int32:
    if flag:
        with Owner(5) as it:
            pass
    else:
        with Owner(9) as it:
            pass
    # The read that forces the manager to be kept; CPython has already printed
    # "owner dropped" by this point, TPy prints it when `run` returns.
    print("reading:", it.n)
    return it.n


def main() -> None:
    print(run(True))


main()
