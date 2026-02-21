# None == v on nullable union should error (symmetric form of v == None)
from tpy import Int32

def eq_left(v: Int32 | str | None) -> bool:
    return None == v  # tpyc: error(/Use 'is None'/)
