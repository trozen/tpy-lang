# An UN-narrowed value-repr `Optional[value tuple]` name as a tuple-unpack source
# (no `is not None` guard): the whole-optional read is not a tuple, so it rejects.
from tpy import int32


def f(r: tuple[float, int32] | None) -> None:
    a, b = r  # tpyc: error(/Cannot unpack non-tuple type/)
    print(a, b)


f((1.5, 3))
