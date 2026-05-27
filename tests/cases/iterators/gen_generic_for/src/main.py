# Generic generator iterating a list[T] with multiple yields per element --
# exercises the resumable for-loop frame field with a generic element type
# (the element is a real type param T, unlike a protocol-typed iterable).
from typing import Iterator


def doubled[T](xs: list[T]) -> Iterator[T]:  # tpyc: ok
    for x in xs:
        yield x
        yield x


def main() -> None:
    for v in doubled([1, 2, 3]):
        print(v)


main()
