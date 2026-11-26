# Reads off a temporary in a loop source: the statement keeps the receiver for
# the loop, so retaining builtins and a generator factory read live storage.
from typing import Iterator
from tpy import Own, int32

LG = [1, 2]


class H:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [7, 8]

    @property
    def items(self) -> list[int32]:
        return LG

    @property
    def own_items(self) -> list[int32]:
        return self.xs


def mk() -> Own[H]:
    return H()


def walk(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def main() -> None:
    # retaining builtin over a getter lending a global
    for x in reversed(mk().items):  # tpyc: ok
        print("reversed_global:", x)
    # retaining builtin over a getter lending the temporary's own field
    for x in reversed(mk().own_items):  # tpyc: ok
        print("reversed_self:", x)
    # zip and enumerate over the temporary's own field
    for a, b in zip(mk().own_items, LG):  # tpyc: ok
        print("zip:", a, b)
    for i, x in enumerate(mk().own_items):  # tpyc: ok
        print("enumerate:", i, x)
    # a generator factory taking the temporary's own field
    for x in walk(mk().own_items):  # tpyc: ok
        print("gen_factory:", x)
    # generator expression source
    print("genexpr:", sum(x for x in mk().items))  # tpyc: ok
