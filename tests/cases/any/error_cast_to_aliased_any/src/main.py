# cast(Any, x) is rejected even when Any is imported under an alias --
# the rejection runs after type resolution, not on the raw arg name.

from typing import Any as A, cast


def main() -> None:
    x: A = 1
    y = cast(A, x)  # tpyc: error(/cast.Any,.+is meaningless/)
    print(y)


main()
