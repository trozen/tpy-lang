# Generator with `yield` inside both `if` branches plus a `yield` after the
# `if` -- routed onto the resumable-frame path (Phase D1: if/while). Guards
# that branch join + post-compound yields lower correctly (the post-compound
# yield is exactly what the match-gate protects against on undecomposed
# compounds).
from typing import Iterator
from tpy import int32


def sign_stream(n: int32) -> Iterator[int32]:
    yield 0
    if n > 0:
        yield 1
        yield 2
    else:
        yield -1
    yield 99


def main() -> None:
    for v in sign_stream(5):
        print(v)
    for v in sign_stream(-3):
        print(v)


main()
