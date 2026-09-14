# Two single-yield generators delegating to each other: each frame would
# embed the other by value (mutually infinite-size), so the emit-order cycle
# check raises the recursive-delegation diagnostic instead of recursing at
# runtime.
from typing import Iterator
from tpy import int32


def ping() -> Iterator[int32]:  # tpyc: error(/recursive generator delegation/)
    for x in pong():
        yield x


def pong() -> Iterator[int32]:
    for x in ping():
        yield x


def main() -> None:
    for v in ping():
        print(v)


main()
