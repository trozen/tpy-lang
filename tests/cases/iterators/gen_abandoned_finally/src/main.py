# Abandoning a suspended generator runs its pending finally via the frame
# destructor, at loop exit for a temporary source (CPython: GeneratorExit
# on refcount drop) and at scope end for a named source.
from typing import Iterator


def gen(tag: str) -> Iterator[int]:
    try:
        yield 1
        yield 2
    finally:
        print("cleanup", tag)


def temp_source() -> None:
    for x in gen("temp"):
        print(x)
        break
    print("after temp")


def named_source() -> None:
    g = gen("named")
    for x in g:
        print(x)
        break
    print("after named")


def exhausted() -> None:
    for x in gen("full"):
        print(x)
    print("after full")


def main() -> None:
    temp_source()
    named_source()
    exhausted()


main()
