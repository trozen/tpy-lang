# Reads of the suspended `a` that CPython does not evaluate at import, and
# reads of `Three`, which `a` bound before it started `b`.
import a
from a import Three  # tpyc: ok
from typing import Protocol

type Items = list[a.Item]  # tpyc: ok


def quoted(x: "a.Item") -> int:  # tpyc: ok
    return x.n


def quoted_leaf(xs: list["a.Item"]) -> int:  # tpyc: ok
    return len(xs)


def bound_early(t: a.Three) -> int:  # tpyc: ok
    return t.k


# Quoted, a protocol's member annotations are not evaluated at import.
class Sink(Protocol):
    last: "a.Item"  # tpyc: ok

    def put(self, i: "a.Item") -> None: ...  # tpyc: ok


def show(it: "a.Item") -> None:
    items: Items = [a.Item(2), a.Item(3)]
    it.n += 10
    print("b:", quoted(it), quoted_leaf(items), bound_early(Three(7)))
