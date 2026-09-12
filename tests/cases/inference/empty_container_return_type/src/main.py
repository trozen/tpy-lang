# An empty container returned from a function resolves its element types from
# the declared return type alone (no population) -- list/dict/set parity.
from tpy import int32, Own


def empty_list() -> Own[list[int32]]:
    out = []  # tpyc: type(/list\[int32\]/)
    return out


def empty_dict() -> Own[dict[int32, int32]]:
    d = {}  # tpyc: type(/dict\[int32, int32\]/)
    return d


def empty_set() -> Own[set[int32]]:
    s = set()  # tpyc: type(/set\[int32\]/)
    return s


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def empty_str_set() -> Own[set[str]]:
    s = set()  # tpyc: type(/set\[str\]/)
    return s


def empty_record_dict() -> Own[dict[int32, Point]]:
    d = {}  # tpyc: type(/dict\[int32, Point\]/)
    return d


def main() -> None:
    print(len(empty_list()))
    print(len(empty_dict()))
    print(len(empty_set()))
    print(len(empty_str_set()))
    print(len(empty_record_dict()))


main()
