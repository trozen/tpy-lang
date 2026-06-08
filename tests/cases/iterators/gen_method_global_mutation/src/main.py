# Regression: a `global` written inside a generator METHOD, across a yield,
# must reach the module slot (not a resumable-frame copy) so the driver
# observes it per resume. Multiple yields force the resumable-frame path
# (where the shadowing frame field would otherwise appear).
from typing import Iterator
from tpy import Int32

emitted: Int32 = 0


class Source:
    def values(self) -> Iterator[Int32]:
        global emitted
        emitted += 1
        yield 0
        emitted += 1
        yield 1
        emitted += 1
        yield 2


def main() -> None:
    s = Source()
    for x in s.values():
        print("x =", x, "emitted =", emitted)


main()
