# A list[Counter] @export param whose exposed-class ELEMENT is mutated (a method
# call in a loop) is copied in, so the element mutation is not visible to the
# caller -- the container copy cliff, extended to exposed-class elements. The
# existing mutated-param tracking already traces the loop variable back to the
# param, so the same warning fires. A read-only element access stays quiet.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Counter:
    def __init__(self, v: Int64):
        self.value = v

    def bump(self) -> None:
        self.value += 1


@export
def bump_all(cs: list[Counter]) -> None:  # tpyc: warning(/list parameter 'cs' is copied in.*not visible to the caller/)
    for c in cs:
        c.bump()


@export
def total(cs: list[Counter]) -> Int64:  # tpyc: ok
    t: Int64 = 0
    for c in cs:
        t += c.value
    return t
