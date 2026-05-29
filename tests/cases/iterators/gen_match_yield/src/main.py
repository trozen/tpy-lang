# H1: a `yield` inside a `match` lowers on the resumable frame -- `match` is
# decomposed by the CFG builder (arm bodies become states; the type-aware
# dispatch is reused unchanged). Each arm may suspend.
from typing import Iterator


def gen(n: int) -> Iterator[int]:
    match n:  # tpyc: ok
        case 0:
            yield 10
            yield 20
        case _:
            yield 30


def main() -> None:
    for v in gen(0):
        print(v)
    print("--")
    for v in gen(7):
        print(v)


main()
