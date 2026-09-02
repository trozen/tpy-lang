from tpy import Int32, error_return, ReturnException
class NotFound(Exception, ReturnException):
    pass
@error_return(NotFound)
def inner(n: Int32) -> Int32:
    if n < 0:
        raise NotFound
    return n
@error_return(NotFound)
def outer(n: Int32) -> Int32 | None:
    return inner(n)
def main() -> None:
    try:
        r = outer(1)
        print(1 if r is None else 0)
    except NotFound:
        print('e')
main()
