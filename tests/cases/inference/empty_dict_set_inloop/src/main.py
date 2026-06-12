# Legitimate dict/set locals pinned only by an in-loop mutation resolve at the
# post-loop return -- the dict/set siblings of empty_list_inloop_append.
from tpy import Int32, Own


def build_dict(xs: list[Int32]) -> Own[dict[Int32, Int32]]:
    d = {}  # tpyc: type(/dict\[Int32, Int32\]/)
    for x in xs:
        d[x] = x * x
    return d


def build_set(xs: list[Int32]) -> Own[set[Int32]]:
    s = set()  # tpyc: type(/set\[Int32\]/)
    for x in xs:
        s.add(x)
    return s


def main() -> None:
    print(len(build_dict([1, 2, 3])))
    print(len(build_set([1, 1, 2])))


main()
