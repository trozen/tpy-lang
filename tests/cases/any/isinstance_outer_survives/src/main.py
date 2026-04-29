# The non-consuming guarantee: the outer Any remains alive after the
# narrowed branch and can be used again.

from typing import Any


def main() -> None:
    x: Any = "hello"
    if isinstance(x, str):
        print(x)             # narrowed view
    print(x)                 # outer Any still alive
    if isinstance(x, str):
        print(x.upper())     # narrowed again, fresh borrow
    print(x)                 # outer survives twice


main()
