# Cross-module @error_return: function with error_return defined here, called from main
from tpy import int32, error_return, ReturnException

class NotFound(Exception, ReturnException):
    pass

@error_return(NotFound)
def find(items: list[int32], target: int32) -> int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound
