# Abandoning a generator suspended inside a `with` region runs the
# manager's __exit__ via the frame destructor, with GeneratorExit as
# exc_val (CPython close contract: __exit__ observes an exceptional exit).
from typing import Iterator


class CM:
    def __enter__(self) -> None:
        print("enter")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit exceptional" if exc_val is not None else "exit normal")


def gen() -> Iterator[int]:
    cm = CM()
    with cm:
        yield 1
        yield 2


def main() -> None:
    for x in gen():
        print(x)
        break
    print("after")


main()
