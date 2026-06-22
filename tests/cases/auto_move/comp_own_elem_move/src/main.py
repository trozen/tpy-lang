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


def filtered_copies() -> int:
    # When the loop var is also read in the filter, liveness marks no occurrence
    # last-use, so the element copies -- and the warning must NOT be suppressed
    # (the move-suppression decision reads the same last-use fact as codegen).
    ys = [n for n in nodes(4) if n.v > 0]  # tpyc: warning(/copies Node into owned storage/)
    return len(ys)


def main() -> None:
    print(collect_list_nocopy())
    print(collect_set())
    src = [Node(1), Node(2)]
    print(borrow_source_copies(src), len(src))
    print(filtered_copies())


main()
