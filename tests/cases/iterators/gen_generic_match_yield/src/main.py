# H1: a generic generator with a `match`+`yield` body lowers on the resumable
# frame (match dispatch is type-agnostic on the `int` tag; the template
# struct + inline factory shape is unchanged).
from typing import Iterator


def gen[T](a: T, b: T, tag: int) -> Iterator[T]:
    match tag:  # tpyc: ok
        case 0:
            yield a
            yield b
        case _:
            yield a


def main() -> None:
    for v in gen(1, 2, 0):
        print(v)
    print("--")
    for v in gen(8, 9, 1):
        print(v)


main()
