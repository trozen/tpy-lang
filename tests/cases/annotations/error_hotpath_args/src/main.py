# @hotpath takes no arguments -- the decorator stub's signature drives the check.
from tpy import hotpath


@hotpath("fast")  # tpyc: error(/@hotpath does not take arguments/)
def scale(x: int) -> int:
    return x * 2


print(scale(3))
