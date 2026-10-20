# `del t[k]` receives its key as the explicit t.__delitem__(k) call does
# (docs/OVERLOAD_DESIGN.md "Subscripts"): a float key at an int64 parameter is
# the call's type mismatch, not a truncation.
from tpy import int64


class S:
    def __init__(self) -> None:
        pass

    def __delitem__(self, x: int64) -> None:
        print("del", x)


def main() -> None:
    s = S()
    del s[2.5]  # tpyc: error(/Type mismatch in argument 'x': expected int64, got float/)


main()
