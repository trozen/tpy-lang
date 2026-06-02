# Regression: isinstance narrowing established by a `while` condition and by
# `assert` is re-established across a suspension inside the narrowed region
# (arm-specific access after the yield, in a generator/resumable frame).
from typing import Iterator


def loop(a: int | str) -> Iterator[str]:
    count = 0
    while isinstance(a, int):
        yield "tick"
        yield str(a + 1)
        count += 1
        if count >= 3:
            break


def checked(a: int | str) -> Iterator[str]:
    assert isinstance(a, int)
    yield "checked"
    yield str(a + 100)


def main() -> None:
    for s in loop(5):
        print(s)
    for s in checked(7):
        print(s)


main()
