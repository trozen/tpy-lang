# Nested def in a resumable (multi-yield) generator: a frame member that
# mutates a captured frame local, visible across yields.
from typing import Iterator
from tpy import Int32


def gen() -> Iterator[Int32]:
    total = 0

    def add(x: Int32) -> None:
        nonlocal total
        total += x

    add(5)
    # Bound AFTER the def: must still hoist to a frame field (the sema
    # namespace-identity fix) to survive the yields below.
    bonus = 100
    yield total
    add(7)
    yield total + bonus


def main() -> None:
    for v in gen():
        print(v)


main()
