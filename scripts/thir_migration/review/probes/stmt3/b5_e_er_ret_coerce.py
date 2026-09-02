from tpy import Int32, error_return, ReturnException
class NotFound(Exception, ReturnException):
    pass
from tpy import Int64
@error_return(NotFound)
def inner(n: Int32) -> Int32:
    if n < 0:
        raise NotFound
    return n
@error_return(NotFound)
def outer(n: Int32) -> Int64:
    return inner(n)
def main() -> None:
    try:
        print(outer(1))
    except NotFound:
        print('e')
main()
