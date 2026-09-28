# Wrong number of arguments to raise
from tpy import int32, error_return, ReturnException

class MyError(Exception, ReturnException):
    code: int32
    message: str

    def __init__(self, code: int32, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

@error_return(MyError)
def foo() -> int32:
    raise MyError(1)  # tpyc: error(/expects 2 arguments, got 1/)
