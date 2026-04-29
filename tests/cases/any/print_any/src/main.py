# print(x) on a raw Any dispatches through the per-type str slot and
# produces the Python str() representation of the contained value.

from typing import Any


def main() -> None:
    a: Any = 42
    b: Any = "hello"
    c: Any = 3.14
    d: Any = None
    e: Any = True
    print(a)
    print(b)
    print(c)
    print(d)
    print(e)


main()
