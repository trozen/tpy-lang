# Resumable-path generator iterating a *temporary* native container (the
# result of a call, not a named variable) with a `yield` inside the loop
# (Phase D2). The temporary is stored in a `__for_src` frame field so the
# begin/end iterator doesn't dangle across the suspension. A leading yield
# makes this non-simple, so it takes the struct/resumable path.
from typing import Iterator
from tpy import int32, Own


def make() -> Own[list[int32]]:
    return [10, 20, 30]


def gen() -> Iterator[int32]:
    yield 0
    for x in make():
        yield x
    yield -1


def main() -> None:
    for v in gen():
        print(v)


main()
