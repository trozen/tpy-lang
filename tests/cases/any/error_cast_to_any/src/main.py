# typing.cast(Any, x) is meaningless -- if you wanted Any, just use the
# variable directly. Reject at compile time.

from typing import Any, cast


def main() -> None:
    x: Any = 1
    y = cast(Any, x)  # tpyc: error(/cast.Any,.+is meaningless/)
    print(y)


main()
