# Consuming a @nocopy local, then reading it in a `yield` inside a generator,
# must be rejected: the yield read keeps the var live, so consume is not last-use.
from tpy import int32, Own, nocopy
from typing import Iterator


@nocopy
class Handle:
    fd: int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


def gen() -> Iterator[int32]:
    h = Handle()
    h.fd = 7
    consume(h)  # tpyc: error(/@nocopy.*used after/)
    yield h.fd


def main() -> None:
    for v in gen():
        print(v)


main()
