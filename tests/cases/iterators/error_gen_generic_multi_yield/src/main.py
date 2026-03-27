# Error: generic generators with multiple yield points are not yet supported
from typing import Iterator

def two_yields[T](a: T, b: T) -> Iterator[T]:  # tpyc: error(/generic generator functions with multiple yield points/)
    yield a
    yield b

def main() -> None:
    for x in two_yields(1, 2):
        print(x)

main()
