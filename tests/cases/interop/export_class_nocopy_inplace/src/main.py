# INVERSE pin for the @nocopy dunder borrow-return reject: an in-place
# dunder's required `-> Cls` self-return is identity-preserving by
# construction (the nb_inplace wrapper hands back the same self PyObject,
# never a copy), so @nocopy stays admitted there and the emitted glue
# builds cleanly.
# tpy: ext_module
from tpy import int32, nocopy
from tpy.extern import export


@export
@nocopy
class Acc:
    total: int32

    def __init__(self):
        self.total = 0

    def __iadd__(self, n: int32) -> "Acc":  # tpyc: ok
        self.total += n
        return self
