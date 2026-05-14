# `Literal[bool]`-annotated local rejects a non-Literal-typed bool RHS for the
# same reason as the str/int variants.
from typing import Literal


def get_flag() -> bool:
    return True


def main() -> None:
    b: Literal[True] = get_flag()  # tpyc: error(/Literal\[True\].*bool/)
    print(b)


main()
