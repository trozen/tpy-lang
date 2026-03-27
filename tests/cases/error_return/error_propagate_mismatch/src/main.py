# Different error types: auto-propagation requires matching types
from tpy import Int32, error_return, ControlFlow

class ErrorA(Exception, ControlFlow):
    pass

class ErrorB(Exception, ControlFlow):
    pass

@error_return(ErrorA)
def may_fail_a() -> Int32:
    raise ErrorA

@error_return(ErrorB)
def wrapper() -> Int32:
    x = may_fail_a()  # tpyc: error(/must be handled/)
    return x
