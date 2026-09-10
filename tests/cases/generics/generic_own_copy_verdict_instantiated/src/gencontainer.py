# A generic body in a SECOND module: it states its contract in its OWN
# diag.txt, with no call site in this file at all.
from tpy import Int32


def cross_slot[T](v: T) -> Int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)
