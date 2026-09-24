# A generator is never rebound through `nonlocal`: the binding function may
# be looping over it (BUGS.md#generator-rebind-rejects).
from typing import Iterator

from tpy import int32


def counter(start: int32) -> Iterator[int32]:
    i = start
    while True:
        i += 1
        yield i


def main() -> None:
    g = counter(0)

    def reset() -> None:
        nonlocal g
        # The subject: a rebind through nonlocal.
        g = counter(50)  # tpyc: error(/cannot rebind generator 'g' through 'nonlocal'/)

    reset()
    for v in g:
        print(v)
        break


main()
