# != None on nullable union should error with hint to use 'is not None'
from tpy import Int32

def check(v: Int32 | str | None) -> bool:
    return v != None  # tpyc: error(/Use 'is None'/)
