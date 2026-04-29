# String narrowing extracts std::string (Any always stores str as owned).

from typing import Any


def main() -> None:
    x: Any = "hello"
    if isinstance(x, str):
        print(x.upper())


main()
