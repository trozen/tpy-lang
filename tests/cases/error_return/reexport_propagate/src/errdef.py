# Defining module: the real AppError lives here.
from tpy import ReturnException, error_return, int32

class AppError(Exception, ReturnException):
    pass

@error_return(AppError)
def check(n: int32) -> int32:
    if n < 0:
        raise AppError
    return n
