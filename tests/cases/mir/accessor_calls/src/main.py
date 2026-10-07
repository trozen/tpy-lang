# MIR pins for property accessors and @auto_readonly twins as user calls: bodies, results
# rooted in a receiver field, callers per result kind, conflicts and kept refusals.
from typing import Protocol
from tpy import int32, readonly, auto_readonly, dynamic, StrView


class Inner:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Counter:
    n: int32
    name: str
    items: list[int32]

    def __init__(self, name: str) -> None:
        self.n = 0
        self.name = name
        self.items = []

    # getter body: a scalar field returned
    @property
    def count(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        return self.n

    # setter body: a scalar field written through the receiver
    @count.setter
    def count(self, v: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n = v

    # getter body: an owned-leaf field returned by value (a str copy)
    @property
    def label(self) -> str:  # tpyc: mir(covered) mir_summary(known)
        return self.name  # tpyc: warning(/returns a copy of str field/)

    # setter body: a str (view) parameter stored into an owned-leaf field
    @label.setter
    def label(self, v: str) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.name = v

    # getter body: a container field returned by reference (origin param0.items); the def
    # emits a mutable and a const body, each pinned
    @property
    def elems(self) -> list[int32]:  # tpyc: mir(covered) mir_summary(known)
        return self.items

    # getter body: a view of an owned-leaf field returned (origin param0.name)
    @property
    def tag(self) -> StrView:  # tpyc: mir(covered) mir_summary(known)
        return self.name

    # @auto_readonly body: the receiver returned; one callable whose result has the
    # receiver's access at the call
    @auto_readonly
    def me(self) -> "Counter":  # tpyc: mir(covered) mir_summary(known)
        return self

    # @auto_readonly body: a twin calling a twin -- both clone bodies must summarize alike
    @auto_readonly
    def via(self) -> "Counter":  # tpyc: mir(covered) mir_summary(known)
        return self.me()

    # @auto_readonly body: a container field returned (origin param0.items in both clones)
    @auto_readonly
    def view_items(self) -> list[int32]:  # tpyc: mir(covered) mir_summary(known)
        return self.items

    # method body: a container field grown
    def push(self, v: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.items.append(v)

    # method body: an owned-leaf field replaced
    def rename(self, v: str) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.name = v


class Holder:
    inner: Inner
    k: int32

    def __init__(self, inner: Inner) -> None:
        self.inner = inner  # tpyc: warning(/copies Inner into field/)
        self.k = 1

    # getter body: an inline record field returned by reference (origin param0.inner); the
    # record's definition composes its member record's
    @property
    def part(self) -> Inner:  # tpyc: mir(covered) mir_summary(known)
        return self.inner

    # setter body: a record parameter (expanded to Own[Inner]) moved into a field
    @part.setter
    def part(self, v: Inner) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.inner = v


# free caller: a scalar getter read and a setter write (a scalar field written through a
# call publishes no line write fact)
def scalar_accessors(c: Counter) -> int32:  # tpyc: mir(covered)
    c.count = c.count + 2
    return c.count


# free caller: an owned-leaf getter and a str setter
def leaf_accessors(c: Counter) -> str:  # tpyc: mir(covered)
    s = c.label
    c.label = "renamed"
    return s


# free caller: a getter read on a readonly receiver (a readonly result)
def readonly_getter(c: readonly[Counter]) -> int32:  # tpyc: mir(covered)
    return c.count


# free conflict: a container getter's result iterated while the field grows through a method
def grow_under_iter(c: Counter) -> int32:  # tpyc: mir(conflict /replacement/)
    total = 0
    for x in c.elems:
        if x > 100:
            c.push(x)  # tpyc: warning(/while iterating over it/)
        total += x
    return total


# free safe sibling: a container getter's result bound, the field grown, then the alias
# written -- the list object persists, so the write shows through the record; sema warns
# although the alias stays valid (BUGS.md#getter-borrow-whole-record-false-positive)
def alias_survives_growth(c: Counter) -> int32:  # tpyc: mir(covered)
    xs = c.elems  # tpyc: mir_borrows(xs, c.items)
    c.push(5)  # tpyc: mir_write(c.items[structure]) warning(/while borrowed/)
    xs[0] = xs[0] + 40
    return xs[0]


# free conflict: a view of a field from a getter, the field replaced through a method;
# sema is silent (BUGS.md#field-loan-whole-record-callee-unchecked)
def view_then_rename(c: Counter) -> int:  # tpyc: mir(conflict /replacement/)
    t: StrView = c.tag  # tpyc: mir_borrows(t, c.name)
    c.rename("zz")
    return len(t)


# free safe sibling: a view of a field from a getter, a sibling scalar field written; sema
# warns although the view stays valid (BUGS.md#getter-borrow-whole-record-false-positive)
def view_then_sibling(c: Counter) -> int:  # tpyc: mir(covered)
    t: StrView = c.tag
    c.count = 4  # tpyc: warning(/while borrowed/)
    return len(t)


# free safe sibling: the unannotated binding of a view getter owns a copy (the view
# rule), so the rename cannot reach it
def copy_then_rename(c: Counter) -> int:  # tpyc: mir(covered)
    t = c.tag
    c.rename("yy")
    return len(t)


# free caller: a twin on a mutable receiver, written through the result
def twin_mutable(c: Counter) -> int32:  # tpyc: mir(certified)
    d = c.me()  # tpyc: mir_borrows(d, c)
    d.count = 7
    return d.count


# free caller: a twin on a readonly receiver, read through the result
def twin_readonly(c: readonly[Counter]) -> int32:  # tpyc: mir(certified)
    d = c.me()
    return d.count


# kept refusal: a call result as a receiver (receivers beyond a name or self)
def twin_via(c: Counter) -> int32:  # tpyc: mir(uncovered /^call needs borrowed record name$/)
    return c.via().count


# free conflict: a twin's container result iterated while the field grows through a method
def twin_items_grow(c: Counter) -> int32:  # tpyc: mir(conflict /replacement/)
    total = 0
    for x in c.view_items():
        if x > 100:
            c.push(x)  # tpyc: warning(/while iterating over it/)
        total += x
    return total


# free caller: a twin's container result on a mutable receiver, written through; the
# write lands on c.items
def twin_items_write(c: Counter) -> int32:  # tpyc: mir(covered)
    ys = c.view_items()  # tpyc: mir_borrows(ys, c.items)
    ys[0] += 1  # tpyc: mir_write(ys[elements])
    return ys[0]


class Wrap:
    c: Counter

    def __init__(self, c: Counter) -> None:
        self.c = c


# free caller: a getter read through an inline record field receiver (a member holder)
def getter_through_field(w: Wrap) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return w.c.count


class Driver:
    # method caller: a getter read on a record parameter
    def run(self, c: Counter) -> int32:  # tpyc: mir(covered)
        return c.count


# free caller: a getter result as a write receiver -- the result origin names the field
# place, so the stub write lands on c.items
def write_through_getter(c: Counter) -> int:  # tpyc: mir(covered)
    c.elems.append(3)  # tpyc: mir_write(c.items[structure])
    return len(c.items)


# free caller: an inline record getter and a record setter, both summarized; kept refusal:
# the last line reads a field of a getter CALL result, a receiver with no field identity
def record_accessors(h: Holder) -> int32:  # tpyc: mir(uncovered /^reference needs local name$/)
    p = h.part
    p.x += 10
    seen = h.inner.x
    # `p` is dead once the argument is evaluated; the borrow warning is the whole-record
    # false positive (BUGS.md#getter-borrow-whole-record-false-positive)
    h.part = Inner(p.x + 1)  # tpyc: warning(/while borrowed/)
    return seen * 100 + h.part.x


@dynamic
class Shape(Protocol):
    def grow(self) -> None: ...


class Base(Shape):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def grow(self) -> None:
        self.n += 1

    @property
    def size(self) -> int32:
        return self.n


# kept refusal: a getter on a record inheriting a @dynamic protocol
def call_virtual_getter(b: Base) -> int32:  # tpyc: mir(uncovered /^unsupported expression$/)
    return b.size


def main() -> None:
    c = Counter("c")
    c.push(1)
    c.push(2)
    print("scalar_accessors:", scalar_accessors(c), c.n)
    print("leaf_accessors:", leaf_accessors(c), c.label)
    print("readonly_getter:", readonly_getter(c))
    print("grow_under_iter:", grow_under_iter(c), len(c.items))
    print("alias_survives_growth:", alias_survives_growth(c), c.items[0], len(c.items))
    print("view_then_rename:", view_then_rename(c), c.name)
    print("view_then_sibling:", view_then_sibling(c), c.n)
    print("copy_then_rename:", copy_then_rename(c), c.name)
    print("twin_mutable:", twin_mutable(c), c.n)
    print("twin_readonly:", twin_readonly(c))
    print("twin_via:", twin_via(c))
    print("twin_items_grow:", twin_items_grow(c), len(c.items))
    print("twin_items_write:", twin_items_write(c), c.items[0])
    print("Driver.run:", Driver().run(c))
    w = Wrap(c)
    print("getter_through_field:", getter_through_field(w))
    print("write_through_getter:", write_through_getter(c), len(c.items))
    h = Holder(Inner(1))
    print("record_accessors:", record_accessors(h), h.inner.x)
    b = Base()
    b.grow()
    print("call_virtual_getter:", call_virtual_getter(b))


main()
