# Generator with `yield` inside a `while` loop and a `yield` after the loop --
# routed onto the resumable-frame path (Phase D1: if/while). The counter `i`
# is live across the in-loop suspension, so it must persist in the coro frame
# rather than a C++ local that resets on resume.
from typing import Iterator
from tpy import Int32


def countdown(n: Int32) -> Iterator[Int32]:
    i = n
    while i > 0:
        yield i
        i -= 1
    yield -1


def main() -> None:
    for v in countdown(3):
        print(v)


main()
