# Source module for import_inferred_global: every module-level binding here
# is UNANNOTATED, so its type is inferred from the initializer.
from tpy import int32


class Counter:
    n: int32

    def __init__(self, n: int32):
        self.n = n


WIDTH = 800
label = "wide"
sizes = [1, 2, 3]
shared = Counter(1)
step = 1
# module-level rebind of an inferred global: importers see the final value
step = step + 4
tally = 0


def bump() -> None:
    # `global` rebind of an inferred global, plus a mutation through an
    # inferred record global -- both must still resolve.
    global tally
    tally += 1
    shared.n += 1


def get_shared() -> Counter:
    # borrow return of an inferred module global: durable storage, so no
    # "local or temporary" rejection
    return shared  # tpyc: ok
