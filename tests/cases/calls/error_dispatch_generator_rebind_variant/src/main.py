# Each variant of a generator @dispatch set has its own generator type, so
# one local cannot hold both: rebinding it to the other variant's generator
# is refused, though CPython runs it (BUGS.md#dispatch-generator-rebind-variant).
from typing import Iterator
from tpy import dispatch, int32


@dispatch
def gen(x: int32) -> Iterator[int32]:
    yield x


@dispatch
def gen(x: str) -> Iterator[int32]:
    yield len(x)


def main() -> None:
    g = gen(1)
    for v in g:
        print(v)
    g = gen("ab")  # tpyc: error(/cannot rebind 'g' to a different generator: 'g' holds a 'gen\(x: int32\)' generator.*bind 'gen\(x: str\)' to a new name/)
    for v in g:
        print(v)


main()
