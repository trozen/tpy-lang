# A comprehension over a Bag the settled body keeps (rebound later, re-read in
# a loop) is lent: each element copy is warned, so only lengths are printed.
from typing import Iterator, Self
from tpy import Own, dispatch, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v

    def fin(self: Own[Self]) -> Own[Self]:
        return self

    def __hash__(self) -> int:
        return hash(self.v)

    def __eq__(self, other: Self) -> bool:
        return other.v == self.v


class Bag:
    items: list[R]

    def __init__(self) -> None:
        self.items = [R(1), R(2)]

    @dispatch
    def __iter__(self) -> Iterator[R]:
        for r in self.items:
            yield r

    @dispatch
    def __iter__(self: Own[Self]) -> Iterator[Own[R]]:
        while self.items:
            yield self.items.pop(0)


def keep(r: Own[R]) -> Own[R]:
    return r


def rebound_arg(p: Bag) -> None:
    b = Bag()
    # the clause variable at an `Own` argument
    out = [keep(r) for r in b]  # tpyc: warning(/copies R into owned storage/)
    b = p
    print("comp_lent arg", len(out), len(b.items))


def rebound_sink(p: Bag) -> None:
    b = Bag()
    # the clause variable is the element
    out = [r for r in b]  # tpyc: warning(/copies R into owned storage/)
    b = p
    print("comp_lent sink", len(out), len(b.items))


def rebound_receiver(p: Bag) -> None:
    b = Bag()
    # the clause variable as a consuming receiver
    out = [r.fin() for r in b]  # tpyc: warning(/copies R into owned storage/)
    b = p
    print("comp_lent receiver", len(out), len(b.items))


def rebound_dict(p: Bag) -> None:
    b = Bag()
    # the clause variable as a dict comprehension's value
    d = {r.v: r for r in b}  # tpyc: warning(/copies R into owned storage/)
    b = p
    print("comp_lent dict", len(d), len(b.items))


def rebound_for(p: Bag) -> None:
    # the `for` twin
    b = Bag()
    out: list[R] = []
    for r in b:
        out.append(r)  # tpyc: warning(/copies R into owned storage/)
    b = p
    print("comp_lent for", len(out), len(b.items))


def in_loop() -> None:
    bag = Bag()
    for i in range(2):
        # read again on the next iteration: lent
        out = [r for r in bag]  # tpyc: warning(/copies R into owned storage/)
        print("comp_lent in loop", i, len(out), len(bag.items))


def keys_only() -> None:
    bag = Bag()
    # the key alone moves a dead Bag's variable out: consumed, no copy
    by_r = {r: 0 for r in bag}  # tpyc: ok
    print("comp_lent dict keys", sorted(k.v for k in by_r))


def main() -> None:
    rebound_arg(Bag())
    rebound_sink(Bag())
    rebound_receiver(Bag())
    rebound_dict(Bag())
    rebound_for(Bag())
    in_loop()
    keys_only()
