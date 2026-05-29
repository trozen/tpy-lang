# H1: `match` inside a for-loop -- a `break` in one arm targets the loop
# (becomes a CFG state transition, not a C++ break inside the dispatch
# switch), and a guarded arm yields. Exercises capture + guard + break.
from typing import Iterator


def gen(items: list[int]) -> Iterator[int]:
    for it in items:
        match it:
            case 0:
                break
            case v if v > 10:
                yield v
                yield v + 100
            case v:
                yield v
    yield -1


def main() -> None:
    for y in gen([3, 20, 0, 5]):
        print(y)


main()
