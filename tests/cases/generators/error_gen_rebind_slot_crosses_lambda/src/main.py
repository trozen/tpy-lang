# A local declared BEFORE a generator's loop and reassigned to an rvalue INSIDE
# it has no sound home for its rebind slot: inside the lambda the slot dies each
# invocation while the pointer aliasing it is captured and outlives it, outside
# the lambda cannot name it. Rejected rather than emitted either way.
#
# This is a stopgap over one codegen-visible slice of a general hazard -- a
# rebind while a live alias points at the local, which one slot cannot represent
# past a single generation. The sync and resumable-frame spellings of the same
# shape are silently wrong instead (BUGS.md), so expect this reject to be
# replaced by the uniform diagnostic rather than kept as a special case.
from tpy import Int32
from typing import Iterator


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def before_while(n: Int32) -> Iterator[Int32]:
    p = Point(11)
    i = 0
    while i < n:
        p = Point(i)  # tpyc: error(/declared outside a generator or nested function/)
        yield p.x
        i += 1


def main() -> None:
    for got in before_while(2):
        print(got)


main()
