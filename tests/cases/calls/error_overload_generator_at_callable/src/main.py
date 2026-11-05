# An @overload generator passed to a callable slot is refused as a plain
# generator is: each stub's copy is a generator
# (BUGS.md#generator-at-callable-slot-unlowered).
from typing import Iterator, overload
from tpy import Fn, int32


@overload
def gen(n: int32) -> Iterator[int32]: ...
@overload
def gen(n: str) -> Iterator[int32]: ...
def gen(n: int32 | str) -> Iterator[int32]:
    yield 1


def use(f: Fn[[int32], Iterator[int32]]) -> int32:
    return sum(f(3))


def main() -> None:
    print(use(gen))  # tpyc: error(/generator/)


main()
