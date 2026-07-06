# A numeric Span crosses the boundary as a PARAM only (buffer-protocol
# copy-in has no return direction); a Span method RETURN is rejected with a
# located error, mirroring the free-function rule.
# tpy: ext_module
from tpy import Int64, Span
from tpy.extern import export


@export
class Viewer:
    n: Int64

    def __init__(self, n: Int64):
        self.n = n

    def view(self, xs: Span[Int64]) -> Span[Int64]:  # tpyc: error(/method 'view' return of type.*cannot cross the CPython boundary/)
        return xs
