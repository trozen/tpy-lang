# A narrowed Optional FIELD read in an except handler is stale when the
# try body suspends: the exception may be raised after a resume where
# the caller mutated the field, so handler-entry kills field facts. The
# try body's own first yield still derefs -- that face is pinned by
# tests/cases/generators/narrowed_value_opt_field_yield.
from tpy import int32
from typing import Iterator


class Box:
    f: int32 | None

    def __init__(self) -> None:
        self.f = 5

    def in_except(self) -> Iterator[int32]:
        if self.f is not None:
            try:
                yield self.f
                raise ValueError("boom")
            except ValueError:
                yield self.f  # tpyc: error(/Type mismatch in yield value/)
        yield -1


def main() -> None:
    b = Box()
    for x in b.in_except():
        print(x)


main()
