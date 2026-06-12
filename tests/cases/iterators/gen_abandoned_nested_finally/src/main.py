# Abandoning a generator suspended inside NESTED try/finally regions runs
# BOTH pending finally bodies, innermost first (destructor multi-helper
# chain ordering).
from typing import Iterator


def gen() -> Iterator[int]:
    try:
        try:
            yield 1
            yield 2
        finally:
            print("inner cleanup")
    finally:
        print("outer cleanup")


def main() -> None:
    for x in gen():
        print(x)
        break
    print("after")


main()
