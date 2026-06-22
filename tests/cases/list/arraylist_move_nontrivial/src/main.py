# ArrayList[T, N] movability for a non-trivially-relocatable element type
# (Item has a str field -> SSO, not byte-relocatable). The factory returns are
# NRVO/copy-elided (no move ctor runs there), so the load-bearing check is the
# `moved = ws` line below: `std::move` of a named lvalue is NOT elidable, so it
# actually runs ArrayList.__move__, relocating the live prefix element-wise. If
# __move__ were wrong (e.g. a byte memcpy), the SSO strings would corrupt here.
# Mutating an element after a boundary also forces reference semantics (a
# silent copy would not be observed), per the reference-type-distinction rule.
from tplib import ArrayList
from tpy import Own


class Item:
    name: str
    n: int

    def __init__(self, name: str, n: int) -> None:
        self.name = name
        self.n = n


def make() -> Own[ArrayList[Item, 4]]:
    xs = ArrayList[Item, 4]()
    xs.append(Item("alpha", 1))
    xs.append(Item("beta", 2))
    return xs


def make_full() -> Own[ArrayList[Item, 4]]:
    xs = ArrayList[Item, 4]()
    xs.append(Item("a", 0))
    xs.append(Item("b", 1))
    xs.append(Item("c", 2))
    xs.append(Item("d", 3))
    return xs


def make_empty() -> Own[ArrayList[Item, 4]]:
    return ArrayList[Item, 4]()


def main() -> None:
    xs = make()                 # returned by value -> move of live prefix
    print(len(xs))              # 2
    print(xs[0].name, xs[1].name)  # alpha beta

    xs[0].n = 99                # mutate through element ref after the move
    print(xs[0].n)              # 99

    xs.append(Item("gamma", 3))
    last = xs.pop()
    print(last.name)            # gamma

    xs.insert(0, Item("zero", 0))
    print(xs[0].name, len(xs))  # zero 3
    xs.reverse()
    print(xs[0].name)           # beta

    ys = make_full()
    print(len(ys))              # 4
    print(ys[3].name)           # d

    zs = make_empty()
    print(len(zs))              # 0
    zs.append(Item("late", 7))
    print(zs[0].name)           # late

    # Forced, non-elided move: `std::move` of a named lvalue runs the
    # relocating move ctor for real (NRVO/elision cannot apply here).
    ws = make()
    moved = ws                  # last-use of ws -> move-constructs `moved`
    print(len(moved), moved[0].name, moved[1].name)  # 2 alpha beta
    moved[0].name = "shifted"   # mutate a relocated element
    print(moved[0].name)        # shifted

    # Move-assignment: reassigning a live list runs operator= (destroy the
    # old elements, then relocate the new ones via the move ctor).
    acc = make()                # [alpha, beta]
    acc = make_full()           # reassign -> drop old, relocate [a, b, c, d]
    print(len(acc), acc[0].name, acc[3].name)  # 4 a d


main()
