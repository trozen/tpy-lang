# None != v on nullable union should error (symmetric form of v != None)
from tpy import Int32

def check(v: Int32 | str | None) -> bool:
    return None != v  # tpyc: error(/Use 'is None'/)
