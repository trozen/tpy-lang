# A list/dict/set @export param crosses the boundary copy-in, so mutating it
# (append / setitem) is not visible to the Python caller -- warn precisely where
# sema proves the mutation happens. A read-only container param has no
# observable divergence and stays quiet.
# tpy: ext_module
from tpy.extern import export


@export
def push(xs: list[int], v: int) -> None:  # tpyc: warning(/list parameter 'xs' is copied in.*not visible to the caller/)
    xs.append(v)


@export
def fill(d: dict[str, int]) -> None:  # tpyc: warning(/dict parameter 'd' is copied in.*not visible to the caller/)
    d["k"] = 1


@export
def add_one(s: set[int], v: int) -> None:  # tpyc: warning(/set parameter 's' is copied in.*not visible to the caller/)
    s.add(v)


@export
def read_only(s: set[int]) -> int:  # tpyc: ok
    return len(s)
