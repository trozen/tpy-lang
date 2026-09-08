# A narrowed Optional FIELD read inside a loop that suspends is stale on
# re-entry -- the back-edge crosses the yield, so the loop-entry meet
# kills field facts when the body suspends. Bind the value to a local
# before the loop to keep it. That local-bind loop face is pinned by
# tests/cases/generators/narrowed_value_opt_field_yield.
from tpy import Int32
from typing import Iterator


class Box:
    f: Int32 | None

    def __init__(self) -> None:
        self.f = 5

    def items(self) -> Iterator[Int32]:
        if self.f is not None:
            for _i in range(3):
                yield self.f  # tpyc: error(/Type mismatch in yield value/)
        yield -1


def main() -> None:
    b = Box()
    for x in b.items():
        print(x)


main()
