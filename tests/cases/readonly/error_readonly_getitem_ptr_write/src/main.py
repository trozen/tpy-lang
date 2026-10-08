# A subscript on a readonly receiver runs the const clone of an @auto_readonly
# __getitem__, so the Ptr it lends points at readonly storage: a write through
# it is rejected, not left to fail in C++.
from tpy import Ptr, int32, auto_readonly, readonly


class Item:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class Rows:
    p: Ptr[Item]

    def __init__(self, p: Ptr[Item]) -> None:
        self.p = p

    @auto_readonly
    def __getitem__(self, i: int32) -> Ptr[auto_readonly[Item]]:
        return self.p


def write(r: readonly[Rows]) -> None:
    q = r[0]
    q.n = 5  # tpyc: error(/Cannot assign through read-only pointer/)


def main() -> None:
    it = Item()
    write(Rows(it))


main()
