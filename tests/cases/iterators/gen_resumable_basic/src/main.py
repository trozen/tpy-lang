# Generator lowered onto the resumable-frame path (free fn, no params,
# linear body, multiple yields, a local live across yields). Validates the
# migrated `yield` -> `__next__` codegen (Phase C of the generator ->
# resumable-frame migration).
from typing import Iterator
from tpy import Int32


def counts() -> Iterator[Int32]:
    n = 10
    yield n
    n += 5
    yield n
    yield n * 2


def main() -> None:
    for v in counts():
        print(v)


main()
