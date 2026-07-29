# Same cross-scope hazard as error_gen_rebind_slot_crosses_lambda (see its
# header for why the reject is a stopgap), but with an earlier same-scope rebind
# that already drained the slot's declaration. The reject keys on the slot's
# owning scope, not on the pending declaration, so the diagnostic must not
# depend on statement order.
from tpy import Int32
from typing import Iterator


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def before_while(n: Int32) -> Iterator[Int32]:
    p = Point(11)
    p = Point(12)
    i = 0
    while i < n:
        p = Point(i)  # tpyc: error(/declared outside a generator or nested function/)
        yield p.x
        i += 1


def main() -> None:
    for got in before_while(2):
        print(got)


main()
