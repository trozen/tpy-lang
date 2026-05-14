# Augmented assignment on a `Literal[...]`-annotated local is rejected -- the
# result of `x += y` is rarely in the declared value set, and Literal[str]
# locals use std::string_view storage which can't hold a new owned string.
from typing import Literal


def main() -> None:
    m: Literal["r", "w"] = "r"
    m += "x"  # tpyc: error(/Augmented assignment is not supported for Literal/)
    print(m)


main()
