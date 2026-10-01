# A @nocopy tplib Box local moved into a Box field at its last use: the slot
# no render family claims takes the one source the lowered node vouches for
# (movable, last use). The consuming method's receiver is such a source too:
# stored into a field it relocates, as CPython hands over the very object.
from typing import Self
from tplib import Box
from tpy import Own, int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Cell:
    f: Box[P]

    def __init__(self) -> None:
        self.f = Box(P(1))


def free_move(v: int32) -> None:
    # free function: a local holder's Box field from a Box local.
    s = Box(P(v))
    h = Cell()
    h.f = s  # tpyc: ok
    h.f.get().v += 1
    print("free.boxed_local_move", h.f.get().v)


class InCtor:
    tag: int32

    def __init__(self, v: int32) -> None:
        # constructor body: the same write into a local holder.
        self.tag = 0
        s = Box(P(v))
        h = Cell()
        h.f = s  # tpyc: ok
        h.f.get().v += 1
        self.tag = h.f.get().v


class Ticket:
    id: int32

    def __init__(self, id: int32) -> None:
        self.id = id

    def stash(self: Own[Self], s: "TicketStash") -> None:
        # consuming method: `self` stored into a field moves the receiver.
        s.t = self  # tpyc: ok


class TicketStash:
    t: Ticket

    def __init__(self) -> None:
        self.t = Ticket(0)


def consuming_store() -> None:
    st = TicketStash()
    Ticket(42).stash(st)
    st.t.id += 1
    print("consuming.self_into_field", st.t.id)


def main() -> None:
    free_move(5)
    print("ctor.boxed_local_move", InCtor(6).tag)
    consuming_store()


main()
