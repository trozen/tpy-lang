# Cleanup raising inside the frame destructor cannot propagate (noexcept):
# TPy panics. CPython prints "Exception ignored in: <generator...>" and
# continues -- acknowledged divergence (fail-fast), hence no CPython phase.
from typing import Iterator


def gen() -> Iterator[int]:
    try:
        yield 1
        yield 2
    finally:
        raise ValueError("cleanup boom")


def main() -> None:
    for x in gen():
        print(x)
        break
    print("after")


main()
