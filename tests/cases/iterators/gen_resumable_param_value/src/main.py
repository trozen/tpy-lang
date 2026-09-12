# Resumable-path generator with a value-type parameter, captured into the
# coro frame by value (Phase D: params widening of the generator ->
# resumable-frame migration).
from typing import Iterator
from tpy import int32


def scaled(n: int32) -> Iterator[int32]:
    yield n
    yield n * 2
    yield n * 3


def main() -> None:
    for v in scaled(5):
        print(v)


main()
