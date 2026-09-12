# A would-be-simple generator containing a nested def routes to the
# resumable path (the peephole's yielding lambda cannot see the closure).
from typing import Iterator
from tpy import int32


def gen(k: int32) -> Iterator[int32]:
    base = 100

    def scale(x: int32) -> int32:
        return x + base

    for i in range(k):
        yield scale(i)


def main() -> None:
    for v in gen(3):
        print(v)


main()
