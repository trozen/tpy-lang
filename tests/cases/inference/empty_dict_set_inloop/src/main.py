# Legitimate dict/set locals pinned only by an in-loop mutation resolve at the
# post-loop return -- the dict/set siblings of empty_list_inloop_append.
from tpy import int32, Own


def build_dict(xs: list[int32]) -> Own[dict[int32, int32]]:
    d = {}  # tpyc: type(/dict\[int32, int32\]/)
    for x in xs:
        d[x] = x * x
    return d


def build_set(xs: list[int32]) -> Own[set[int32]]:
    s = set()  # tpyc: type(/set\[int32\]/)
    for x in xs:
        s.add(x)
    return s


def main() -> None:
    print(len(build_dict([1, 2, 3])))
    print(len(build_set([1, 1, 2])))


main()
