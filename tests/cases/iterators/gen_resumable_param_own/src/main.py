# Resumable-path generator with an `Own[T]` parameter (a move-only Box):
# the param is moved into the coro frame (OWNED_VALUE capture), not copied
# -- so move-only types work here where the legacy by-value-copy capture
# could not. (Phase D: params widening.)
from typing import Iterator
from tplib.box import Box
from tpy import Int32, Own


def drain(b: Own[Box[Int32]]) -> Iterator[Int32]:
    yield b.get()
    yield b.get() * 2


def main() -> None:
    box = Box(Int32(7))
    for v in drain(box):
        print(v)


main()
