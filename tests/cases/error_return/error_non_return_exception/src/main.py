# @error_return(E) requires E to be a ReturnException type
from tpy import int32, error_return

class MyError(Exception):
    pass

@error_return(MyError)
def find(items: list[int32], target: int32) -> int32:  # tpyc: error(/not a ReturnException type/)
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise MyError
