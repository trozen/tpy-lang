# An empty dict pinned only by in-loop subscript assignment whose value
# contradicts the declared return type is rejected with a clean mismatch error.
from tpy import int32, Own


def bad_dict(xs: list[int32]) -> Own[dict[int32, str]]:
    d = {}
    for x in xs:
        d[x] = x
    return d  # tpyc: error(/expected dict\[int32, str\], got dict\[int32, int32\]/)
