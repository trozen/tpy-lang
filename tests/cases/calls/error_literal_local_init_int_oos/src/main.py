# Initial binding of an int-Literal-annotated local rejects an out-of-set literal RHS.
from typing import Literal


def main() -> None:
    n: Literal[1, 2] = 5  # tpyc: error(/Literal\[1, 2\].*5/)
    print(n)


main()
