# Consuming a @nocopy local, then reading it in a `yield` inside a generator,
# must be rejected: the yield read keeps the var live, so consume is not last-use.
from tpy import Int32, Own, nocopy
from typing import Iterator


@nocopy
class Handle:
    fd: Int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


def gen() -> Iterator[Int32]:
    h = Handle()
    h.fd = 7
    consume(h)  # tpyc: error(/@nocopy.*used after/)
    yield h.fd


def main() -> None:
    for v in gen():
        print(v)


main()
