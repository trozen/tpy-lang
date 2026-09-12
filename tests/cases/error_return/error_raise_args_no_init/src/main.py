# raise E(args) without __init__ is an error when E has data fields
from tpy import int32, error_return, ReturnException

class MyError(Exception, ReturnException):
    code: int32
    message: str

@error_return(MyError)
def foo() -> int32:
    raise MyError(1, "oops")  # tpyc: error(/has data fields but no __init__/)
