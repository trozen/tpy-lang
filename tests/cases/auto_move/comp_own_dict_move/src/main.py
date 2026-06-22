# A dict comprehension over an Own[T] source moves the VALUE (last sink), with
# the key sequenced before the move; the key moves only when it is the last use.
from typing import Iterator
from tpy import Own, nocopy


@nocopy
class Widget:
    id: int
    tag: int

    def __init__(self, i: int) -> None:
        self.id = i
        self.tag = i * 10


def widgets(n: int) -> Iterator[Own[Widget]]:
    i = 0
    while i < n:
        yield Widget(i)
        i += 1


class Node:
    id: int

    def __init__(self, i: int) -> None:
        self.id = i

    def __hash__(self) -> int:
        return self.id

    def __eq__(self, o: "Node") -> bool:
        return self.id == o.id


def nodes(n: int) -> Iterator[Own[Node]]:
    i = 0
    while i < n:
        yield Node(i)
        i += 1


def value_moves_key_field() -> int:
    # @nocopy value forces the move (a copy is a hard error); the key reads a
    # field of the same element, so it is sequenced before the value move.
    d = {w.id: w for w in widgets(3)}  # tpyc: ok
    total = 0
    for k in d:
        total += d[k].tag
    return total


def value_only() -> int:
    # Key does not read the element: pure value move. The key is still sequenced
    # into a local (the sequencing fires whenever the value is a bare-var last
    # sink, independent of whether the key reads the loop var) -- harmless here.
    d = {7: w for w in widgets(1)}  # tpyc: ok
    return len(d)


def key_does_not_move(src_key: int) -> int:
    # The value reads the element after the key, so the bare-loop-var KEY must
    # NOT move -- it copies (a move would leave the value reading a moved-from
    # node). Correct output proves the key was not consumed early.
    d = {node: node.id for node in nodes(3)}  # tpyc: warning(/copies Node into owned storage/)
    total = 0
    for n in d:
        total += d[n]
    return total


def filtered_value_moves() -> int:
    # Filter + owned dict: the value is still the last sink (the filter ran
    # first), so @nocopy Widget moves -- the case only compiles if it does.
    d = {w.id: w for w in widgets(4) if w.id > 0}  # tpyc: ok
    total = 0
    for k in d:
        total += d[k].tag
    return total


def same_var_key_and_value() -> int:
    # Both sinks are the bare loop var: the value (last sink) moves; the key,
    # sequenced into a local first, copies (warns) -- a hashable owned key.
    d = {node: node for node in nodes(3)}  # tpyc: warning(/copies Node into owned storage/)
    return len(d)


def main() -> None:
    print(value_moves_key_field())
    print(value_only())
    print(key_does_not_move(0))
    print(filtered_value_moves())
    print(same_var_key_and_value())


main()
