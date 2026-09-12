# An empty list pinned only by an in-loop .append() whose element contradicts
# the declared return type is rejected with a clean element-mismatch error.
from tpy import int32, Own


def bad_list(xs: list[int32]) -> Own[list[str]]:
    out = []
    for x in xs:
        out.append(x)
    return out  # tpyc: error(/expected str, got int32/)
