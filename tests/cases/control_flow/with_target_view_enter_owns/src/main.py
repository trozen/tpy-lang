# A `str` / `bytes` `__enter__` hands back the OWNING form by value, but sema
# types the target as the VIEW (string_view / span). A frame field spelled from
# the view outlives the buffer, so the field must own -- and that has to hold
# whether or not the `with` body itself suspends, since the target is
# frame-resident either way. Reading the contents after a later yield is what
# catches a dangling view: the length often survives while the bytes do not.
from typing import Iterator

from tpy import Int32


class Label:
    def __enter__(self) -> str:
        return "hello" + "-world"

    def __exit__(self, et, ev, tb) -> None:
        pass


class Blob:
    def __enter__(self) -> bytes:
        return b"abc" + b"def"

    def __exit__(self, et, ev, tb) -> None:
        pass


def gen() -> Iterator[Int32]:
    with Label() as s:
        pass
    with Blob() as b:
        pass
    yield 1
    print(s)
    print(len(s))
    print(b[0])
    yield 2


def main() -> None:
    for v in gen():
        print(v)


main()
