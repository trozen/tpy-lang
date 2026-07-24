# Subscripting an isinstance-narrowed list routes the checked ::tpy::__getitem__:
# an out-of-range index raises IndexError (not raw operator[] UB), and an
# in-range read returns the element -- matching CPython.
from tpy import Own


def get_list() -> Own[list[int] | int]:
    xs: list[int] = [10, 20]
    return xs


def main() -> None:
    w = get_list()
    if isinstance(w, list):
        try:
            print(w[99])  # tpyc: ok
        except IndexError:
            print("IndexError caught")
        print(w[-1])      # -1 -> last element (20), proves negative-index normalization


main()
