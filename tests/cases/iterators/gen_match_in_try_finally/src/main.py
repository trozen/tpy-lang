# H1: a suspending `match` nested inside a try/finally -- the arm-body BBs
# inherit the enclosing try region, so the finally runs on every exit path.
from typing import Iterator


def gen(n: int) -> Iterator[int]:
    try:
        match n:
            case 0:
                yield 10
            case _:
                yield 20
                yield 21
    finally:
        print("cleanup")
    yield 99


def main() -> None:
    for v in gen(0):
        print(v)
    print("--")
    for v in gen(5):
        print(v)


main()
