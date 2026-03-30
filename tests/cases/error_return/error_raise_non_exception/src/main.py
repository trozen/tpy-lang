# Error: raise a class that doesn't inherit from Exception
from tpy import Int32

class NotFound:
    pass

def find_index(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound  # tpyc: error(/not an exception type/)
