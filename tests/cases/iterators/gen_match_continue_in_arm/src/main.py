# H1: `continue` in a match arm inside a generator loop targets the loop (a
# CFG state transition, not a C++ continue in the dispatch switch).
from typing import Iterator


def gen(items: list[int]) -> Iterator[int]:
    for it in items:
        match it:
            case 0:
                continue
            case v:
                yield v
                yield v * 10


def main() -> None:
    for y in gen([1, 0, 2]):
        print(y)


main()
