# Generator sibling of tuple-unpack-across-await: value-type unpacked locals
# held across a yield must be frame fields (else they read stale after resume).
from typing import Iterator


def make_pair() -> tuple[int, int]:
    return (10, 20)


def g() -> Iterator[int]:
    a, b = make_pair()
    yield a
    yield b
    yield a + b


def main() -> None:
    for v in g():
        print(v)


main()
