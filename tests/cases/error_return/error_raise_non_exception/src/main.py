# Error: raise a class that doesn't inherit from Exception
from tpy import int32

class NotFound:
    pass

def find_index(items: list[int32], target: int32) -> int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound  # tpyc: error(/not an exception type/)
