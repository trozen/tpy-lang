# @error_return(E) requires E to be a ReturnException type
from tpy import Int32, error_return

class MyError(Exception):
    pass

@error_return(MyError)
def find(items: list[Int32], target: Int32) -> Int32:  # tpyc: error(/not a ReturnException type/)
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise MyError
