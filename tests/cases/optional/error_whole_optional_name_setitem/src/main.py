# Storing a whole `Optional[int32]` NAME into a `list[Optional[int32]]` slot: the
# scalar setitem row does not cover a whole-optional binding copy.
# Concretely, `items[0] = o` stores the whole `Optional[int32]` name `o`;
# TPy rejects that assignment today.
from typing import Optional
from tpy import int32


def store(items: list[Optional[int32]], o: Optional[int32]) -> None:
    items[0] = o  # tpyc: error(/setitem.optval_value_shape/)


def main() -> None:
    xs: list[Optional[int32]] = [None]
    store(xs, 3)
    print(xs[0])


main()
