# The dict views of a READONLY dict are the runtime's const view specializations:
# a view temp spells its value argument `const V` (items() and values()).
from tpy import int32, readonly
from tplib import ArrayList


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


def count(d: readonly[dict[str, Node]]) -> int32:
    # copies each Node (BUGS.md#dict-view-source-copy-unwarned); only len is read
    xs = ArrayList[tuple[str, readonly[Node]], 16](d.items())  # tpyc: ok
    return len(xs)


def count_values(d: readonly[dict[str, Node]]) -> int32:
    # copies each Node (BUGS.md#dict-view-source-copy-unwarned); only len is read
    xs = ArrayList[readonly[Node], 16](d.values())  # tpyc: ok
    return len(xs)


def main() -> None:
    d = {"a": Node(1), "b": Node(2)}
    print("items", count(d))
    print("values", count_values(d))


main()
