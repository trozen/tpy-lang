from tpy import int32, error_return, ReturnException
class NotFound(Exception, ReturnException):
    pass
from tpy import int64
@error_return(NotFound)
def inner(n: int32) -> int32:
    if n < 0:
        raise NotFound
    return n
@error_return(NotFound)
def outer(n: int32) -> int64:
    return inner(n)
def main() -> None:
    try:
        print(outer(1))
    except NotFound:
        print('e')
main()
