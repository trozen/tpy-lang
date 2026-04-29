# Any is the universal supertype -- combining it with another type is redundant.

from typing import Any


def main() -> None:
    x: Any | int = 1  # tpyc: error(/Any . T is redundant/)
    print(x)


main()
