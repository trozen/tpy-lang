# Regression: a `global` mutated inside a generator must reach the module
# slot, not a frame-field copy, so the driver observes the write per resume.
from typing import Iterator

seen: int = 0


def counter() -> Iterator[int]:
    global seen
    for i in range(3):
        seen = i
        yield i


def main() -> None:
    for x in counter():
        print("x =", x, "seen =", seen)


main()
