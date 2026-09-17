# A `for` body's binding counts as assigned after the loop, but only as far as
# the block it sits in: nested in a `while` whose entry condition sema cannot
# prove true, the whole binding may never run, so the read after the `while` is
# rejected (LANGUAGE_FEATURES: "after a `while` only when the entry condition is
# provably true ... otherwise the read is rejected as possibly unassigned").
from tpy import int32


class Flat:
    def __init__(self, n: int32) -> None:
        self.n = n


def outer(cond: bool) -> None:
    while cond:
        for i in range(2):
            f = Flat(i)
        break
    print("outer", f.n)  # tpyc: error(/may not be assigned at this point/)


outer(True)
