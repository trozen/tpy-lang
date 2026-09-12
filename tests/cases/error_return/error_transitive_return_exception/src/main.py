# ReturnException does not propagate through inheritance -- each type must declare it explicitly
from tpy import int32, error_return, ReturnException

class BaseError(Exception, ReturnException):
    pass

class SpecificError(BaseError):
    pass

@error_return(SpecificError)
def f() -> int32:  # tpyc: error(/not a ReturnException type/)
    raise SpecificError
