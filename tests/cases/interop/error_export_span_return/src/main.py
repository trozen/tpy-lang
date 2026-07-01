# Span[T] numeric crosses the @export boundary only as a function PARAM (the
# buffer-protocol copy-in); a function hands back numeric data via list[T]
# instead -- returning a Span is rejected, not silently mishandled.
# tpy: ext_module
from tpy import Span, readonly, Int32
from tpy.extern import export


@export
def f(xs: list[Int32]) -> Span[readonly[Int32]]:  # tpyc: error(/return type.*not yet marshallable/)
    return Span[readonly[Int32]](xs)
