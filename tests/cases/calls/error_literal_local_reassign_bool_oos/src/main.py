# Reassignment to a bool-Literal-annotated local rejects an out-of-set literal RHS.
from typing import Literal


def main() -> None:
    b: Literal[True] = True
    b = False  # tpyc: error(/Literal\[True\].*False/)
    print(b)


main()
