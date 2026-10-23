# `del r[k]` on a class with no `__delitem__` says how to enable it.
from tpy import int32


class Table:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __getitem__(self, k: int32) -> int32:
        return self.n + k


def main() -> None:
    t = Table()
    print(t[1])
    del t[0]  # tpyc: error(/Type 'Table' does not support item deletion/)


main()
