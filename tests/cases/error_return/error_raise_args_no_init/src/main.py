# raise E(args) without __init__ is an error when E has data fields
from tpy import Int32, error_return, ControlFlow

class MyError(Exception, ControlFlow):
    code: Int32
    message: str

@error_return(MyError)
def foo() -> Int32:
    raise MyError(1, "oops")  # tpyc: error(/has data fields but no __init__/)
