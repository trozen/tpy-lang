# A call-result borrow through a cycle peer, in both members: ba's use
# calls pick_bb (analyzed later), bb's use calls pick_ba (earlier).
from tpy import int32, Own
from item import Bag, Item


def pick_ba(xs: list[Item]) -> Item:
    return xs[0]


def sink(xs: Own[list[Item]]) -> int32:
    bag = Bag(xs)
    return len(bag.items)


def use() -> int32:
    xs = [Item(1), Item(2)]
    it = pick_bb(xs)
    # `it` still borrows xs, so xs is copied rather than moved out.
    k = sink(xs)  # tpyc: warning(/copies list\[Item\] into owned storage/)
    it.n += 100
    return it.n + k


# At the bottom so CPython, importing ba first, has pick_ba defined when
# bb's top-level import of it runs.
from bb import pick_bb  # noqa: E402  # tpyc: warning(/Import statement should be at the top/)
