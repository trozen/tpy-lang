# Delegating to a generator method on a GENERIC class inside a resumable
# frame is rejected cleanly: the method's struct is templated on the class
# type args, which the __for_src field type cannot spell yet.
from typing import Iterator
from tpy import Int32


class Box[T]:
    items: list[T]

    def __init__(self, items: list[T]) -> None:
        self.items = items

    def pair(self) -> Iterator[T]:
        yield self.items[0]
        yield self.items[1]


def gen(b: Box[Int32]) -> Iterator[Int32]:
    yield 0
    for v in b.pair():  # tpyc: error(/generic class/)
        yield v


def main() -> None:
    b = Box([5, 6])
    for v in gen(b):
        print(v)


main()
