# Initial binding of a bool-Literal-annotated local rejects an out-of-set literal RHS.
from typing import Literal


def main() -> None:
    b: Literal[True] = False  # tpyc: error(/Literal\[True\].*False/)
    print(b)


main()
