# Own[Any] is redundant -- Any is already an owning value cell.

from typing import Any
from tpy import Own


def main() -> None:
    x: Own[Any] = 1  # tpyc: error(/Own.Any. is redundant/)
    print(x)


main()
