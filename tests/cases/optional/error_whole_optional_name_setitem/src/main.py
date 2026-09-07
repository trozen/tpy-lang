# Storing a whole `Optional[Int32]` NAME into a `list[Optional[Int32]]` slot: the
# scalar setitem row does not cover a whole-optional binding copy.
# Concretely, `items[0] = o` stores the whole `Optional[Int32]` name `o`;
# TPy rejects that assignment today.
from typing import Optional
from tpy import Int32


def store(items: list[Optional[Int32]], o: Optional[Int32]) -> None:
    items[0] = o  # tpyc: error(/setitem.optval_value_shape/)


def main() -> None:
    xs: list[Optional[Int32]] = [None]
    store(xs, 3)
    print(xs[0])


main()
