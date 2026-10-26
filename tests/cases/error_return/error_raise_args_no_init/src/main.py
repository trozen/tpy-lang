# A return exception without __init__ takes no constructor arguments
# (ERROR_RETURN_DESIGN.md "Exception Type"); CPython accepts `MyError(1, "oops")`.
from tpy import int32, error_return, ReturnException

class MyError(Exception, ReturnException):
    code: int32
    message: str

@error_return(MyError)
def foo() -> int32:
    raise MyError(1, "oops")  # tpyc: error(/'MyError' has no __init__; use @dataclass or define __init__/)
