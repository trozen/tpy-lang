# Module A's own AppError -- a distinct type from main's same-named AppError.
from tpy import ReturnException, error_return, int32

class AppError(Exception, ReturnException):
    pass

@error_return(AppError)
def inner() -> int32:
    raise AppError
