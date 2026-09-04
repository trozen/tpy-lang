# The adjacent shape to yielding a container PARAM: yielding a container
# FIELD. The slot wants a borrow of the field's storage, which is a different
# read from the param's reference member, so it keeps its own reject.
from typing import Iterator
from tpy import Int32


class H:
    buf: list[Int32]

    def __init__(self) -> None:
        self.buf = [1]


def each(h: H) -> Iterator[list[Int32]]:  # tpyc: error(/not yet supported.*res.yield_type/)
    yield h.buf
    yield h.buf


def main() -> None:
    for got in each(H()):
        print(len(got))


main()
