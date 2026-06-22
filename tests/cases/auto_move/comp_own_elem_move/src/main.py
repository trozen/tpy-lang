# A list/set comprehension over an Own[T]-yielding source moves a bare last-use
# element (the @nocopy guard forces it); derived/filtered sinks and borrow
# sources still copy+warn.
from typing import Iterator
from tpy import Own, nocopy


@nocopy
class Widget:
    id: int

    def __init__(self, i: int) -> None:
        self.id = i


def widgets(n: int) -> Iterator[Own[Widget]]:
    i = 0
    while i < n:
        yield Widget(i)
        i += 1


class Node:
    v: int

    def __init__(self, i: int) -> None:
        self.v = i

    def __hash__(self) -> int:
        return self.v

    def __eq__(self, o: "Node") -> bool:
        return self.v == o.v


def nodes(n: int) -> Iterator[Own[Node]]:
    i = 0
    while i < n:
        yield Node(i)
        i += 1


def collect_list_nocopy() -> int:
    # Only compiles because the element moves -- a copy of @nocopy Widget is
    # a hard error.
    xs = [w for w in widgets(3)]  # tpyc: ok
    return len(xs)


def collect_set() -> int:
    s = {n for n in nodes(3)}  # tpyc: ok
    return len(s)


def borrow_source_copies(src: list[Node]) -> int:
    # Borrowed (list) source: the element is not owned, so it copies (warns),
    # proving the move is gated on an Own[T]-yielding source.
    ys = [n for n in src]  # tpyc: warning(/copies Node into owned storage/)
    return len(ys)


def filtered_moves() -> int:
    # The element is the comprehension's last sink: even with the loop var also
    # read in the filter, it is structurally the last use (the filter ran
    # first), so the element moves. @nocopy makes this self-evidencing -- the
    # case only compiles because the filtered element moves (a copy is an error).
    ys = [w for w in widgets(3) if w.id > 0]  # tpyc: ok
    return len(ys)


def main() -> None:
    print(collect_list_nocopy())
    print(collect_set())
    src = [Node(1), Node(2)]
    print(borrow_source_copies(src), len(src))
    print(filtered_moves())


main()
