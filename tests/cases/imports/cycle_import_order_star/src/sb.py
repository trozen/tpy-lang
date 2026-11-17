# The star import from the suspended `sa` binds what `sa` bound so far,
# which is all it exports.
from sa import *  # tpyc: ok
from tpy import Own


def grow(e: Early) -> None:  # tpyc: ok
    e.n += 1


def fresh() -> Own[Early]:
    return Early(9)
