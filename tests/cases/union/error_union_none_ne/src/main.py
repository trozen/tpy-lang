# != None on nullable union should error with hint to use 'is not None'
from tpy import int32

def check(v: int32 | str | None) -> bool:
    return v != None  # tpyc: error(/Use 'is None'/)
