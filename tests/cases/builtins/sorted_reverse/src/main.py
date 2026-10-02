# sorted(..., reverse=True), alone and with key=, in either keyword order.
# Equal elements keep their original order, as in CPython (reverse is not
# "sort then reverse").
from typing import Iterator


def evens(n: int) -> Iterator[int]:
    for i in range(n):
        yield i * 2


class Board:
    names: list[str]

    def __init__(self) -> None:
        self.names = ["bob", "al", "carol", "ed"]

    def show_by_length(self, longest_first: bool) -> None:
        # method: a run-time flag, not a literal
        ordered = sorted(self.names, key=lambda n: len(n), reverse=longest_first)  # tpyc: ok
        print("method:", ordered)


def main() -> None:
    xs = [3, 1, 2]
    # plain
    print("plain:", sorted(xs, reverse=True), sorted(xs, reverse=False), sorted(xs))  # tpyc: ok
    # element types
    print("types:", sorted(["b", "a", "c"], reverse=True), sorted([1.5, 0.5, 2.5], reverse=True))  # tpyc: ok
    # key= and reverse=, in both orders
    words = ["bb", "a", "ccc"]
    print("key:", sorted(words, key=lambda s: len(s), reverse=True))  # tpyc: ok
    print("key_order:", sorted(words, reverse=True, key=lambda s: len(s)))  # tpyc: ok
    # stability: equal keys keep their original order under reverse (the
    # annotation works around BUGS.md#key-over-pending-tuple-list)
    pairs: list[tuple[int, int]] = [(1, 20), (1, 10), (0, 99), (1, 30)]
    print("stable:", sorted(pairs, key=lambda t: t[0], reverse=True))  # tpyc: ok
    # generator source
    print("generator:", sorted(evens(4), reverse=True))  # tpyc: ok
    # the source list is untouched
    print("source:", xs)
    b = Board()
    b.show_by_length(True)
    b.show_by_length(False)


main()
