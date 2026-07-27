# The generator `while` peephole and the resumable-CFG branch route through the
# same truthiness render, so an always-truthy record-returning call must be
# evaluated there too rather than folded to `true`.
from typing import Iterator

from tpy import Own


class Rec:
    v: int

    def __init__(self, v: int):
        self.v = v


calls = 0


def make() -> Own[Rec]:
    global calls
    calls += 1
    return Rec(1)


def gen_while(n: int) -> Iterator[int]:
    i = 0
    while make():
        yield i
        i += 1
        if i >= n:
            break


def gen_branch(n: int) -> Iterator[int]:
    i = 0
    while i < n:
        # The suspend inside the branch forces the resumable CFG, not the
        # simple-generator peephole.
        if make():
            yield i
        i += 1


def main() -> None:
    for v in gen_while(2):
        print("while", v)
    print("after while:", calls)

    for v in gen_branch(2):
        print("branch", v)
    print("after branch:", calls)


main()
