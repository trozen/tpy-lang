# A `with ... as t` target in a resumable frame ALIASES the manager the way
# CPython does (`t is c` for a `return self` __enter__) rather than holding a
# copy: the mutation through the target must be visible on the manager, both in
# __exit__ and after the statement. An owning frame slot would print the
# pre-mutation value here.
from typing import Iterator

from tpy import Int32


class Counter:
    def __init__(self, start: Int32):
        self.n = start

    def __enter__(self) -> "Counter":
        return self

    def __exit__(self, et, ev, tb) -> None:
        print("exit sees", self.n)


def steps(limit: Int32) -> Iterator[Int32]:
    c = Counter(limit)
    with c as guard:
        yield guard.n
        guard.n += 1
        yield guard.n
    yield c.n


def main() -> None:
    for v in steps(5):
        print(v)


main()
