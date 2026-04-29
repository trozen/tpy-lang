# Any() and Any(a, b) are rejected -- the constructor sugar takes
# exactly one argument.

from typing import Any


def main() -> None:
    x = Any()  # tpyc: error(/Any.+takes exactly 1 argument/)
    print(x)


main()
