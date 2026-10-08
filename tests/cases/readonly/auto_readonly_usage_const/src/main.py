# @auto_readonly usage-dependent receiver const-ness: a param read only through an
# accessor keeps a const receiver; a mutation through the result demotes it.
from tpy import int32, auto_readonly
from tplib.box import Box


class Inner:
    v: int32

    def __init__(self, v: int32):
        self.v = v

    def bump(self):
        self.v += 1


class Cell:
    # User-defined accessor (not a tplib type): borrowing get + value-returning peek.
    inner: Inner

    def __init__(self, v: int32):
        self.inner = Inner(v)

    @auto_readonly
    def get(self) -> auto_readonly[Inner]:
        return self.inner

    @auto_readonly
    def peek(self) -> int32:   # value return: copy, never roots mutation to receiver
        return self.inner.v


class Outer:
    b: Box[Inner]
    items: list[Inner]

    def __init__(self, v: int32):
        self.b = Box(Inner(v))
        self.items = [Inner(v)]


def read_through_get(o: Outer) -> int32:   # const Outer&
    return o.b.get().v


def write_through_get(o: Outer):           # Outer&
    o.b.get().v = 9


def mutate_through_get(o: Outer):          # Outer&
    o.b.get().bump()


def aug_through_get(o: Outer):             # Outer&
    o.b.get().v += 1


def alias_read(o: Outer) -> int32:         # const Outer&: the alias is never written
    x = o.b.get()                          # through, so its loan on the receiver credits
    return x.v                             # nothing and the local binds const


def alias_write(o: Outer):                 # Outer&
    x = o.b.get()
    x.v = 7


def elem_alias_write(o: Outer):            # Outer&: field-path element-borrow alias write
    e = o.items[0]
    e.v = 3


def read_user_accessor(c: Cell) -> int32:  # const Cell&
    return c.get().v


def write_user_accessor(c: Cell):          # Cell&
    c.get().v = 8


def peek_user_accessor(c: Cell) -> int32:  # const Cell&: value return, only read
    return c.peek()


def sum_boxes(boxes: list[Box[Inner]]) -> int32:   # const list&: loop var read through accessor
    total = 0
    for b in boxes:
        total += b.get().v
    return total


def main():
    o = Outer(5)
    print(read_through_get(o))
    write_through_get(o)
    print(read_through_get(o))
    mutate_through_get(o)
    aug_through_get(o)
    print(read_through_get(o))
    alias_write(o)
    print(alias_read(o))
    elem_alias_write(o)
    print(o.items[0].v)

    c = Cell(2)
    print(read_user_accessor(c))
    write_user_accessor(c)
    print(peek_user_accessor(c))

    boxes = [Box(Inner(1)), Box(Inner(2)), Box(Inner(3))]
    print(sum_boxes(boxes))


main()
