# A `while` whose condition narrows a Literal param: sema seeds the literal fact
# into the body, where it FOLDS a repeat of the same compare to a constant. The
# fold lives on the compare, and the body walk here does not carry it, so the
# loop would re-test what the fact already decided.
from typing import Literal
from tpy import int32


def go(mode: Literal["r", "w"]) -> int32:
    n = 0
    while mode == "r":  # tpyc: error(/not yet supported.*while.literal_fact/)
        n += 1
        if n > 2:
            break
    return n


def main() -> None:
    print(go("r"))


main()
