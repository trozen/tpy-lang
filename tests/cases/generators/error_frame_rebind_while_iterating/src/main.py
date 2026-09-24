# A generator is not rebound inside a `for` over it, which still runs the old
# one (BUGS.md#generator-rebind-rejects).
from typing import Iterator

from tpy import int32


def evens(start: int32) -> Iterator[int32]:
    i = start
    while i < start + 10:
        i += 2
        yield i


def main() -> None:
    g = evens(0)
    out = []
    for v in g:
        out.append(v)
        # The subject: the name the running loop iterates is rebound.
        g = evens(100)  # tpyc: error(/cannot rebind 'g' inside a 'for' loop over it/)
    print(out)


main()
