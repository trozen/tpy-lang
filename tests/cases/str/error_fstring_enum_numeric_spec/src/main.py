# Pins the restriction in docs/LANGUAGE_FEATURES.md "F-string Formatting"
# (format specs): a plain Enum formats its str() text, so a number's spec
# (`d` here) is a compile error; CPython raises ValueError at run time.
from enum import Enum


class Color(Enum):
    RED = 1


def show(c: Color) -> str:
    return f"[{c:d}]"  # tpyc: error(/Format spec ':d' cannot apply to text/)


def main() -> None:
    print(show(Color.RED))


main()
