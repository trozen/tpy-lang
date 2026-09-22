# A BORROW-returning free call written into a container FIELD takes the same
# row as its record twin: the field owns its storage, so the write COPIES the
# borrowed list and says so -- the same warning `self.m = other.m` carries for
# a record field. Copy semantics are intended here (the field cannot alias
# another object's storage); the source is read back only through its own
# owner so the copy is not observed.
from tpy import int32


class Holder:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def peek(self) -> list[int32]:
        return self.items


def first_of(h: Holder) -> list[int32]:
    return h.items


class Sink:
    data: list[int32]

    def __init__(self) -> None:
        self.data = []

    def fill(self, h: Holder) -> None:
        self.data = first_of(h)  # tpyc: warning(/copies list.* into field; use copy/)


def main() -> None:
    s = Sink()
    h = Holder()
    h.items.append(42)
    s.fill(h)
    s.data.append(7)
    print(len(s.data), s.data[0])


main()
