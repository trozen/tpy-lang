# A Span[T] @export param crosses the boundary copy-in, so mutating it
# (element assignment) is not visible to the Python caller -- warn precisely
# where sema proves the mutation happens, mirroring the list/dict/set
# container-mutation warning. Span[readonly[T]] can never warn here (writing
# through it is already a compile error elsewhere), and a non-mutating Span[T]
# param has no observable divergence and stays quiet.
# tpy: ext_module
from tpy import Span, readonly, int32
from tpy.extern import export


@export
def scale(xs: Span[int32], factor: int32) -> None:  # tpyc: warning(/Span parameter 'xs' is copied in.*not visible to the caller/)
    for i in range(len(xs)):
        xs[i] = xs[i] * factor


@export
def total(xs: Span[readonly[int32]]) -> int32:  # tpyc: ok
    s: int32 = 0
    for x in xs:
        s += x
    return s


@export
def peek(xs: Span[int32]) -> int32:  # tpyc: ok
    # Mutable-typed but never mutated: no observable divergence, stays quiet
    # (unlike `scale` above, which writes through xs and warns).
    s: int32 = 0
    for x in xs:
        s += x
    return s
