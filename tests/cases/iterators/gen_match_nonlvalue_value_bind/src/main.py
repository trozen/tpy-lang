# H1 guard: binding a VALUE field (owned `str`) from a NON-lvalue match
# subject (a constructed temporary) is safe across a suspension -- the value
# is copied into the frame field, not aliased -- so it compiles and runs
# (unlike the rejected pointer-repr case).
from typing import Iterator


class Box:
    label: str

    def __init__(self, s: str) -> None:
        self.label = s


def gen() -> Iterator[int]:
    match Box("hello-world"):
        case Box(label=v):
            yield 1
            print(v)
            yield 2


def main() -> None:
    for x in gen():
        print(x)


main()
