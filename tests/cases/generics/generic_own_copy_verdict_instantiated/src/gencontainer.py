# A generic body in a SECOND module: it states its contract in its OWN
# diag.txt, with no call site in this file at all.
from tpy import int32


def cross_slot[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)
