# Generator method on a generic class. Two instantiations exercise
# template monomorphization.
from typing import Iterator


class Box[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value

    def items(self) -> Iterator[T]:  # tpyc: ok
        yield self.value
        yield self.value


def main() -> None:
    b = Box(42)
    for v in b.items():
        print(v)
    s = Box("x")
    for t in s.items():
        print(t)


main()
