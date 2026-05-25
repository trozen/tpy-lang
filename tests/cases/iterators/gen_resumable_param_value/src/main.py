# Resumable-path generator with a value-type parameter, captured into the
# coro frame by value (Phase D: params widening of the generator ->
# resumable-frame migration).
from typing import Iterator
from tpy import Int32


def scaled(n: Int32) -> Iterator[Int32]:
    yield n
    yield n * 2
    yield n * 3


def main() -> None:
    for v in scaled(5):
        print(v)


main()
