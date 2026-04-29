# typing.cast(str, any_var) extracts the contained string. The Any
# stores std::string (owning); the cast returns it.

from typing import Any, cast


def main() -> None:
    x: Any = "hello"
    s = cast(str, x)
    print(s.upper())


main()
