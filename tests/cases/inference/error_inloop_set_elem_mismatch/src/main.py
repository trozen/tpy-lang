# An empty set pinned only by an in-loop .add() whose element contradicts the
# declared return type is rejected with a clean element-mismatch error.
from tpy import int32, Own


def bad_set(xs: list[int32]) -> Own[set[str]]:
    s = set()
    for x in xs:
        s.add(x)
    return s  # tpyc: error(/expected set\[str\], got set\[int32\]/)
