# Wrong number of arguments to raise
from tpy import Int32, error_return, ReturnException

class MyError(Exception, ReturnException):
    code: Int32
    message: str

    def __init__(self, code: Int32, message: str) -> None:
        self.code = code
        self.message = message

@error_return(MyError)
def foo() -> Int32:
    raise MyError(1)  # tpyc: error(/expects 2 arguments, got 1/)
