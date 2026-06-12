# An empty container returned from a function resolves its element types from
# the declared return type alone (no population) -- list/dict/set parity.
from tpy import Int32, Own


def empty_list() -> Own[list[Int32]]:
    out = []  # tpyc: type(/list\[Int32\]/)
    return out


def empty_dict() -> Own[dict[Int32, Int32]]:
    d = {}  # tpyc: type(/dict\[Int32, Int32\]/)
    return d


def empty_set() -> Own[set[Int32]]:
    s = set()  # tpyc: type(/set\[Int32\]/)
    return s


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def empty_str_set() -> Own[set[str]]:
    s = set()  # tpyc: type(/set\[str\]/)
    return s


def empty_record_dict() -> Own[dict[Int32, Point]]:
    d = {}  # tpyc: type(/dict\[Int32, Point\]/)
    return d


def main() -> None:
    print(len(empty_list()))
    print(len(empty_dict()))
    print(len(empty_set()))
    print(len(empty_str_set()))
    print(len(empty_record_dict()))


main()
