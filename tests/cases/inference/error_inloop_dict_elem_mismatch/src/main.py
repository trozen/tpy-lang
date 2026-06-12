# An empty dict pinned only by in-loop subscript assignment whose value
# contradicts the declared return type is rejected with a clean mismatch error.
from tpy import Int32, Own


def bad_dict(xs: list[Int32]) -> Own[dict[Int32, str]]:
    d = {}
    for x in xs:
        d[x] = x
    return d  # tpyc: error(/expected dict\[Int32, str\], got dict\[Int32, Int32\]/)
