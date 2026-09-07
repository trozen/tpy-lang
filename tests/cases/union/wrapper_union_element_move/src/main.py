# A movable recursive-union local at a container element slot: the insert MOVES
# it, so the source list shrinks and the destination grows by exactly the item
# that left.
from tpy import Int32

type JV = None | int | str | list[JV]


def push(a: list[JV], src: list[JV]) -> Int32:
    item = src.pop()
    a.append(item)  # the last use of `item` moves into the element slot
    return len(a)


def store(d: dict[str, JV], src: list[JV]) -> Int32:
    item = src.pop()
    d["k"] = item  # the dict insert is the same sink
    return len(d)


def main() -> None:
    a: list[JV] = []
    s: list[JV] = [1, 2]
    print(push(a, s), len(s))
    print(store({}, s), len(s))


main()
