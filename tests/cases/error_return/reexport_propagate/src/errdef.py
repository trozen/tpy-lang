# Defining module: the real AppError lives here.
from tpy import ReturnException, error_return, Int32

class AppError(Exception, ReturnException):
    pass

@error_return(AppError)
def check(n: Int32) -> Int32:
    if n < 0:
        raise AppError
    return n
