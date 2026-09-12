# None == v on nullable union should error (symmetric form of v == None)
from tpy import int32

def eq_left(v: int32 | str | None) -> bool:
    return None == v  # tpyc: error(/Use 'is None'/)
