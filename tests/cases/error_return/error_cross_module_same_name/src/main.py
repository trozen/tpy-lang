# `outer` declares its own main.AppError but propagates inner()'s exc_a.AppError
# -- a distinct type sharing the short name -- which must be rejected by qname.
from tpy import error_return, int32, ReturnException
from exc_a import inner

class AppError(Exception, ReturnException):
    pass

@error_return(AppError)
def outer() -> int32:
    return inner()  # tpyc: error(/call to 'inner' may return 'AppError'/)

def main() -> None:
    try:
        print(outer())
    except AppError:
        print("caught")

main()
