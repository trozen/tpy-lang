# H1: Optional-subject `match` -- a capture binding from the non-None arm is
# read across a yield (the captured inner value is a frame field).
from typing import Iterator, Optional


def gen(x: Optional[int]) -> Iterator[int]:
    match x:
        case None:
            yield -1
        case v:
            yield v
            yield v * 2


def main() -> None:
    for y in gen(None):
        print(y)
    print("--")
    for y in gen(4):
        print(y)


main()
