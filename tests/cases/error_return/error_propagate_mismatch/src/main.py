# Different error types: auto-propagation requires matching types
from tpy import int32, error_return, ReturnException

class ErrorA(Exception, ReturnException):
    pass

class ErrorB(Exception, ReturnException):
    pass

@error_return(ErrorA)
def may_fail_a() -> int32:
    raise ErrorA

@error_return(ErrorB)
def wrapper() -> int32:
    x = may_fail_a()  # tpyc: error(/must be handled/)
    return x
