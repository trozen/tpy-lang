# Two nested suspending `finally` bodies; both run on the normal-exit
# path. Expected iteration: 1, 2, 3.
from typing import Iterator


def gen() -> Iterator[int]:
    try:
        try:
            yield 1
        finally:
            yield 2
    finally:
        yield 3


def main() -> None:
    for v in gen():
        print(v)


main()
