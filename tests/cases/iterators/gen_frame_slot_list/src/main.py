# Regression: a `list[T]` local declared with the empty-literal `[]` and
# mutated across a yield. Before frame_slot, this lowered to an outer
# `std::optional<vector<T>>` with `history = {};` setting the outer to
# nullopt; the subsequent `.append()` was UB. frame_slot replaces the
# outer wrap, and codegen routes init through `.emplace()` so the
# brace-init ambiguity can't recur.
from tpy import Int32
from typing import Iterator


def gen() -> Iterator[Int32]:
    history: list[Int32] = []
    history.append(1)
    history.append(2)
    history.append(3)
    yield len(history)
    history.append(4)
    yield len(history)


def main() -> None:
    for n in gen():
        print(n)


main()
