# A narrowed Optional FIELD read in an except handler is stale when the
# try body suspends: the exception may be raised after a resume where
# the caller mutated the field, so handler-entry kills field facts.
from tpy import Int32
from typing import Iterator


class Box:
    f: Int32 | None

    def __init__(self) -> None:
        self.f = 5

    def in_except(self) -> Iterator[Int32]:
        if self.f is not None:
            try:
                yield self.f  # tpyc: ok
                raise ValueError("boom")
            except ValueError:
                yield self.f  # tpyc: error(/Type mismatch in yield value/)
        yield -1


def main() -> None:
    b = Box()
    for x in b.in_except():
        print(x)


main()
