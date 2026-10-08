# Valid Python refused by design (docs/LANGUAGE_FEATURES.md "Iteration"): a
# `match` on the loop variable decides the list, so a later wider store fails.
from tpy import int64


def a64() -> int64:
    return 1099511627776


def main() -> None:
    xs = [1, 2]
    for x in xs:
        match x:
            case 1:
                print("one")
            case _:
                print("other")
    # The match subject fixed the elements at int32.
    xs.append(a64())  # tpyc: error(/'xs' holds int32 elements since line 13 \(through 'x', a match subject\), and this value is int64; annotate its first binding: xs: list\[int64\]/)


main()
