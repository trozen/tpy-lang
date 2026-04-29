# Optional[Any] is redundant -- Any already accepts None.

from typing import Any, Optional


def main() -> None:
    x: Optional[Any] = None  # tpyc: error(/Optional.Any. is redundant/)
    print(x)


main()
