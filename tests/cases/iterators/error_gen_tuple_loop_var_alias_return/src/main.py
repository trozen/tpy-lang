# An alias of an ephemeral tuple loop var is the same stale-slot borrow
# under another name: returning the alias must be rejected like the direct
# `return p` form (error_gen_tuple_loop_var_return).
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen() -> Iterator[tuple[int32, Box]]:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    yield items[0]


def take() -> tuple[int32, Box]:
    for p in gen():
        q = p
        return q  # tpyc: error(/borrows an element/)
    raise RuntimeError("empty")


def main() -> None:
    pass


main()
