# Subscripting an isinstance-narrowed dict routes the checked ::tpy::__getitem__:
# a missing key raises KeyError (not a silent default-insert), and a present-key
# read aliases the element (mutating it through the borrow is observed via the
# dict), matching CPython.
from tpy import Own


class Cell:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


def get_dict() -> Own[dict[str, Cell] | int]:
    d: dict[str, Cell] = {}
    d["a"] = Cell(1)
    return d


def main() -> None:
    v = get_dict()
    if isinstance(v, dict):
        try:
            print(v["missing"].n)  # tpyc: ok
        except KeyError:
            print("KeyError caught")
        inner = v["a"]
        inner.n = 99               # mutate through the borrowed element
        print(v["a"].n)            # 99 -- proves the read aliases, not a copy


main()
