# String's methods take str's parameter names and markers: find's `sub` is
# positional-only, as CPython's str.find refuses `sub=` (TypeError:
# str.find() takes no keyword arguments).
from tpy import String


def main() -> None:
    t = String("ab")
    print(t.find(sub="b"))  # tpyc: error(/'find' parameter 'sub' is positional-only/)


main()
