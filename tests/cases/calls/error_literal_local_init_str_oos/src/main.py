# Initial binding of a str-Literal-annotated local rejects an out-of-set literal RHS.
from typing import Literal


def main() -> None:
    s: Literal["r", "w"] = "wb"  # tpyc: error(/Literal\["r", "w"\].*"wb"/)
    print(s)


main()
