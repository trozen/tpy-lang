# Generic generator functions: type-parameterized generators over Iterable[T]
from tpy import Int32
from typing import Iterable, Iterator

def repeat[T](value: T, count: Int32) -> Iterator[T]:
    i: Int32 = 0
    while i < count:
        yield value
        i += 1

def enumerate[T](iterable: Iterable[T]) -> Iterator[tuple[Int32, T]]:
    i: Int32 = 0
    for item in iterable:
        yield (i, item)
        i += 1

def main() -> None:
    # Generic while-generator with value type
    for x in repeat(42, 3):
        print(x)

    # Generic while-generator with str
    for s in repeat("hi", 2):
        print(s)

    # Generic for-generator (enumerate) over list[str]
    words = ["hello", "world", "foo"]
    for i, w in enumerate(words):
        print(i, w)

    # enumerate over list[Int32]
    nums = [10, 20, 30]
    for i, n in enumerate(nums):
        print(i, n)

    # compose: enumerate directly over repeat (generator over generator)
    for i, s in enumerate(repeat("x", 3)):
        print(i, s)

main()
