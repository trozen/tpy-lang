# Returned borrows whose storage outlives the call, as the durability rule
# reads them (LANGUAGE_FEATURES "Owned Return Values").
from enum import Enum
from tpy import StrView, readonly


class Node:
    def __init__(self, v: int) -> None:
        self.v = v


# self_loop: a loop variable over self's field is self's storage.
class Bag:
    items: list[Node]

    def __init__(self) -> None:
        self.items = [Node(1)]

    def first(self) -> Node:
        for x in self.items:
            return x  # tpyc: ok
        return self.items[0]


def self_loop() -> None:
    b = Bag()
    b.first().v = 2  # the write reaches the bag's element
    print("self_loop", b.items[0].v)


# enum_name_view: an enum member's `.name` is static storage.
class Color(Enum):
    RED = 1


def red_name() -> StrView:
    return Color.RED.name  # tpyc: ok


def enum_name_view() -> None:
    print("enum_name_view", red_name())


# enter_through_method: `__enter__` lends self's field through a call, so
# a manager the target outlives is kept alive with it.
class Picker:
    def pick(self, n: Node) -> Node:
        return n


PICK = Picker()


class Mgr:
    item: Node

    def __init__(self, v: int) -> None:
        self.item = Node(v)

    def __enter__(self) -> Node:
        return PICK.pick(self.item)  # tpyc: ok

    def __exit__(self, et, ev, tb) -> None:
        self.item.v += 100  # a manager-side write the target must see


def enter_through_method() -> None:
    for k in range(1):
        with Mgr(22 + k) as view:
            pass
    view.v += 1  # a write through the target after the block
    print("enter_through_method", view.v)


# pack_from_params: a `*args` pack of durable parameters lends them (bound
# first: a pack call returned directly has no lowering yet,
# BUGS.md#vararg-call-in-str-concat-rejects).
def first(*xs: readonly[Node]) -> readonly[Node]:
    return xs[0]


def pack_first(a: readonly[Node], b: readonly[Node]) -> readonly[Node]:
    r = first(a, b)
    return r  # tpyc: ok


def pack_from_params() -> None:
    a = Node(3)
    b = Node(4)
    print("pack_from_params", pack_first(a, b) is a)


# view_ctor_through_call: a call returning its view argument lends the view
# constructor's argument, so the later write to that field warns. The loan
# does not end at v's last read: the direct `StrView(h.s)` form warns the same.
class Holder:
    def __init__(self, s: str) -> None:
        self.s = s


def ident_view(s: StrView) -> StrView:
    return s


def view_ctor_through_call() -> None:
    h = Holder("abc")
    v: StrView = ident_view(StrView(h.s))
    print("view_ctor_through_call", v)
    h.s = "xyz"  # tpyc: warning(/while borrowed/)
    print("view_ctor_through_call", h.s)


def main() -> None:
    self_loop()
    enum_name_view()
    enter_through_method()
    pack_from_params()
    view_ctor_through_call()


main()
