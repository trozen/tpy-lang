# == None on nullable union should error with hint to use 'is None'
from tpy import Int32

def check(v: Int32 | str | None) -> bool:
    return v == None  # tpyc: error(/Use 'is None'/)
