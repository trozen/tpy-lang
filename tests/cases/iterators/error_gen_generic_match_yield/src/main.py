# Error: a generic generator with a `match`+`yield` body gets the same clean
# sema reject as a non-generic one (the suspension-shape check runs before
# the resumable CFG build, independent of type params). Guards against a
# regression where a generic generator would instead reach a C++ template
# link failure.
from typing import Iterator


def gen[T](a: T, b: T, tag: int) -> Iterator[T]:
    match tag:  # tpyc: error(/inside a .match. statement is not yet supported/)
        case 0:
            yield a
            yield b
        case _:
            yield a


def main() -> None:
    for v in gen(1, 2, 0):
        print(v)


main()
