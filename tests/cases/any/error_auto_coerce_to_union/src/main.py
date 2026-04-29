# Auto-coerce to a union target is rejected -- ambiguous which alternative
# to extract. User must narrow first.

from typing import Any


def main() -> None:
    a: Any = 1
    x: int | str = a  # tpyc: error(/cannot auto-coerce Any to/)
    print(x)


main()
