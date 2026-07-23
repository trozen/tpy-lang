# A narrowed Optional FIELD read again after a yield is stale: the
# caller may mutate the field between next() calls, so sema kills
# field-path narrow facts at every suspension point. Re-guard or bind
# the value to a local before the first yield to keep it.
from tpy import Int32
from typing import Iterator


class Box:
    f: Int32 | None

    def __init__(self) -> None:
        self.f = 5

    def ints(self) -> Iterator[Int32]:
        if self.f is not None:
            yield self.f  # tpyc: ok
            yield self.f  # tpyc: error(/Type mismatch in yield value/)
        yield -1


def main() -> None:
    b = Box()
    for x in b.ints():
        print(x)


main()
