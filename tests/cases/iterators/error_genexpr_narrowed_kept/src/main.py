# A genexpr handed to a LAZY consumer that is bound to a name outlives the
# statement that creates it, so a narrowing it took of a captured name does not
# hold once that name can be rebound before the pulls.
from typing import Iterable, Iterator
from tpy import int32


def relay(it: Iterable[int32]) -> Iterator[int32]:
    for v in it:
        yield v + 100


def main(xs: list[int32], k: int32 | None) -> None:
    if k is None:
        return
    # `g` keeps the genexpr alive and hands it on to `h`, which is what gets
    # consumed; `k` is rebound before that.
    g = relay(x * k for x in xs)  # tpyc: error(/is kept past the statement and 'k' can be rebound before it runs/)
    h = g
    k = None
    for v in h:
        print(v)


main([1, 2, 3], 2)
