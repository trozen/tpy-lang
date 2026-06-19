# A plain local first-assigned inside an if/elif/else/match branch and read
# across a yield must survive the generator state-machine split (frame field).
from typing import Iterator


def gen_if(n: int) -> Iterator[int]:
    if n == 0:
        r = 100
    else:
        r = n + 1
    yield r
    yield r + 1


def gen_elif(n: int) -> Iterator[int]:
    if n == 0:
        r = 1
    elif n == 1:
        r = 2
    else:
        r = n + 10
    yield r
    yield r + 1


def gen_match(n: int) -> Iterator[int]:
    match n:  # tpyc: ok
        case 0:
            r = 100
        case _:
            r = n + 1
    yield r
    yield r + 1


def main() -> None:
    print(list(gen_if(5)))
    print(list(gen_if(0)))
    print(list(gen_elif(7)))
    print(list(gen_match(5)))


main()
