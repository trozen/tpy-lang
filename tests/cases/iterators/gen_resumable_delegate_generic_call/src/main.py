# A resumable generator delegating to a GENERIC sub-generator call: the
# __for_src frame field spells the callee struct with its inferred type
# args (__gen_pair<int32_t>).
from typing import Iterator
from tpy import Int32


def pair[T](a: T, b: T) -> Iterator[T]:
    yield a
    yield b


def gen() -> Iterator[Int32]:
    yield 0
    for x in pair(7, 8):  # tpyc: ok
        yield x


def main() -> None:
    for v in gen():
        print(v)


main()
