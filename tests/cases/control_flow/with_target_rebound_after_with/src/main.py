# A `with ... as g` target REASSIGNED after the block and then read across a
# suspension must resolve to ONE storage location. Two locations (a frame field
# plus a shadowing body-local) let the write land on one and the post-resume read
# take the other, which is uninitialized memory rather than a wrong-but-stable
# value.
from typing import Iterator

from tpy import Int32


class Source:
    def __init__(self, start: Int32):
        self.n = start

    def __enter__(self) -> Int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


def steps() -> Iterator[Int32]:
    c = Source(5)
    with c as g:
        pass
    g = 9
    yield 1
    yield g


def main() -> None:
    for v in steps():
        print(v)


main()
