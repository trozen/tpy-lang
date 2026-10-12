from tpy import int32, Own
from ba import pick_ba
from item import Bag, Item


def pick_bb(xs: list[Item]) -> Item:
    return xs[0]


def sink(xs: Own[list[Item]]) -> int32:
    bag = Bag(xs)
    return len(bag.items)


def use() -> int32:
    xs = [Item(3), Item(4)]
    it = pick_ba(xs)
    # `it` still borrows xs, so xs is copied rather than moved out.
    k = sink(xs)  # tpyc: warning(/copies list\[Item\] into owned storage/)
    it.n += 100
    return it.n + k
