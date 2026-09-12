# A generator that forwards its Iterator parameter through a LOCAL before
# iterating it: the peephole has no forwarded-local rung, so this rejects.
from typing import Iterator
from tpy import int32


def relay(it: Iterator[int32]) -> Iterator[int32]:  # tpyc: error(/sgen.forwarded_local/)
    xs = it
    for x in xs:
        yield x


def source(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        yield i
        i = i + 1


def main() -> None:
    for v in relay(source(3)):
        print(v)


main()
