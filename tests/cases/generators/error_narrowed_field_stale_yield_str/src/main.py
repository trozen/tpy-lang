# The str flavor of the stale-field yield (read freed memory pre-fix):
# the sema kill is type-agnostic, but the reference-flavor field gets its
# own regression guard against a future type-forked regression. The
# surviving first yield is pinned by
# tests/cases/generators/narrowed_str_field_yield.
from typing import Iterator


class Box:
    s: str | None

    def __init__(self) -> None:
        self.s = "hi"

    def strs(self) -> Iterator[str]:
        if self.s is not None:
            yield self.s
            yield self.s  # tpyc: error(/Type mismatch in yield value/)
        yield "end"


def main() -> None:
    b = Box()
    for x in b.strs():
        print(x)


main()
