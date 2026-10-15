# Outside the import cycle, importing only one member: its header also brings
# in the other member's, which the cycle member's `operator()` and overload
# candidates name by value.
from tpy import int32
import cyc_a


def use() -> str:
    o = cyc_a.Opt(3)
    first = o(2)  # tpyc: ok
    o.k = 4
    return f"{first} {o(2)} {o.f(1)}"  # tpyc: ok
