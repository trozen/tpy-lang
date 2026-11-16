# Lending sources backed by storage other than a module global: a call
# argument, a `Ptr` target, the receiver's own field, a native-container
# subclass.
from typing import Iterator
from tpy import Own, Ptr, int32
from fixtures import Pt, bump, bump_pt, passthru


class Rows:
    items: list[list[int32]]

    def __init__(self) -> None:
        self.items = [[1], [2]]


class PView:
    rows: Ptr[Rows]

    def __init__(self, rows: Ptr[Rows]) -> None:
        self.rows = rows

    def __iter__(self) -> Iterator[list[int32]]:
        yield self.rows.items[0]
        yield self.rows.items[1]


def view_of(rows: Ptr[Rows]) -> Own[PView]:
    return PView(rows)


class Own2:
    items: list[list[int32]]

    def __init__(self) -> None:
        self.items = [[1], [2]]

    def __iter__(self) -> Iterator[list[int32]]:
        for x in self.items:
            yield x


class PList(list[Pt]):
    pass


def main() -> None:
    # a generic generator declared `Iterator[T]` lends (a copying body is an
    # error there), so a named argument's elements copy at the owning slot
    cg: list[list[int32]] = [[15]]
    bump(passthru(cg))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free copying generic", cg)
    rows = Rows()
    bump(view_of(rows))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free Own factory", rows.items)
    # a record iterating its own field, temporary or dead: the copy is real
    # (the step is lent) though nothing can observe it
    bump(Own2())  # tpyc: warning(/copies list\[int32\] elements/)
    o = Own2()
    bump(o)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free own field")
    q = Pt(1)
    pl = PList()
    # q is read below, so the append rightly copies it (warned)
    pl.append(q)
    # a dead native-container subclass still copies silently
    # (BUGS.md#dead-native-like-source-own-slot-copies)
    bump_pt(pl)  # tpyc: ok
    print("free PList", q.v)
