# An `&&`/`||` chain at EXPRESSION position over Literal stub facts folds whole:
# the {r,w} stub renders `true`, the {x,y} stub `false`.
from typing import Literal, overload


@overload
def isrw(m: Literal["r", "w"]) -> bool: ...


@overload
def isrw(m: Literal["x", "y"]) -> bool: ...


def isrw(m: str) -> bool:
    return m == "r" or m == "w"  # folded per stub


def main() -> None:
    print(isrw("r"), isrw("x"))


main()
