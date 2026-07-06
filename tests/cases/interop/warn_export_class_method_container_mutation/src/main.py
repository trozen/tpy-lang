# A container or Span param of an exposed-class method crosses the boundary
# copy-in (same as a free @export function), so a mutation the method makes to
# it is not visible to the CPython caller -- warn precisely where sema proves
# the mutation happens; a read-only param stays quiet.
# tpy: ext_module
from tpy import Int64, Span
from tpy.extern import export


@export
class Sink:
    n: Int64

    def __init__(self, n: Int64):
        self.n = n

    def push(self, xs: list[Int64]) -> None:  # tpyc: warning(/method 'push': list parameter 'xs' is copied in.*not visible to the caller/)
        xs.append(self.n)

    def fill(self, d: dict[str, Int64]) -> None:  # tpyc: warning(/method 'fill': dict parameter 'd' is copied in.*not visible to the caller/)
        d["k"] = self.n

    def toggle(self, s: set[Int64]) -> None:  # tpyc: warning(/method 'toggle': set parameter 's' is copied in.*not visible to the caller/)
        s.add(self.n)

    def scale(self, xs: Span[Int64]) -> None:  # tpyc: warning(/method 'scale': Span parameter 'xs' is copied in.*not visible to the caller/)
        for i in range(len(xs)):
            xs[i] = xs[i] * 2

    def read_only(self, xs: list[Int64]) -> Int64:  # tpyc: ok
        s: Int64 = 0
        for x in xs:
            s += x
        return s
