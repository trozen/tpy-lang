# Any | None is redundant -- Any already accepts None values directly.

from typing import Any


def main() -> None:
    x: Any | None = None  # tpyc: error(/Any . None is redundant/)
    print(x)


main()
