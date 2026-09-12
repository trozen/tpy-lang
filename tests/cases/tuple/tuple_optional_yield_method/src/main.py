# Method-bound generator yielding tuple[T | None, ...] over a local list
# parameter. Exercises the `record_name` codepath in _gen_simple_for_generator
# alongside the storage-form lift. The `extra=1` indentation offset (from
# record_name) and the `this` capture are both active; yield_storage_form=True
# selects the storage-form slot type independently.
#
# The iteration source is the method's `items` parameter (a non-const local),
# not `self.field` -- BUGS.md tracks the const-source iteration bug that fires
# when `self.list_field` or `dict.values()` are the iterable.
from typing import Iterator
from tpy import int32


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


class Holder:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def pairs(self, items: list[P]) -> Iterator[tuple[P | None, P | None]]:
        for it in items:
            yield (it, None)


def main() -> None:
    h = Holder(int32(0))
    items: list[P] = [P(1), P(2), P(3)]
    for a, b in h.pairs(items):
        if a is not None:
            print(a.x)


main()
